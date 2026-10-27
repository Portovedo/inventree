"""Landed cost service: compute, apply and revert landed cost for purchase orders."""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal

import structlog
from django.db import transaction
from django.utils import timezone

from . import allocation as alloc
from .config import Settings, load_settings

logger = structlog.get_logger('inventree')

ZERO = Decimal(0)


# --------------------------------------------------------------------------- helpers


def default_currency() -> str:
    from common.currency import currency_code_default

    return currency_code_default()


def convert(amount, from_currency: str, to_currency: str) -> Decimal | None:
    """Convert a Decimal amount between currencies (None if no rate available)."""
    if amount is None:
        return None
    amount = Decimal(amount)
    if not from_currency or not to_currency or from_currency == to_currency:
        return amount
    from djmoney.contrib.exchange.exceptions import MissingRate
    from djmoney.contrib.exchange.models import convert_money
    from djmoney.money import Money

    try:
        return Decimal(convert_money(Money(amount, from_currency), to_currency).amount)
    except MissingRate:
        logger.warning('landed_cost_capacity: missing rate %s -> %s', from_currency, to_currency)
        return None


def money_parts(money) -> tuple[Decimal | None, str]:
    if money is None:
        return None, ''
    return Decimal(money.amount), str(money.currency)


def fee_rules():
    from .models import FeeRule

    rules = []
    for rule in FeeRule.objects.filter(active=True).order_by('priority', 'pk'):
        try:
            rules.append((re.compile(rule.pattern, re.IGNORECASE), rule.category))
        except re.error:
            logger.warning('landed_cost_capacity: invalid fee rule pattern %r', rule.pattern)
    return rules


def detect_category(text: str, rules=None) -> str:
    for regex, category in rules if rules is not None else fee_rules():
        if regex.search(text or ''):
            return category
    return alloc.CATEGORY_OTHER


