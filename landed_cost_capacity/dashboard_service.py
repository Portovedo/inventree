"""Data for the dashboard widgets: overview, production, purchasing needs, incoming orders,
inventory value and supplier fees. Results are cached briefly so a dashboard with several
widgets stays fast."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db.models import Sum

from .config import Settings, load_settings
from .costing import OrderCache, _computed_landed, estimated_purchase_unit, primary_supplier_part, rolled_cost, supplier_uplifts
from .landed import compute, convert, d2, default_currency, money_parts

ZERO = Decimal(0)
CACHE_SECONDS = 120


def cached(name: str, fn, refresh: bool = False):
    key = f'landed_cost_capacity:dashboard:{name}'
    if not refresh:
        hit = cache.get(key)
        if hit is not None:
            return hit
    data = fn()
    cache.set(key, data, CACHE_SECONDS)
    return data


def _brief(part):
    return {'part': part.pk, 'name': part.full_name, 'ipn': part.IPN, 'units': part.units}


# --------------------------------------------------------------------------- production


BUILD_STATUS = {10: 'Pending', 20: 'Production', 25: 'On hold'}


def _active_build_lines():
    from build.models import BuildLine
    from build.status_codes import BuildStatusGroups

    return (
        BuildLine.objects.filter(build__status__in=BuildStatusGroups.ACTIVE_CODES)
        .select_related('build', 'bom_item', 'bom_item__sub_part')
        .annotate(allocated=Sum('allocations__quantity'))
    )


def _remaining(line) -> Decimal:
    """Quantity of a build line still to be allocated (required - consumed - allocated)."""
    return max(ZERO, Decimal(line.quantity) - Decimal(line.consumed or 0) - Decimal(line.allocated or 0))


def production(settings: Settings | None = None) -> dict:
    from build.models import Build
    from build.status_codes import BuildStatusGroups

    from .capacity_service import availability

    settings = settings or load_settings()
    today = date.today()
    lines = [ln for ln in _active_build_lines() if not ln.bom_item.consumable]
    avail = availability(list({ln.bom_item.sub_part_id for ln in lines}), settings)

    # Free stock is shared between builds: hand it out in target-date order
    by_build = defaultdict(list)
    for ln in lines:
        by_build[ln.build_id].append(ln)

    pool = dict(avail)
    builds = []
    qs = Build.objects.filter(status__in=BuildStatusGroups.ACTIVE_CODES).select_related('part')
    for b in sorted(qs, key=lambda x: (x.target_date or date.max, x.pk)):
        blines = by_build.get(b.pk, [])
        allocated_lines = 0
        short = []
        for ln in blines:
            rem = _remaining(ln)
            if rem <= 0:
                allocated_lines += 1
                continue
            pid = ln.bom_item.sub_part_id
            take = min(pool.get(pid, ZERO), rem)
            pool[pid] = pool.get(pid, ZERO) - take
            if rem - take > 0:
                short.append({**_brief(ln.bom_item.sub_part), 'missing': d2(rem - take)})
        builds.append({
            'build': b.pk,
            'reference': b.reference,
            'title': b.title,
            'part': _brief(b.part),
            'quantity': d2(b.quantity),
            'completed': d2(b.completed),
            'status': BUILD_STATUS.get(b.status, str(b.status)),
            'start_date': b.start_date.isoformat() if b.start_date else None,
            'target_date': b.target_date.isoformat() if b.target_date else None,
            'overdue': bool(b.target_date and b.target_date < today),
            'lines': len(blines),
            'lines_allocated': allocated_lines,
            'shortages': short,
        })
    return {'builds': builds}


# --------------------------------------------------------------------------- purchasing


def purchasing_needs(settings: Settings | None = None) -> dict:
    """Parts to buy (or build) to cover active build orders and minimum stock levels."""
    from part.models import Part

    from .capacity_service import availability, on_order

    settings = settings or load_settings()
    need = defaultdict(Decimal)
    builds_for = defaultdict(set)
    for ln in _active_build_lines():
        if ln.bom_item.consumable:
            continue
        rem = _remaining(ln)
        if rem > 0:
            need[ln.bom_item.sub_part_id] += rem
            builds_for[ln.bom_item.sub_part_id].add(ln.build.reference)

    minimum = {
        p.pk: Decimal(p.minimum_stock)
        for p in Part.objects.filter(active=True, minimum_stock__gt=0).only('pk', 'minimum_stock')
    }
    part_ids = set(need) | set(minimum)
    avail = availability(list(part_ids), settings)
    parts = {p.pk: p for p in Part.objects.filter(pk__in=part_ids)}

    cache_ = OrderCache(settings)
    uplift_cache: dict = {}
    rows = []
    totals = defaultdict(Decimal)
    for pid in part_ids:
        p = parts[pid]
        required = need.get(pid, ZERO) + minimum.get(pid, ZERO)
        shortage = max(ZERO, required - avail.get(pid, ZERO))
        if shortage <= 0:
            continue
        ordered, orders = on_order(p, settings)
        to_get = max(ZERO, shortage - ordered)
        est = None
        action = 'build' if p.assembly else 'buy'
        if action == 'buy' and to_get > 0:
            est = estimated_purchase_unit(p, to_get, cache_, uplift_cache)
            if est and est.get('landed_total'):
                totals[est['supplier']] += Decimal(est['landed_total'])
        reasons = []
        if need.get(pid):
            reasons.append('Builds ' + ', '.join(sorted(builds_for[pid])))
        if minimum.get(pid):
            reasons.append(f'Min. stock {d2(minimum[pid])}')
        rows.append({
            **_brief(p),
            'action': action,
            'required': d2(required),
            'available': d2(avail.get(pid, ZERO)),
            'shortage': d2(shortage),
            'on_order': d2(ordered),
            'orders': orders,
            'to_get': d2(to_get),
            'reasons': reasons,
            'estimate': est,
            'no_supplier': action == 'buy' and primary_supplier_part(p) is None,
        })
    rows.sort(key=lambda r: (Decimal(r['to_get']) == 0, r['action'] != 'buy', r['name']))
    return {
        'currency': default_currency(),
        'parts': rows,
        'total_by_supplier': {k: d2(v) for k, v in sorted(totals.items())},
        'total': d2(sum(totals.values(), ZERO)),
    }


# --------------------------------------------------------------------------- incoming


def incoming(settings: Settings | None = None) -> dict:
    from order.models import PurchaseOrder
    from order.status_codes import PurchaseOrderStatus, PurchaseOrderStatusGroups

    settings = settings or load_settings()
    cur = default_currency()
    today = date.today()
    labels = {PurchaseOrderStatus.PENDING.value: 'Pending', PurchaseOrderStatus.PLACED.value: 'Placed', PurchaseOrderStatus.ON_HOLD.value: 'On hold'}
    open_rows = []
    total = ZERO
    for o in PurchaseOrder.objects.filter(status__in=PurchaseOrderStatusGroups.OPEN).select_related('supplier'):
        lines = list(o.lines.all())
        remaining = sum(1 for ln in lines if ln.received < ln.quantity)
        price, pc = money_parts(o.total_price)
        value = convert(price, pc, cur) if price is not None else None
        total += value or ZERO
        open_rows.append({
            'order': o.pk,
            'reference': o.reference,
            'supplier': o.supplier.name if o.supplier else '',
            'status': labels.get(o.status, str(o.status)),
            'target_date': o.target_date.isoformat() if o.target_date else None,
            'overdue': bool(o.target_date and o.target_date < today),
            'lines': len(lines),
            'lines_remaining': remaining,
            'value': d2(value),
        })
    open_rows.sort(key=lambda r: (r['target_date'] or '9999', r['reference']))

    since = today - timedelta(days=30)
    recent = []
    for o in PurchaseOrder.objects.filter(status=PurchaseOrderStatus.COMPLETE.value, complete_date__gte=since).select_related('supplier').order_by('-complete_date')[:5]:
        recent.append({'order': o.pk, 'reference': o.reference, 'supplier': o.supplier.name if o.supplier else '', 'date': o.complete_date.isoformat()})
    return {
        'currency': cur,
        'open': open_rows,
        'open_value': d2(total),
        'overdue': sum(1 for r in open_rows if r['overdue']),
        'recent': recent,
    }


# --------------------------------------------------------------------------- inventory value


def inventory_value(settings: Settings | None = None) -> dict:
    """Stock value at landed cost, by category; built assemblies valued at BOM roll-up."""
    from stock.models import StockItem

    settings = settings or load_settings()
    cur = default_currency()
    cache_ = OrderCache(settings)
    memo: dict = {}
    by_cat = defaultdict(lambda: {'value': ZERO, 'items': 0})
    by_part = defaultdict(lambda: {'value': ZERO, 'quantity': ZERO})
    total = external = estimated = ZERO
    unpriced = 0

    items = StockItem.objects.filter(StockItem.IN_STOCK_FILTER).select_related(
        'part', 'part__category', 'location', 'purchase_order', 'landed_cost_backup'
    )
    for item in items:
        q = Decimal(item.quantity)
        unit = None
        if item.purchase_order_id and item.supplier_part_id and not hasattr(item, 'landed_cost_backup'):
            # Received on a PO whose landed cost is not applied yet: use the computed landed price
            unit = _computed_landed(item, cache_, cur)
        if unit is None:
            price, pc = money_parts(item.purchase_price)
            unit = convert(price, pc, cur) if price is not None else None
        is_estimate = False
        if unit is None and item.part.assembly:
            key = ('rolled', item.part_id)
            if key not in memo:
                rc = rolled_cost(item.part, settings, cache_)
                memo[key] = Decimal(rc['unit_cost']) if rc.get('unit_cost') else None
            unit = memo[key]
            is_estimate = unit is not None
        if unit is None:
            unpriced += 1
            continue
        value = q * unit
        total += value
        if is_estimate:
            estimated += value
        if item.location and item.location.external:
            external += value
        cat = item.part.category.pathstring if item.part.category else 'Uncategorised'
        by_cat[cat]['value'] += value
        by_cat[cat]['items'] += 1
        by_part[item.part_id]['value'] += value
        by_part[item.part_id]['quantity'] += q
        by_part[item.part_id]['part'] = item.part

    cats = sorted(by_cat.items(), key=lambda kv: -kv[1]['value'])
    top = sorted(by_part.values(), key=lambda v: -v['value'])[:8]
    return {
        'currency': cur,
        'total': d2(total),
        'external': d2(external),
        'estimated': d2(estimated),
        'unpriced_items': unpriced,
        'categories': [{'category': k, 'value': d2(v['value']), 'items': v['items']} for k, v in cats],
        'top_parts': [{**_brief(v['part']), 'value': d2(v['value']), 'quantity': d2(v['quantity'])} for v in top],
    }


# --------------------------------------------------------------------------- supplier fees


def supplier_fees(settings: Settings | None = None, months: int = 12) -> dict:
    """Fees paid per month (by type) and each supplier's typical fee uplift."""
    from order.models import PurchaseOrder

    settings = settings or load_settings()
    cache_ = OrderCache(settings)
    cur = default_currency()
    today = date.today()
    # First day of the month (months - 1) months ago
    y, mth = today.year, today.month - (months - 1)
    while mth <= 0:
        y, mth = y - 1, mth + 12
    first = date(y, mth, 1)

    month_keys = []
    m = first
    while m <= today:
        month_keys.append(m.strftime('%Y-%m'))
        m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)

    per_month = {k: defaultdict(Decimal) for k in month_keys}
    goods_month = defaultdict(Decimal)
    totals = defaultdict(Decimal)
    goods_total = ZERO
    for o in PurchaseOrder.objects.filter(lines__received__gt=0).distinct():
        when = o.creation_date or o.issue_date
        if not when or when < first:
            continue
        key = when.strftime('%Y-%m')
        data = cache_.get(o)
        oc = data['currency']
        g = convert(Decimal(data['goods_total'] or 0), oc, cur) or ZERO
        goods_month[key] += g
        goods_total += g
        for fee in data['fees']:
            amount = convert(Decimal(fee['amount'] or 0), oc, cur) or ZERO
            cat = fee['category'] if fee['included'] else 'not_counted'
            if key in per_month:
                per_month[key][cat] += amount
            totals[cat] += amount

    counted = sum((v for k, v in totals.items() if k != 'not_counted'), ZERO)
    return {
        'currency': cur,
        'months': [
            {'month': k, 'goods': d2(goods_month.get(k, ZERO)), **{c: d2(v) for c, v in per_month[k].items()}}
            for k in month_keys
        ],
        'totals': {k: d2(v) for k, v in totals.items()},
        'fees_counted': d2(counted),
        'goods_total': d2(goods_total),
        'uplift_percent': d2(counted / goods_total * 100) if goods_total else None,
        'suppliers': supplier_uplifts(cache_),
    }


