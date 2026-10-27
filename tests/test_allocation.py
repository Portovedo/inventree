"""Unit tests for the pure allocation maths (run with: python -m pytest tests)."""

from decimal import Decimal

import pytest

from landed_cost_capacity.allocation import (
    CATEGORY_DUTY,
    CATEGORY_EXCLUDED,
    CATEGORY_IMPORT_VAT,
    CATEGORY_SHIPPING,
    METHOD_MANUAL,
    METHOD_QUANTITY,
    METHOD_VALUE,
    FeeLine,
    GoodsLine,
    allocate,
    split_amount,
)

D = Decimal


def test_split_amount_sums_exactly():
    shares = split_amount(D('10'), [D(1), D(1), D(1)], places=2)
    assert sum(shares) == D('10')
    assert sorted(shares) == [D('3.33'), D('3.33'), D('3.34')]


def test_split_amount_rejects_zero_weights():
    with pytest.raises(ValueError):
        split_amount(D('5'), [D(0), D(0)])


def test_po0005_jlcpcb_real_data():
    """PO-0005: 10x PCBA @ 40.905, delivery 12.73 + customs 108.17 + other 94.10, prototype 9.31 excluded."""
    lines = [GoodsLine(id=13, quantity=D(10), unit_price=D('40.905'))]
    fees = [
        FeeLine(1, D('12.73'), CATEGORY_SHIPPING),
        FeeLine(2, D('108.17'), CATEGORY_DUTY),
        FeeLine(3, D('94.10'), 'other'),
        FeeLine(4, D('9.31'), CATEGORY_EXCLUDED),
    ]
    res = allocate(lines, fees)
    line = res.lines[13]
    assert line.fees == D('215.00')
    assert line.landed_unit_price_native == D('62.405')
    assert round(line.uplift_percent, 1) == D('52.6')
    assert res.fees_excluded == D('9.31')
    assert line.fees_by_category[CATEGORY_DUTY] == D('108.17')


def test_value_method_proportional():
    lines = [
        GoodsLine(1, D(10), D(10)),  # 100
        GoodsLine(2, D(1), D(300)),  # 300
    ]
    res = allocate(lines, [FeeLine(1, D(40), CATEGORY_SHIPPING)], METHOD_VALUE)
    assert res.lines[1].fees == D(10)
    assert res.lines[2].fees == D(30)
    assert res.lines[1].fee_per_native_unit == D(1)
    assert res.lines[2].landed_unit_price_native == D(330)


def test_quantity_method_uses_native_units():
    lines = [
        GoodsLine(1, D(1), D(38), pack_quantity=D(20)),  # 20 m reel
        GoodsLine(2, D(20), D('0.5')),  # 20 pcs
    ]
    res = allocate(lines, [FeeLine(1, D(4), CATEGORY_SHIPPING)], METHOD_QUANTITY)
    assert res.lines[1].fees == D(2)
    assert res.lines[2].fees == D(2)
    # 38 EUR / 20 m = 1.9 per m, + 0.1 fee per m
    assert res.lines[1].landed_unit_price_native == D('2.0')


def test_manual_weights_and_targets():
    lines = [GoodsLine(1, D(1), D(10), weight=D(3)), GoodsLine(2, D(1), D(10), weight=D(1)), GoodsLine(3, D(1), D(10))]
    fees = [FeeLine(1, D(8), CATEGORY_SHIPPING, targets=[1, 2])]
    res = allocate(lines, fees, METHOD_MANUAL)
    assert res.lines[1].fees == D(6)
    assert res.lines[2].fees == D(2)
    assert res.lines[3].fees == 0


def test_discount_reduces_value_and_unit_price():
    line = GoodsLine(1, D(10), D(10), discount=D(10))
    assert line.net_value == D(90)
    assert line.net_unit_price_native == D(9)


def test_import_vat_excluded_by_default_and_includable():
    lines = [GoodsLine(1, D(1), D(100))]
    fees = [FeeLine(1, D(23), CATEGORY_IMPORT_VAT)]
    assert allocate(lines, fees).lines[1].fees == 0
    inc = allocate(lines, fees, included_categories=(CATEGORY_IMPORT_VAT,))
    assert inc.lines[1].fees == D(23)


def test_fallback_when_no_prices():
    lines = [GoodsLine(1, D(2), None), GoodsLine(2, D(6), None)]
    res = allocate(lines, [FeeLine(7, D(8), CATEGORY_SHIPPING)], METHOD_VALUE)
    assert res.method_used[7] == METHOD_QUANTITY
    assert res.lines[1].fees == D(2)
    assert res.lines[1].landed_unit_price_native == D(1)


def test_unallocatable_fee_reported():
    res = allocate([GoodsLine(1, D(0), None)], [FeeLine(5, D(3), CATEGORY_SHIPPING)])
    assert 5 in res.unallocated
    assert res.fees_excluded == D(3)


def test_targets_pointing_to_unknown_lines_fall_back_to_all():
    lines = [GoodsLine(1, D(1), D(10)), GoodsLine(2, D(1), D(10))]
    res = allocate(lines, [FeeLine(1, D(2), CATEGORY_SHIPPING, targets=[999])])
    assert res.lines[1].fees == D(1) and res.lines[2].fees == D(1)