def d2(value) -> str | None:
    """Decimal -> string for JSON (None-safe)."""
    if value is None:
        return None
    text = format(Decimal(value).quantize(Decimal('0.000001')), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return '0' if text in ('', '-0') else text


# --------------------------------------------------------------------------- compute


def get_config(order, create=False):
    from .models import OrderConfig

    if create:
        return OrderConfig.objects.get_or_create(order=order)[0]
    return OrderConfig.objects.filter(order=order).first()


def compute(order, settings: Settings | None = None) -> dict:
    """Compute landed cost for a purchase order (does not modify anything)."""
    from .models import ExtraLineConfig

    settings = settings or load_settings()
    cfg = get_config(order)
    method = (cfg.method if cfg and cfg.method else settings.default_method) or alloc.METHOD_VALUE
    weights = (cfg.weights if cfg else {}) or {}
    currency = str(order.currency)
    warnings = []

    goods, line_info = [], {}
    for line in order.lines.all().select_related('part', 'part__part', 'part__supplier'):
        price, price_cur = money_parts(line.purchase_price)
        unit_price = convert(price, price_cur, currency) if price is not None else None
        if price is not None and unit_price is None:
            warnings.append(f'No exchange rate {price_cur}->{currency} for line {line.pk}')
        sp = line.part
        pack = Decimal(sp.pack_quantity_native or 1) if sp else Decimal(1)
        goods.append(
            alloc.GoodsLine(
                id=line.pk,
                quantity=Decimal(line.quantity),
                unit_price=unit_price,
                pack_quantity=pack,
                discount=Decimal(getattr(line, 'discount', 0) or 0),
                weight=Decimal(str(weights.get(str(line.pk)))) if weights.get(str(line.pk)) not in (None, '') else None,
            )
        )
        line_info[line.pk] = {
            'line': line.pk,
            'reference': line.reference,
            'supplier_part': sp.pk if sp else None,
            'sku': sp.SKU if sp else '',
            'part': sp.part.pk if sp and sp.part else None,
            'part_name': sp.part.full_name if sp and sp.part else '',
            'part_ipn': sp.part.IPN if sp and sp.part else '',
            'units': sp.part.units if sp and sp.part else '',
            'quantity': d2(line.quantity),
            'received': d2(line.received),
            'pack_quantity': d2(pack),
            'unit_price': d2(unit_price),
            'discount': d2(getattr(line, 'discount', 0) or 0),
            'weight': weights.get(str(line.pk)),
        }

    rules = fee_rules()
    overrides = {c.extra_line_id: c for c in ExtraLineConfig.objects.filter(extra_line__order=order)}
    fees, fee_info = [], []
    for extra in order.extra_lines.all():
        price, price_cur = money_parts(extra.price)
        total = ZERO
        if price is not None:
            total = Decimal(extra.quantity) * price * (1 - Decimal(getattr(extra, 'discount', 0) or 0) / 100)
        amount = convert(total, price_cur, currency)
        if amount is None:
            warnings.append(f'No exchange rate {price_cur}->{currency} for extra line {extra.pk}')
            amount = ZERO
        ov = overrides.get(extra.pk)
        detected = detect_category(f'{extra.reference} {extra.description}', rules)
        category = ov.category if ov and ov.category else detected
        targets = list(ov.targets) if ov and ov.targets else []
        fees.append(alloc.FeeLine(id=extra.pk, amount=amount, category=category, targets=targets, reference=extra.reference))
        fee_info.append({
            'id': extra.pk,
            'reference': extra.reference,
            'description': extra.description,
            'amount': d2(amount),
            'category': category,
            'detected_category': detected,
            'overridden': bool(ov and ov.category),
            'targets': targets,
            'included': category in settings.included_categories,
        })

    result = alloc.allocate(goods, fees, method, settings.included_categories)

    for fi in fee_info:
        fi['method_used'] = result.method_used.get(fi['id'])
        fi['unallocated_reason'] = result.unallocated.get(fi['id'])
        if fi['unallocated_reason']:
            fi['included'] = False

    lines = []
    for gl in goods:
        lr = result.lines[gl.id]
        info = line_info[gl.id]
        info.update({
            'net_value': d2(lr.net_value),
            'native_quantity': d2(lr.native_quantity),
            'net_unit_price_native': d2(lr.net_unit_price_native),
            'fees': d2(lr.fees),
            'fees_by_category': {k: d2(v) for k, v in lr.fees_by_category.items()},
            'fee_per_native_unit': d2(lr.fee_per_native_unit),
            'landed_unit_price_native': d2(lr.landed_unit_price_native),
            'uplift_percent': d2(lr.uplift_percent),
        })
        lines.append(info)

    hash_src = json.dumps(
        {
            'm': method,
            'c': list(settings.included_categories),
            'g': [(g.id, str(g.quantity), str(g.unit_price), str(g.pack_quantity), str(g.discount), str(g.weight)) for g in goods],
            'f': [(f.id, str(f.amount), f.category, f.targets) for f in fees],
        },
        sort_keys=True,
    )
    input_hash = hashlib.sha256(hash_src.encode()).hexdigest()

    status = 'not_applied'
    if cfg and cfg.applied:
        status = 'applied' if cfg.applied_hash == input_hash else 'stale'

    return {
        'order': order.pk,
        'reference': order.reference,
        'supplier': order.supplier.name if order.supplier else '',
        'currency': currency,
        'order_status': order.status,
        'method': method,
        'method_is_default': not (cfg and cfg.method),
        'included_categories': list(settings.included_categories),
        'goods_total': d2(result.goods_total),
        'fees_included': d2(result.fees_included),
        'fees_excluded': d2(result.fees_excluded),
        'uplift_percent': d2(result.uplift_percent),
        'lines': lines,
        'fees': fee_info,
        'warnings': warnings,
        'hash': input_hash,
        'status': status,
        'applied_at': cfg.applied_at.isoformat() if cfg and cfg.applied_at else None,
        'applied_by': cfg.applied_by.username if cfg and cfg.applied_by else None,
        '_result': result,
    }


def public(data: dict) -> dict:
    return {k: v for k, v in data.items() if not k.startswith('_')}


# --------------------------------------------------------------------------- apply / revert


def _stock_price_currency() -> str | None:
    """Stock prices are converted to the default currency when PURCHASEORDER_CONVERT_CURRENCY is set."""
    from common.settings import get_global_setting

    return default_currency() if get_global_setting('PURCHASEORDER_CONVERT_CURRENCY') else None


@transaction.atomic
def apply(order, user=None, settings: Settings | None = None) -> dict:
    """Write landed unit cost into the purchase price of all stock items received against the order."""
    from stock.models import StockItem

    from .models import StockPriceBackup

    settings = settings or load_settings()
    data = compute(order, settings)
    order_currency = data['currency']
    target_currency = _stock_price_currency()

    # Group by supplier part: if several lines share a supplier part, use the quantity weighted landed price
    by_sp: dict[int, list[tuple[Decimal, Decimal]]] = {}
    for line in data['lines']:
        if line['supplier_part'] is None or line['landed_unit_price_native'] is None:
            continue
        by_sp.setdefault(line['supplier_part'], []).append(
            (Decimal(line['native_quantity'] or 0), Decimal(line['landed_unit_price_native']))
        )

    updated, parts = 0, set()
    for sp_id, entries in by_sp.items():
        qty = sum((q for q, _ in entries), ZERO)
        landed = (sum((q * p for q, p in entries), ZERO) / qty) if qty else entries[0][1]
        currency = target_currency or order_currency
        price = convert(landed, order_currency, currency)
        if price is None:
            data['warnings'].append(f'Could not convert landed price for supplier part {sp_id}')
            continue
        price = price.quantize(Decimal('0.000001'))

        for item in StockItem.objects.filter(purchase_order=order, supplier_part_id=sp_id):
            orig, orig_cur = money_parts(item.purchase_price)
            backup, created = StockPriceBackup.objects.get_or_create(
                stock_item=item,
                defaults={'order': order, 'original_price': orig, 'original_currency': orig_cur, 'landed_price': price},
            )
            if not created:
                backup.landed_price = price
                backup.save(update_fields=['landed_price'])
            # Queryset update: no tracking entries / signals for a price correction
            StockItem.objects.filter(pk=item.pk).update(purchase_price=price, purchase_price_currency=currency)
            updated += 1
            parts.add(item.part_id)

    cfg = get_config(order, create=True)
    cfg.applied = True
    cfg.applied_hash = data['hash']
    cfg.applied_at = timezone.now()
    cfg.applied_by = user if user and user.is_authenticated else None
    cfg.applied_summary = {
        'stock_items_updated': updated,
        'uplift_percent': data['uplift_percent'],
        'fees_included': data['fees_included'],
        'goods_total': data['goods_total'],
        'currency': order_currency,
    }
    cfg.save()

    _schedule_pricing(parts)
    data['status'] = 'applied'
    data['applied_at'] = cfg.applied_at.isoformat()
    data['stock_items_updated'] = updated
    return data


@transaction.atomic
def revert(order) -> int:
    """Restore original purchase prices for all stock items of the order."""
    from stock.models import StockItem

    from .models import StockPriceBackup

    restored, parts = 0, set()
    for backup in StockPriceBackup.objects.filter(order=order).select_related('stock_item'):
        StockItem.objects.filter(pk=backup.stock_item_id).update(
            purchase_price=backup.original_price, purchase_price_currency=backup.original_currency or None
        )
        parts.add(backup.stock_item.part_id)
        restored += 1
    StockPriceBackup.objects.filter(order=order).delete()

    cfg = get_config(order)
    if cfg:
        cfg.applied = False
        cfg.applied_hash = ''
        cfg.applied_at = None
        cfg.applied_by = None
        cfg.applied_summary = {}
        cfg.save()

    _schedule_pricing(parts)
    return restored


def _schedule_pricing(part_ids):
    from part.models import Part

    for part in Part.objects.filter(pk__in=part_ids):
        try:
            part.schedule_pricing_update(create=True)
        except Exception:
            logger.exception('landed_cost_capacity: pricing update failed for part %s', part.pk)


# --------------------------------------------------------------------------- automation


def auto_apply_order(order_id: int, reason: str = '') -> None:
    """Apply landed cost automatically (event / schedule entry point)."""
    from order.models import PurchaseOrder

    settings = load_settings()
    if not settings.auto_apply:
        return
    order = PurchaseOrder.objects.filter(pk=order_id).first()
    if order is None:
        return
    try:
        apply(order, None, settings)
        logger.info('landed_cost_capacity: applied landed cost to %s (%s)', order.reference, reason)
    except Exception:
        logger.exception('landed_cost_capacity: failed to apply landed cost to order %s', order_id)


def nightly_check() -> dict:
    """Re-apply orders whose inputs changed since they were applied, and catch missed new orders."""
    from order.models import PurchaseOrder
    from order.status_codes import PurchaseOrderStatus

    from .models import OrderConfig

    settings = load_settings()
    summary = {'reapplied': [], 'new': []}
    if not settings.auto_apply:
        return summary

    for cfg in OrderConfig.objects.filter(applied=True).select_related('order'):
        data = compute(cfg.order, settings)
        if data['hash'] != cfg.applied_hash:
            apply(cfg.order, None, settings)
            summary['reapplied'].append(cfg.order.reference)

    if settings.auto_apply_since:
        qs = PurchaseOrder.objects.filter(
            status=PurchaseOrderStatus.COMPLETE.value, complete_date__gt=settings.auto_apply_since
        ).exclude(landed_cost_config__applied=True)
        for order in qs:
            apply(order, None, settings)
            summary['new'].append(order.reference)
    return summary


def backfill(dry_run: bool = True, user=None, order_ids=None) -> list[dict]:
    """Preview (or apply) landed cost for all completed/received purchase orders."""
    from order.models import PurchaseOrder

    settings = load_settings()
    qs = PurchaseOrder.objects.filter(lines__received__gt=0).distinct().order_by('pk')
    if order_ids:
        qs = qs.filter(pk__in=order_ids)
    out = []
    for order in qs:
        data = apply(order, user, settings) if not dry_run else compute(order, settings)
        out.append({
            'order': order.pk,
            'reference': order.reference,
            'supplier': data['supplier'],
            'currency': data['currency'],
            'goods_total': data['goods_total'],
            'fees_included': data['fees_included'],
            'fees_excluded': data['fees_excluded'],
            'uplift_percent': data['uplift_percent'],
            'status': data['status'],
            'warnings': data['warnings'],
            'lines': [
                {k: line[k] for k in ('part_name', 'part_ipn', 'sku', 'net_unit_price_native', 'fee_per_native_unit', 'landed_unit_price_native', 'uplift_percent', 'units')}
                for line in data['lines']
            ],
            'fees': [{k: f[k] for k in ('reference', 'amount', 'category', 'included')} for f in data['fees']],
        })
    return out
