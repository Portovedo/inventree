"""Pure multi-level build capacity maths (no Django / InvenTree imports).

The input is a BOM graph (assemblies -> lines -> component options) plus the
usable stock of every part. ``simulate(top, n)`` explodes a demand for ``n``
units of ``top`` through the BOM:

1. Parts are processed top-down in topological order, so every part has received
   the demand of *all* its parents before it is resolved.
2. A part's demand is served from its own usable stock first (except for the top
   assembly itself, which we want to *build*), then from the stock of allowed
   alternatives (variants / substitutes), then - for sub-assemblies - by building
   the remainder (whole units) from components.
3. Whatever cannot be served is a shortage.

``max_buildable`` finds the largest feasible ``n`` with an exponential + binary
search (feasibility is monotone in ``n``).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal

ZERO = Decimal(0)
MAX_SEARCH = 10_000_000


def D(value) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def ceil_d(value: Decimal) -> Decimal:
    return value.to_integral_value(rounding=ROUND_CEILING)


@dataclass
class Line:
    """A BOM line: ``child`` (plus alternatives) required to build ``parent``."""

    parent: int
    child: int
    quantity: Decimal
    alternatives: list[int] = field(default_factory=list)
    attrition: Decimal = ZERO  # percent
    setup_quantity: Decimal = ZERO
    rounding_multiple: Decimal | None = None
    reference: str = ''
    id: int | None = None

    def required(self, build_quantity: Decimal) -> Decimal:
        """Mirror of InvenTree BomItem.get_required_quantity()."""
        req = D(self.quantity) * D(build_quantity)
        if D(self.attrition) > 0:
            req *= 1 + D(self.attrition) / 100
        if D(self.setup_quantity) > 0 and build_quantity > 0:
            req += D(self.setup_quantity)
        if self.rounding_multiple and D(self.rounding_multiple) > 0:
            mult = D(self.rounding_multiple)
            req = ceil_d(req / mult) * mult
        return req


@dataclass
class Graph:
    lines: dict[int, list[Line]]  # parent part id -> BOM lines
    stock: dict[int, Decimal]  # part id -> usable stock
    whole_units: set[int] = field(default_factory=set)  # parts that must be built in whole units

    def has_bom(self, part: int) -> bool:
        return bool(self.lines.get(part))

    def topo_order(self, top: int) -> list[int]:
        """Parts reachable from ``top`` (including alternatives), parents before children."""
        reachable: set[int] = set()
        stack = [top]
        while stack:
            p = stack.pop()
            if p in reachable:
                continue
            reachable.add(p)
            for line in self.lines.get(p, []):
                stack.append(line.child)
                stack.extend(line.alternatives)

        indeg = dict.fromkeys(reachable, 0)
        for p in reachable:
            for line in self.lines.get(p, []):
                indeg[line.child] += 1
        order, queue = [], [p for p in reachable if indeg[p] == 0]
        while queue:
            p = queue.pop()
            order.append(p)
            for line in self.lines.get(p, []):
                indeg[line.child] -= 1
                if indeg[line.child] == 0:
                    queue.append(line.child)
        if len(order) != len(reachable):
            raise ValueError('BOM contains a circular reference')
        return order


@dataclass
class Simulation:
    top: int
    quantity: Decimal
    feasible: bool
    demand: dict[int, Decimal]  # total gross demand per part
    from_stock: dict[int, Decimal]  # served from the part's own stock
    from_alternatives: dict[int, dict[int, Decimal]]  # part -> {alternative: qty}
    to_build: dict[int, Decimal]  # assemblies to build (incl. top)
    shortages: dict[int, Decimal]  # part -> missing quantity


def simulate(graph: Graph, top: int, quantity, expand_subassemblies: bool = True) -> Simulation:
    quantity = D(quantity)
    order = graph.topo_order(top)
    pool = defaultdict(Decimal, {k: D(v) for k, v in graph.stock.items()})
    demand: dict[int, Decimal] = defaultdict(Decimal)
    alternatives: dict[int, list[int]] = defaultdict(list)
    from_stock: dict[int, Decimal] = defaultdict(Decimal)
    from_alt: dict[int, dict[int, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    to_build: dict[int, Decimal] = {}
    shortages: dict[int, Decimal] = {}

    demand[top] = quantity

    for part in order:
        remaining = demand.get(part, ZERO)
        if remaining <= 0:
            continue

        if part != top:
            use = min(pool[part], remaining)
            if use > 0:
                pool[part] -= use
                from_stock[part] += use
                remaining -= use
            for alt in alternatives.get(part, []):
                if remaining <= 0:
                    break
                use = min(pool[alt], remaining)
                if use > 0:
                    pool[alt] -= use
                    from_alt[part][alt] += use
                    remaining -= use

        if remaining <= 0:
            continue

        if graph.has_bom(part) and (part == top or expand_subassemblies):
            build = ceil_d(remaining) if part in graph.whole_units else remaining
            to_build[part] = build
            for line in graph.lines[part]:
                demand[line.child] += line.required(build)
                for alt in line.alternatives:
                    if alt not in alternatives[line.child] and alt != line.child:
                        alternatives[line.child].append(alt)
        else:
            shortages[part] = remaining

    return Simulation(
        top=top,
        quantity=quantity,
        feasible=not shortages,
        demand=dict(demand),
        from_stock=dict(from_stock),
        from_alternatives={k: dict(v) for k, v in from_alt.items()},
        to_build=to_build,
        shortages=shortages,
    )


def max_buildable(graph: Graph, top: int, expand_subassemblies: bool = True) -> int:
    """Largest whole number of ``top`` assemblies that current stock supports."""
    if not graph.has_bom(top):
        return 0

    def ok(n: int) -> bool:
        return simulate(graph, top, n, expand_subassemblies).feasible

    if not ok(1):
        return 0
    lo, hi = 1, 2
    while hi <= MAX_SEARCH and ok(hi):
        lo, hi = hi, hi * 2
    if hi > MAX_SEARCH:
        return lo
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo


@dataclass
class Headroom:
    line: Line
    available: Decimal  # own stock + alternatives (no sub-builds)
    per_unit: Decimal
    can_build: int  # single-level, this line only


def direct_headroom(graph: Graph, top: int) -> list[Headroom]:
    """Single-level view: how many ``top`` units each direct BOM line supports from stock."""
    rows = []
    for line in graph.lines.get(top, []):
        available = D(graph.stock.get(line.child)) + sum(
            (D(graph.stock.get(a)) for a in line.alternatives if a != line.child), ZERO
        )
        per_unit = line.required(Decimal(1))
        if D(line.quantity) <= 0:
            can = MAX_SEARCH
        else:
            # largest n with required(n) <= available
            lo, hi = 0, max(1, int(available / D(line.quantity)) + 2)
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if line.required(Decimal(mid)) <= available:
                    lo = mid
                else:
                    hi = mid
            can = lo if line.required(Decimal(hi)) > available else hi
        rows.append(Headroom(line=line, available=available, per_unit=per_unit, can_build=can))
    rows.sort(key=lambda r: r.can_build)
    return rows


def limiting_parts(graph: Graph, top: int, expand_subassemblies: bool = True) -> tuple[int, dict[int, Decimal]]:
    """Return (max buildable, shortages that block building one more unit)."""
    n = max_buildable(graph, top, expand_subassemblies)
    if n >= MAX_SEARCH:
        return n, {}
    sim = simulate(graph, top, n + 1, expand_subassemblies)
    return n, sim.shortages


__all__ = [
    'Graph',
    'Headroom',
    'Line',
    'Simulation',
    'direct_headroom',
    'limiting_parts',
    'max_buildable',
    'simulate',
]
