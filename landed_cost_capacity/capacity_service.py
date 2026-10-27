"""Capacity service: build the BOM graph + availability from InvenTree and run capacity/plan queries."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db.models import Q, Sum

from . import capacity as cap
from .config import Settings, load_settings
from .costing import OrderCache, estimated_purchase_unit, primary_supplier_part, rolled_cost
from .landed import d2

ZERO = Decimal(0)


def _bom_lines(part, settings: Settings):
    items = part.get_bom_items(include_virtual=False).select_related('sub_part').prefetch_related('substitutes__part')
    for bi in items:
        if bi.optional and not settings.capacity_include_optional:
            continue
        if bi.consumable and not settings.capacity_include_consumable:
            continue
        yield bi


def build_graph(top, settings: Settings | None = None) -> tuple[cap.Graph, dict]:
    """Return (graph, parts) for everything reachable from ``top`` through the BOM."""
    from part.models import Part

    settings = settings or load_settings()
    lines: dict[int, list[cap.Line]] = {}
    parts: dict[int, Part] = {top.pk: top}
    bom_items: dict[int, object] = {}
    queue = [top]
    seen = set()
    while queue:
        part = queue.pop()
        if part.pk in seen:
            continue
        seen.add(part.pk)
        if not part.assembly:
            continue
        plines = []
        for bi in _bom_lines(part, settings):
            alts = []
            if bi.allow_variants and settings.capacity_use_variants:
                alts += [p for p in bi.sub_part.get_descendants(include_self=False) if p.active]
            if settings.capacity_use_substitutes:
                alts += [s.part for s in bi.substitutes.all()]
            for p in [bi.sub_part, *alts]:
                parts.setdefault(p.pk, p)
            plines.append(
                cap.Line(
                    parent=part.pk,
                    child=bi.sub_part.pk,
                    quantity=Decimal(bi.quantity),
                    alternatives=[a.pk for a in alts if a.pk != bi.sub_part.pk],
                    attrition=Decimal(bi.attrition or 0),
                    setup_quantity=Decimal(bi.setup_quantity or 0),
                    rounding_multiple=Decimal(bi.rounding_multiple) if bi.rounding_multiple else None,
                    reference=bi.reference,
                    id=bi.pk,
                )
            )
            bom_items[bi.pk] = bi
            queue.append(bi.sub_part)
        if plines:
            lines[part.pk] = plines

    stock = availability(list(parts), settings)
    whole = {pk for pk in lines}  # assemblies are built in whole units
    for pk, p in parts.items():
        if p.trackable:
            whole.add(pk)
    return cap.Graph(lines=lines, stock=stock, whole_units=whole), parts


def availability(part_ids: list[int], settings: Settings) -> dict[int, Decimal]:
    """Usable stock per part: in stock, optionally excluding external locations and allocated quantities."""
    from build.models import BuildItem
    from build.status_codes import BuildStatusGroups
    from order.models import SalesOrderAllocation
    from stock.models import StockItem

    items = StockItem.objects.filter(part_id__in=part_ids).filter(StockItem.IN_STOCK_FILTER)
    if settings.capacity_exclude_external:
        items = items.exclude(location__external=True)
    stock = defaultdict(Decimal)
    for row in items.values('part_id').annotate(q=Sum('quantity')):
        stock[row['part_id']] += Decimal(row['q'] or 0)

    if not settings.capacity_include_allocated:
        build_alloc = BuildItem.objects.filter(
            stock_item__in=items, build_line__build__status__in=BuildStatusGroups.ACTIVE_CODES
        )
        for row in build_alloc.values('stock_item__part_id').annotate(q=Sum('quantity')):
            stock[row['stock_item__part_id']] -= Decimal(row['q'] or 0)
        so_alloc = SalesOrderAllocation.objects.filter(item__in=items).filter(
            Q(shipment__isnull=True) | Q(shipment__shipment_date__isnull=True)
        )
        for row in so_alloc.values('item__part_id').annotate(q=Sum('quantity')):
            stock[row['item__part_id']] -= Decimal(row['q'] or 0)

    return {pk: max(ZERO, stock.get(pk, ZERO)) for pk in part_ids}


def _part_brief(part, stock=None):
    return {
        'part': part.pk,
        'name': part.full_name,
        'ipn': part.IPN,
        'units': part.units,
        'assembly': bool(part.assembly),
        'available': d2(stock) if stock is not None else None,
    }


def part_capacity(part, settings: Settings | None = None) -> dict:
    settings = settings or load_settings()
    graph, parts = build_graph(part, settings)
    if not graph.has_bom(part.pk):
        return {'part': part.pk, 'has_bom': False}

    n, shortages = cap.limiting_parts(graph, part.pk)
    single = cap.max_buildable(graph, part.pk, expand_subassemblies=False)
    sim = cap.simulate(graph, part.pk, n) if n else None

    headroom = []
    for h in cap.direct_headroom(graph, part.pk):
        child = parts[h.line.child]
        headroom.append(dict(
            _part_brief(child, h.available),
            per_unit=d2(h.per_unit),
            can_build=h.can_build,
            reference=h.line.reference,
            alternatives=[parts[a].full_name for a in h.line.alternatives],
        ))

    try:
        native = int(part.can_build)
    except Exception:
        native = None

    return {
        'part': part.pk,
        'has_bom': True,
        'native_can_build': native,
        'single_level': single,
        'multi_level': n,
        'limiting': [dict(_part_brief(parts[pk], graph.stock.get(pk)), missing=d2(q)) for pk, q in shortages.items()],
        'builds_needed': [
            dict(_part_brief(parts[pk], graph.stock.get(pk)), quantity=d2(q))
            for pk, q in (sim.to_build.items() if sim else [])
            if pk != part.pk
        ],
        'headroom': headroom,
        'settings': {
            'exclude_external': settings.capacity_exclude_external,
            'include_allocated': settings.capacity_include_allocated,
            'include_optional': settings.capacity_include_optional,
            'include_consumable': settings.capacity_include_consumable,
        },
    }


def on_order(part, settings: Settings) -> tuple[Decimal, list[dict]]:
    """Outstanding purchase order quantity (native units) for a part."""
    from order.models import PurchaseOrderLineItem
    from order.status_codes import PurchaseOrderStatus

    statuses = [PurchaseOrderStatus.PLACED.value]
    if settings.plan_include_pending_orders:
        statuses += [PurchaseOrderStatus.PENDING.value, PurchaseOrderStatus.ON_HOLD.value]
    total = ZERO
    rows = []
    for line in PurchaseOrderLineItem.objects.filter(part__part=part, order__status__in=statuses).select_related('order', 'part'):
        remaining = (Decimal(line.quantity) - Decimal(line.received)) * Decimal(line.part.pack_quantity_native or 1)
        if remaining <= 0:
            continue
        total += remaining
        target = line.target_date or line.order.target_date
        rows.append({
            'order': line.order.pk,
            'reference': line.order.reference,
            'status': line.order.status,
            'quantity': d2(remaining),
            'target_date': target.isoformat() if target else None,
        })
    return total, rows


def plan(part, quantity, settings: Settings | None = None) -> dict:
    """What is needed to build ``quantity`` units: builds, stock usage, shortages, purchasing estimate."""
    settings = settings or load_settings()
    quantity = Decimal(quantity)
    graph, parts = build_graph(part, settings)
    sim = cap.simulate(graph, part.pk, quantity)
    cache = OrderCache(settings)
    uplift_cache: dict = {}

    requirements = []
    for pk, demand in sorted(sim.demand.items(), key=lambda kv: parts[kv[0]].full_name):
        if pk == part.pk:
            continue
        p = parts[pk]
        requirements.append(dict(
            _part_brief(p, graph.stock.get(pk)),
            required=d2(demand),
            from_stock=d2(sim.from_stock.get(pk, ZERO)),
            from_alternatives={parts[a].full_name: d2(q) for a, q in sim.from_alternatives.get(pk, {}).items()},
            to_build=d2(sim.to_build.get(pk)) if pk in sim.to_build else None,
            shortage=d2(sim.shortages.get(pk)) if pk in sim.shortages else None,
        ))

    purchasing = []
    total_by_supplier: dict[str, Decimal] = defaultdict(Decimal)
    for pk, missing in sim.shortages.items():
        p = parts[pk]
        ordered, orders = on_order(p, settings)
        to_buy = max(ZERO, missing - ordered)
        est = estimated_purchase_unit(p, to_buy, cache, uplift_cache) if to_buy > 0 else None
        if est and est.get('landed_total'):
            total_by_supplier[est['supplier']] += Decimal(est['landed_total'])
        sp = primary_supplier_part(p)
        purchasing.append(dict(
            _part_brief(p, graph.stock.get(pk)),
            shortage=d2(missing),
            on_order=d2(ordered),
            orders=orders,
            to_buy=d2(to_buy),
            estimate=est,
            no_supplier=sp is None,
            is_assembly_without_expansion=bool(p.assembly),
        ))
    purchasing.sort(key=lambda r: (r['to_buy'] == '0', r['name']))

    unit = rolled_cost(part, settings, cache)
    return {
        'part': part.pk,
        'quantity': d2(quantity),
        'feasible': sim.feasible,
        'builds': [
            dict(_part_brief(parts[pk], graph.stock.get(pk)), quantity=d2(q)) for pk, q in sim.to_build.items()
        ],
        'requirements': requirements,
        'purchasing': purchasing,
        'purchase_total_by_supplier': {k: d2(v) for k, v in total_by_supplier.items()},
        'purchase_total': d2(sum(total_by_supplier.values(), ZERO)),
        'unit_cost': unit.get('unit_cost'),
        'unit_cost_complete': unit.get('complete'),
    }


def dashboard(settings: Settings | None = None) -> dict:
    from part.models import BomItem, Part

    settings = settings or load_settings()
    cache = OrderCache(settings)
    rows = []
    for part in Part.objects.filter(assembly=True, active=True).order_by('name'):
        graph, parts = build_graph(part, settings)
        if not graph.has_bom(part.pk):
            continue
        n, shortages = cap.limiting_parts(graph, part.pk)
        cost = rolled_cost(part, settings, cache)
        try:
            native = int(part.can_build)
        except Exception:
            native = None
        rows.append({
            **_part_brief(part, None),
            'in_stock': d2(graph.stock.get(part.pk, ZERO)),
            'native_can_build': native,
            'multi_level': n,
            'limiting': [{'name': parts[pk].full_name, 'part': pk, 'missing': d2(q)} for pk, q in shortages.items()][:3],
            'unit_cost': cost.get('unit_cost'),
            'unit_cost_complete': cost.get('complete'),
            'used_in_other_assemblies': BomItem.objects.filter(sub_part=part).exists(),
        })
    return {'assemblies': rows}
