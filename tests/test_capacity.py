"""Unit tests for the pure multi-level capacity maths."""

from decimal import Decimal

import pytest

from landed_cost_capacity.capacity import Graph, Line, direct_headroom, limiting_parts, max_buildable, simulate

D = Decimal

# Part ids modelled on the WADA Logger structure
DEVICE, INT_CONN, EXT_CABLE, BATTERY = 1, 2, 3, 4
SOCKET, JST, SHRINK, PLUG, CABLE_M, CELL, EHR, CONTACT, BOX = 10, 11, 12, 13, 14, 15, 16, 17, 18


def wada_graph(stock=None):
    lines = {
        DEVICE: [
            Line(DEVICE, INT_CONN, D(2)),
            Line(DEVICE, EXT_CABLE, D(2)),
            Line(DEVICE, BATTERY, D(1)),
            Line(DEVICE, BOX, D(1)),
        ],
        INT_CONN: [Line(INT_CONN, SOCKET, D(1)), Line(INT_CONN, JST, D(1)), Line(INT_CONN, SHRINK, D('0.09'))],
        EXT_CABLE: [Line(EXT_CABLE, CABLE_M, D(1)), Line(EXT_CABLE, PLUG, D(1)), Line(EXT_CABLE, SHRINK, D('0.06'))],
        BATTERY: [Line(BATTERY, CELL, D(1)), Line(BATTERY, EHR, D(1)), Line(BATTERY, CONTACT, D(2))],
    }
    base = {
        INT_CONN: 30, EXT_CABLE: 3, BATTERY: 15, BOX: 11,
        SOCKET: 0, JST: 0, SHRINK: D('3.2'), PLUG: 27, CABLE_M: 17, CELL: 0, EHR: 85, CONTACT: 170,
    }
    base.update(stock or {})
    return Graph(lines=lines, stock={k: D(v) for k, v in base.items()}, whole_units={DEVICE, INT_CONN, EXT_CABLE, BATTERY})


def test_single_level_would_say_1_but_multilevel_builds_more():
    g = wada_graph()
    # Direct stock: 3 external cables -> only 1 device (needs 2 each)
    head = {h.line.child: h.can_build for h in direct_headroom(g, DEVICE)}
    assert head[EXT_CABLE] == 1
    # Multi-level: external cables can be built from 27 plugs / 17 m cable / 3.2 m shrink.
    # 11 boxes cap it at 11; cables: need 2n - 3 built -> limited by cable metres 17 => 2n-3 <= 17 => n <= 10
    assert max_buildable(g, DEVICE) == 10


def test_shared_component_pool_is_respected():
    # Shrink sleeve is shared by internal and external sub-assemblies.
    g = wada_graph({INT_CONN: 0, SOCKET: 100, JST: 100, SHRINK: D('1.0'), BOX: 100, CABLE_M: 100, PLUG: 100})
    # Each device built fully from scratch needs 2*0.09 + 2*0.06 = 0.30 m shrink, but 3 ext cables in stock.
    # n devices: int conn shrink 0.18n, ext cables (2n-3)*0.06 -> 0.30n - 0.18 <= 1.0 -> n <= 3.93
    assert max_buildable(g, DEVICE) == 3
    n, short = limiting_parts(g, DEVICE)
    assert n == 3 and SHRINK in short


def test_whole_units_for_subassemblies():
    lines = {1: [Line(1, 2, D('0.5'))], 2: [Line(2, 3, D(1))]}
    g = Graph(lines=lines, stock={3: D(1)}, whole_units={1, 2})
    # 1 unit of top needs 0.5 of sub-assembly 2, but sub-assemblies are built whole -> needs 1 of part 3
    sim = simulate(g, 1, 1)
    assert sim.to_build[2] == 1 and sim.feasible
    assert max_buildable(g, 1) == 2  # 2 tops need exactly 1 sub-assembly


def test_alternatives_used_after_primary():
    lines = {1: [Line(1, 2, D(1), alternatives=[3])]}
    g = Graph(lines=lines, stock={2: D(2), 3: D(5)}, whole_units={1})
    assert max_buildable(g, 1) == 7
    sim = simulate(g, 1, 4)
    assert sim.from_stock[2] == 2 and sim.from_alternatives[2][3] == 2


def test_setup_attrition_rounding():
    line = Line(1, 2, D(2), attrition=D(10), setup_quantity=D(3), rounding_multiple=D(5))
    # 2*4 = 8 -> *1.1 = 8.8 -> +3 = 11.8 -> round up to 15
    assert line.required(D(4)) == D(15)
    g = Graph(lines={1: [line]}, stock={2: D(15)}, whole_units={1})
    assert max_buildable(g, 1) == 5  # 2*5*1.1+3 = 14 -> 15


def test_no_bom_and_cycles():
    assert max_buildable(Graph(lines={}, stock={}), 1) == 0
    g = Graph(lines={1: [Line(1, 2, D(1))], 2: [Line(2, 1, D(1))]}, stock={})
    with pytest.raises(ValueError):
        g.topo_order(1)


def test_expand_disabled_matches_single_level():
    g = wada_graph()
    assert max_buildable(g, DEVICE, expand_subassemblies=False) == 1


def test_plan_shortages_for_target_quantity():
    g = wada_graph()
    sim = simulate(g, DEVICE, 20)
    assert not sim.feasible
    assert sim.shortages[BOX] == 9
    assert sim.to_build[EXT_CABLE] == 37  # 40 needed - 3 in stock
    assert sim.shortages[CABLE_M] == 20  # 37 m needed - 17 m
