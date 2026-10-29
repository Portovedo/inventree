"""Costing service: landed cost history per part, supplier fee uplift, assembly cost roll-up."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from .config import Settings, load_settings
from .landed import compute, convert, d2, default_currency, money_parts

ZERO = Decimal(0)


class OrderCache:
    """Memoise compute() per order within one request."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._data = {}

    def get(self, order):
        if order.pk not in self._data:
            self._data[order.pk] = compute(order, self.settings)
        return self._data[order.pk]


def _order_date(order):
    return order.complete_date or order.issue_date or order.creation_date


def part_history(part, cache: OrderCache) -> list[dict]:
    """Landed cost per received PO line for this part (newest first), in default currency."""
    from order.models import PurchaseOrderLineItem

    cur = default_currency()
    rows = []
    lines = (
        PurchaseOrderLineItem.objects.filter(part__part=part, received__gt=0)
        .select_related('order', 'order__supplier', 'part')
        .order_by('-order__complete_date', '-order__issue_date', '-pk')
    )
    for line in lines:
        data = cache.get(line.order)
        info = next((x for x in data['lines'] if x['line'] == line.pk), None)
        if info is None:
            continue
        oc = data['currency']

        def conv(v, oc=oc):
            return d2(convert(Decimal(v), oc, cur)) if v is not None else None

        rows.append({
            'order': line.order.pk,
            'reference': line.order.reference,
            'supplier': data['supplier'],
            'date': _order_date(line.order).isoformat() if _order_date(line.order) else None,
            'received_native': d2(Decimal(line.received) * Decimal(info['pack_quantity'] or 1)),
            'net_unit': conv(info['net_unit_price_native']),
            'fee_unit': conv(info['fee_per_native_unit']),
            'landed_unit': conv(info['landed_unit_price_native']),
            'uplift_percent': info['uplift_percent'],
            'status': data['status'],
        })
    return rows


def _computed_landed(item, cache: OrderCache, cur: str) -> Decimal | None:
    """Landed unit price (default currency) of a stock item from its PO, even if not yet applied."""
    data = cache.get(item.purchase_order)
    rows = [
        (Decimal(line['native_quantity'] or 0), Decimal(line['landed_unit_price_native']))
        for line in data['lines']
        if line['supplier_part'] == item.supplier_part_id and line['landed_unit_price_native'] is not None
    ]
    qty = sum((q for q, _ in rows), ZERO)
    if not rows or not qty:
        return None
    return convert(sum((q * p for q, p in rows), ZERO) / qty, data['currency'], cur)


def landed_item_ids(items) -> set:
    """Ids of the given stock items whose landed cost has been applied.

    Queried from the backup table instead of the reverse relation on StockItem: that
    relation only exists while the plugin app is registered in the current process, which
    is not guaranteed after a runtime plugin reload.
    """
    from .models import StockPriceBackup

    return set(StockPriceBackup.objects.filter(stock_item__in=items.values('pk')).values_list('stock_item_id', flat=True))


def stock_average(part, cache: OrderCache | None = None) -> dict:
    """Quantity weighted average landed price of stock on hand (default currency).

    Stock received against a purchase order is valued at its landed cost: the applied
    price if landed cost was written, otherwise the computed landed price of its PO line.
    Other stock is valued at its purchase price.
    """
    from stock.models import StockItem

    cache = cache or OrderCache(load_settings())
    cur = default_currency()
    total_q = total_v = ZERO
    unpriced = ZERO
    landed_q = ZERO
    items = StockItem.objects.filter(part=part).filter(StockItem.IN_STOCK_FILTER)
    applied = landed_item_ids(items)
    for item in items.select_related('purchase_order'):
        q = Decimal(item.quantity)
        value = None
        is_landed = item.pk in applied
        if item.purchase_order_id and item.supplier_part_id and not is_landed:
            value = _computed_landed(item, cache, cur)
            is_landed = value is not None
        if value is None:
            price, pc = money_parts(item.purchase_price)
            value = convert(price, pc, cur) if price is not None else None
        if value is None:
            unpriced += q
            continue
        total_q += q
        total_v += q * value
        if is_landed:
            landed_q += q
    return {
        'average': d2(total_v / total_q) if total_q else None,
        'priced_quantity': d2(total_q),
        'unpriced_quantity': d2(unpriced),
        'landed_quantity': d2(landed_q),
    }


def supplier_uplift(supplier, cache: OrderCache) -> Decimal | None:
    """Average fee uplift (%) of a supplier's received orders, weighted by goods value."""
    from order.models import PurchaseOrder

    qs = PurchaseOrder.objects.filter(supplier=supplier, lines__received__gt=0).distinct()
    if cache.settings.uplift_lookback_days:
        since = timezone.now().date() - timedelta(days=cache.settings.uplift_lookback_days)
        qs = qs.filter(issue_date__gte=since)
    goods = fees = ZERO
    cur = default_currency()
    for order in qs:
        data = cache.get(order)
        g = convert(Decimal(data['goods_total'] or 0), data['currency'], cur) or ZERO
        f = convert(Decimal(data['fees_included'] or 0), data['currency'], cur) or ZERO
        goods += g
        fees += f
    if not goods:
        return None
    return fees / goods * 100


def supplier_uplifts(cache: OrderCache) -> list[dict]:
    from company.models import Company

    rows = []
    for company in Company.objects.filter(is_supplier=True).order_by('name'):
        up = supplier_uplift(company, cache)
        if up is not None:
            rows.append({'supplier': company.pk, 'name': company.name, 'uplift_percent': d2(up)})
    return rows


