"""Pure landed-cost allocation maths (no Django / InvenTree imports).

A purchase order has goods lines and extra (fee) lines. Each fee line is spread
across a subset of goods lines ("targets") using an allocation method:

- ``value``    : proportional to the net line value (qty x unit price x (1 - discount))
- ``quantity`` : proportional to the line quantity in native (stock) units
- ``manual``   : proportional to user supplied weights per line

All arithmetic uses Decimal. Allocated amounts are rounded to ``places`` decimals
and any rounding remainder is assigned to the largest share, so the allocated
total always equals the fee total exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

METHOD_VALUE = 'value'
METHOD_QUANTITY = 'quantity'
METHOD_MANUAL = 'manual'
METHODS = (METHOD_VALUE, METHOD_QUANTITY, METHOD_MANUAL)

CATEGORY_SHIPPING = 'shipping'
CATEGORY_DUTY = 'duty'
CATEGORY_IMPORT_VAT = 'import_vat'
CATEGORY_OTHER = 'other'
CATEGORY_EXCLUDED = 'excluded'
CATEGORIES = (
    CATEGORY_SHIPPING,
    CATEGORY_DUTY,
    CATEGORY_IMPORT_VAT,
    CATEGORY_OTHER,
    CATEGORY_EXCLUDED,
)

ZERO = Decimal(0)


def D(value) -> Decimal:
    """Convert a value (int, float, str, Decimal, None) to Decimal."""
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@dataclass
class GoodsLine:
    """A goods line of a purchase order (all money in order currency)."""

    id: int
    quantity: Decimal  # ordered quantity, in supplier pack units
    unit_price: Decimal | None  # price per supplier pack unit
    pack_quantity: Decimal = Decimal(1)  # native units per pack unit
    discount: Decimal = ZERO  # percent
    weight: Decimal | None = None  # manual weight (METHOD_MANUAL)

    @property
    def native_quantity(self) -> Decimal:
        return D(self.quantity) * D(self.pack_quantity or 1)

    @property
    def net_value(self) -> Decimal:
        if self.unit_price is None:
            return ZERO
        return D(self.quantity) * D(self.unit_price) * (1 - D(self.discount) / 100)

    @property
    def net_unit_price_native(self) -> Decimal | None:
        """Net price per native (stock) unit."""
        if self.unit_price is None:
            return None
        pack = D(self.pack_quantity or 1)
        if pack == 0:
            return None
        return D(self.unit_price) * (1 - D(self.discount) / 100) / pack


@dataclass
class FeeLine:
    """An extra line (fee) of a purchase order (amount in order currency)."""

    id: int
    amount: Decimal  # total fee amount (qty x price x (1 - discount))
    category: str = CATEGORY_OTHER
    targets: list[int] | None = None  # goods line ids; None / empty = all lines
    reference: str = ''


@dataclass
class LineResult:
    id: int
    net_value: Decimal
    native_quantity: Decimal
    net_unit_price_native: Decimal | None
    fees: Decimal = ZERO  # total fees allocated to this line
    fees_by_category: dict[str, Decimal] = field(default_factory=dict)

    @property
    def fee_per_native_unit(self) -> Decimal:
        if self.native_quantity == 0:
            return ZERO
        return self.fees / self.native_quantity

    @property
    def landed_unit_price_native(self) -> Decimal | None:
        if self.net_unit_price_native is None:
            if self.native_quantity == 0:
                return None
            return self.fee_per_native_unit
        return self.net_unit_price_native + self.fee_per_native_unit

    @property
    def uplift_percent(self) -> Decimal | None:
        if not self.net_value:
            return None
        return self.fees / self.net_value * 100


@dataclass
class AllocationResult:
    lines: dict[int, LineResult]
    goods_total: Decimal
    fees_included: Decimal
    fees_excluded: Decimal
    unallocated: dict[int, str]  # fee id -> reason it could not be allocated
    method_used: dict[int, str]  # fee id -> method actually used

    @property
    def uplift_percent(self) -> Decimal | None:
        if not self.goods_total:
            return None
        allocated = sum((line.fees for line in self.lines.values()), ZERO)
        return allocated / self.goods_total * 100


def _weights(lines: list[GoodsLine], method: str) -> list[Decimal]:
    if method == METHOD_VALUE:
        return [line.net_value for line in lines]
    if method == METHOD_QUANTITY:
        return [line.native_quantity for line in lines]
    if method == METHOD_MANUAL:
        return [D(line.weight) for line in lines]
    raise ValueError(f'Unknown allocation method: {method}')


def split_amount(amount: Decimal, weights: list[Decimal], places: int = 6) -> list[Decimal]:
    """Split ``amount`` proportionally to ``weights``; rounded shares sum exactly to amount."""
    total = sum(weights, ZERO)
    if total <= 0:
        raise ValueError('Weights must sum to a positive number')
    quant = Decimal(1).scaleb(-places)
    shares = [(amount * w / total).quantize(quant, rounding=ROUND_HALF_UP) for w in weights]
    remainder = amount - sum(shares, ZERO)
    if remainder:
        largest = max(range(len(weights)), key=lambda i: weights[i])
        shares[largest] += remainder
    return shares


def allocate(
    lines: list[GoodsLine],
    fees: list[FeeLine],
    method: str = METHOD_VALUE,
    included_categories: tuple[str, ...] = (CATEGORY_SHIPPING, CATEGORY_DUTY, CATEGORY_OTHER),
    places: int = 6,
) -> AllocationResult:
    """Allocate included fees across goods lines.

    If the chosen method yields zero total weight for a fee's targets (e.g. value
    method with no prices, manual method with no weights), the allocation falls
    back to ``value`` and then ``quantity``.
    """
    if method not in METHODS:
        raise ValueError(f'Unknown allocation method: {method}')

    results = {
        line.id: LineResult(
            id=line.id,
            net_value=line.net_value,
            native_quantity=line.native_quantity,
            net_unit_price_native=line.net_unit_price_native,
        )
        for line in lines
    }
    by_id = {line.id: line for line in lines}
    unallocated: dict[int, str] = {}
    method_used: dict[int, str] = {}
    fees_included = ZERO
    fees_excluded = ZERO

    for fee in fees:
        amount = D(fee.amount)
        if fee.category not in included_categories:
            fees_excluded += amount
            continue
        if amount == 0:
            continue

        target_ids = [t for t in (fee.targets or []) if t in by_id] or list(by_id)
        targets = [by_id[t] for t in target_ids]
        if not targets:
            unallocated[fee.id] = 'Order has no goods lines'
            fees_excluded += amount
            continue

        chain = [method] + [m for m in (METHOD_VALUE, METHOD_QUANTITY) if m != method]
        for m in chain:
            weights = _weights(targets, m)
            if sum(weights, ZERO) > 0:
                break
        else:
            unallocated[fee.id] = 'Target lines have no value or quantity'
            fees_excluded += amount
            continue

        method_used[fee.id] = m
        fees_included += amount
        for target, share in zip(targets, split_amount(amount, weights, places)):
            res = results[target.id]
            res.fees += share
            res.fees_by_category[fee.category] = res.fees_by_category.get(fee.category, ZERO) + share

    return AllocationResult(
        lines=results,
        goods_total=sum((line.net_value for line in lines), ZERO),
        fees_included=fees_included,
        fees_excluded=fees_excluded,
        unallocated=unallocated,
        method_used=method_used,
    )