# --------------------------------------------------------------------------- overview


def overview(settings: Settings | None = None, refresh: bool = False) -> dict:
    from .capacity_service import dashboard as capacity_dashboard

    settings = settings or load_settings()
    cap = cached('capacity', lambda: capacity_dashboard(settings), refresh)
    prod = cached('production', lambda: production(settings), refresh)
    need = cached('purchasing', lambda: purchasing_needs(settings), refresh)
    inc = cached('incoming', lambda: incoming(settings), refresh)
    inv = cached('inventory', lambda: inventory_value(settings), refresh)
    fees = cached('fees', lambda: supplier_fees(settings), refresh)

    products = [a for a in cap['assemblies'] if not a['used_in_other_assemblies']]
    return {
        'currency': default_currency(),
        'products': [
            {'part': a['part'], 'name': a['name'], 'can_build': a['multi_level'], 'in_stock': a['in_stock'], 'unit_cost': a['unit_cost'], 'limiting': a['limiting'][:1]}
            for a in products
        ],
        'inventory_value': inv['total'],
        'inventory_estimated': inv['estimated'],
        'active_builds': len(prod['builds']),
        'overdue_builds': sum(1 for b in prod['builds'] if b['overdue']),
        'units_in_progress': d2(sum((Decimal(b['quantity']) - Decimal(b['completed']) for b in prod['builds']), ZERO)),
        'parts_to_buy': sum(1 for p in need['parts'] if p['action'] == 'buy' and Decimal(p['to_get']) > 0),
        'parts_to_build': sum(1 for p in need['parts'] if p['action'] == 'build' and Decimal(p['to_get']) > 0),
        'purchase_estimate': need['total'],
        'open_orders': len(inc['open']),
        'open_orders_value': inc['open_value'],
        'overdue_orders': inc['overdue'],
        'fees_counted': fees['fees_counted'],
        'fees_uplift_percent': fees['uplift_percent'],
    }