def primary_supplier_part(part):
    sps = part.supplier_parts.filter(active=True).select_related('supplier')
    return sps.filter(primary=True).first() or sps.first()


def estimated_purchase_unit(part, native_qty: Decimal, cache: OrderCache, uplift_cache: dict) -> dict | None:
    """Estimate landed cost per native unit when buying ``native_qty`` from the primary supplier."""
    sp = primary_supplier_part(part)
    if sp is None:
        return None
    pack = Decimal(sp.pack_quantity_native or 1)
    packs = max(Decimal(1), (Decimal(native_qty) / pack).to_integral_value(rounding='ROUND_CEILING'))
    cur = default_currency()
    total = sp.get_price(packs, currency=cur)
    if total is None:
        return {'supplier_part': sp.pk, 'supplier': sp.supplier.name, 'sku': sp.SKU, 'packs': d2(packs), 'pack_quantity': d2(pack)}
    total = Decimal(total.amount if hasattr(total, 'amount') else total)
    if sp.supplier_id not in uplift_cache:
        uplift_cache[sp.supplier_id] = supplier_uplift(sp.supplier, cache)
    up = uplift_cache[sp.supplier_id] or ZERO
    landed_total = total * (1 + up / 100)
    return {
        'supplier_part': sp.pk,
        'supplier': sp.supplier.name,
        'sku': sp.SKU,
        'packs': d2(packs),
        'pack_quantity': d2(pack),
        'price_total': d2(total),
        'uplift_percent': d2(up),
        'landed_total': d2(landed_total),
        'landed_unit': d2(landed_total / (packs * pack)),
    }


def unit_cost(part, cache: OrderCache, uplift_cache: dict) -> tuple[Decimal | None, str, Decimal | None]:
    """Cost per native unit of a purchased component: (cost, source, fee share %)."""
    history = part_history(part, cache)
    uplift = None
    if history and history[0]['uplift_percent'] is not None:
        uplift = Decimal(history[0]['uplift_percent'])

    avg = stock_average(part, cache)
    if avg['average'] is not None and Decimal(avg['priced_quantity'] or 0) > 0:
        source = 'stock_landed' if Decimal(avg['landed_quantity'] or 0) >= Decimal(avg['priced_quantity']) else 'stock'
        return Decimal(avg['average']), source, uplift if source == 'stock_landed' else None
    if history and history[0]['landed_unit'] is not None:
        return Decimal(history[0]['landed_unit']), 'last_purchase', uplift
    est = estimated_purchase_unit(part, Decimal(1), cache, uplift_cache)
    if est and est.get('landed_unit'):
        return Decimal(est['landed_unit']), 'supplier_estimate', Decimal(est['uplift_percent'] or 0)
    return None, 'missing', None


def rolled_cost(part, settings: Settings | None = None, cache: OrderCache | None = None, _memo=None, _path=()) -> dict:
    """Recursive cost breakdown of an assembly per unit (default currency)."""
    settings = settings or load_settings()
    cache = cache or OrderCache(settings)
    memo = {} if _memo is None else _memo
    uplift_cache = memo.setdefault('__uplift__', {})

    node = {
        'part': part.pk,
        'name': part.full_name,
        'ipn': part.IPN,
        'units': part.units,
        'assembly': bool(part.assembly),
        'children': [],
    }

    if part.assembly and part.pk not in _path:
        items = part.get_bom_items().select_related('sub_part')
        total = ZERO
        fees = ZERO
        missing = False
        for bi in items:
            if bi.optional or (bi.consumable and not settings.cost_include_consumable):
                continue
            qty_per = Decimal(bi.quantity) * (1 + Decimal(bi.attrition or 0) / 100)
            child = rolled_cost(bi.sub_part, settings, cache, memo, (*_path, part.pk))
            child = dict(child, bom_quantity=d2(qty_per), reference=bi.reference)
            if child['unit_cost'] is None:
                missing = True
                child['extended'] = None
            else:
                ext = Decimal(child['unit_cost']) * qty_per
                child['extended'] = d2(ext)
                total += ext
                fees += Decimal(child['fees_unit'] or 0) * qty_per
            node['children'].append(child)
        node.update({
            'unit_cost': d2(total) if node['children'] else None,
            'fees_unit': d2(fees),
            'source': 'bom' + ('_incomplete' if missing else ''),
            'complete': not missing,
        })
        return node

    if part.pk in memo:
        cost, source, uplift = memo[part.pk]
    else:
        cost, source, uplift = unit_cost(part, cache, uplift_cache)
        memo[part.pk] = (cost, source, uplift)
    fee_unit = None
    if cost is not None and uplift is not None:
        fee_unit = cost * uplift / (100 + uplift)
    node.update({
        'unit_cost': d2(cost),
        'fees_unit': d2(fee_unit) if fee_unit is not None else '0',
        'source': source,
        'complete': cost is not None,
    })
    return node


def part_landed_summary(part, settings: Settings | None = None) -> dict:
    settings = settings or load_settings()
    cache = OrderCache(settings)
    data = {
        'part': part.pk,
        'currency': default_currency(),
        'history': part_history(part, cache),
        'stock': stock_average(part, cache),
    }
    if part.assembly:
        data['rolled'] = rolled_cost(part, settings, cache)
    sp = primary_supplier_part(part) if part.purchaseable else None
    if sp is not None:
        up = supplier_uplift(sp.supplier, cache)
        data['primary_supplier'] = {'name': sp.supplier.name, 'sku': sp.SKU, 'uplift_percent': d2(up)}
    return data
