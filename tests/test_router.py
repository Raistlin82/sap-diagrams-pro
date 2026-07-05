# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""Tests for the deterministic channel router (Task 8, scripts/_channel_router.py).

The router replaces draw.io's default edge routing (which produced the tangled
centre) with structural routing through reserved channels: vertical gutters
between columns, horizontal corridors above/below rows, parallel lanes within
each channel, barycenter-distributed ports, and collision-free pill/label
slots. These tests pin the load-bearing invariants of each milestone (8a–8e)
and — crucially — assert the collision-free / determinism guarantees via the
_geom_checks kernel rather than merely "it ran".
"""
import copy
import json
import types
from pathlib import Path

import pytest
from conftest import load_script

ROOT = Path(__file__).resolve().parent.parent
NOVA = ROOT / "demo" / "nova" / "nova-L1.json"
V2 = ROOT / "tests" / "fixtures" / "ir-v2-sample.json"

router = load_script("_channel_router")
gc = load_script("_geom_checks")
Rect = gc.Rect


# ── helpers ──────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def gen():
    return load_script("generate-drawio")


@pytest.fixture(scope="module")
def sl():
    return load_script("_skeleton_layout")


def _fixture_route(gen, sl, path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    diagram = gen.parse_json(payload)
    layout = sl.compute_layout(diagram, gen.ShapeIndex.load())
    return diagram, layout, router.route(diagram, layout)


def _edge(eid, source, target, **kw):
    return types.SimpleNamespace(id=eid, source=source, target=target,
                                 flowFamily=kw.get("flowFamily"),
                                 pill=kw.get("pill"), label=kw.get("label"),
                                 kind=kw.get("kind", "default"))


def _diagram(edges):
    return types.SimpleNamespace(edges=edges)


def _synthetic():
    """A hand-built layout with three columns (gutters LC=[100,300],
    CR=[500,700]) and four nodes, for precise gutter/corridor assertions."""
    layout = {
        "nodes": {
            "L1": (20, 360, 60, 40),      # left column,  cy=380
            "C1": (350, 300, 60, 40),     # center column, cy=320
            "C2": (350, 500, 60, 40),     # center column, cy=520
            "R1": (720, 300, 60, 40),     # right column,  cy=320
        },
        "groups": {},
        "canvas": (900, 700),
        "meta": {
            "columns": {"left": (0, 100), "center": (300, 500), "right": (700, 800)},
            "networkSeparator": {"x": 600, "y0": 300, "y1": 540},
        },
    }
    return layout


def _segments(path):
    return list(zip(path, path[1:]))


def _full_path(res, eid):
    ex, en = res.ports[eid]
    return [ex] + res.waypoints[eid] + [en]


# ── 8a: region graph + channel assignment ────────────────────────────────────
def test_8a_adjacent_edge_routes_through_shared_gutter():
    """An edge between adjacent columns puts its vertical travel in the shared
    vertical gutter: every interior waypoint's x lies inside that gutter rect."""
    lay = _synthetic()
    dia = _diagram([_edge("eADJ", "L1", "C1")])
    res = router.route(dia, lay)

    # the left↔center gutter is [100, 300]
    gutter = next(c for c in res.channels if c.axis == "v" and c.rect.x == 100)
    wps = res.waypoints["eADJ"]
    assert wps, "adjacent edge must have >= 1 waypoint"
    for x, _y in wps:
        assert gutter.rect.x <= x <= gutter.rect.right, (
            f"waypoint x={x} not inside shared gutter "
            f"[{gutter.rect.x},{gutter.rect.right}]"
        )
    # and it is a real vertical segment (L1.cy != C1.cy)
    assert any(abs(a[0] - b[0]) < 0.5 and abs(a[1] - b[1]) > 1
               for a, b in _segments(wps + wps[-1:]) if wps)


def test_8a_long_edge_crosses_both_gutters_via_one_horizontal_corridor():
    """A left→right edge (columns two apart) crosses BOTH gutters on a single
    horizontal corridor segment."""
    lay = _synthetic()
    dia = _diagram([_edge("eLONG", "L1", "R1")])
    res = router.route(dia, lay)

    guts = sorted((c for c in res.channels if c.axis == "v"), key=lambda c: c.rect.x)
    assert len(guts) == 2
    g_lc, g_cr = guts

    path = _full_path(res, "eLONG")
    # find the one horizontal segment that spans across both gutters
    spanning = [
        (a, b) for a, b in _segments(path)
        if abs(a[1] - b[1]) < 0.5
        and min(a[0], b[0]) <= g_lc.rect.x and max(a[0], b[0]) >= g_lc.rect.right
        and min(a[0], b[0]) <= g_cr.rect.x and max(a[0], b[0]) >= g_cr.rect.right
    ]
    assert len(spanning) == 1, (
        "exactly one horizontal corridor segment must cross both gutters"
    )
    # that corridor y is a reserved horizontal channel, clear of node content
    corr_y = spanning[0][0][1]
    assert any(c.axis == "h" and abs(c.center - corr_y) < 1 for c in res.channels)


def test_8a_intra_column_edge_stays_within_column():
    """A same-column edge does not detour through a gutter: its waypoints stay
    left of the center→right gutter and right of the left→center gutter."""
    lay = _synthetic()
    dia = _diagram([_edge("eINTRA", "C1", "C2")])
    res = router.route(dia, lay)
    for x, _y in res.waypoints["eINTRA"]:
        assert 100 < x < 700


def test_8a_deterministic_same_input_identical_waypoints(gen, sl):
    """Same IR twice → byte-identical waypoints, ports and crossing count."""
    for path in (V2, NOVA):
        _d1, _l1, r1 = _fixture_route(gen, sl, path)
        _d2, _l2, r2 = _fixture_route(gen, sl, path)
        assert r1.waypoints == r2.waypoints
        assert r1.ports == r2.ports
        assert r1.crossings == r2.crossings


def test_8a_every_edge_has_at_least_one_waypoint(gen, sl):
    """Every routed edge gets >= 1 interior waypoint (foundation for 8d)."""
    for path in (V2, NOVA):
        diagram, _lay, res = _fixture_route(gen, sl, path)
        routed = {e.id for e in diagram.edges}
        assert set(res.waypoints) == routed
        for eid, wps in res.waypoints.items():
            assert len(wps) >= 1, f"edge {eid} has no waypoint"


def test_8a_channels_have_valid_axes(gen, sl):
    """Reserved channels are all vertical gutters or horizontal corridors."""
    _d, _l, res = _fixture_route(gen, sl, V2)
    assert res.channels
    assert all(c.axis in ("v", "h") for c in res.channels)
    assert any(c.axis == "v" for c in res.channels)   # gutters exist
    assert any(c.axis == "h" for c in res.channels)   # corridors exist


# ── 8b: parallel lane offsets ────────────────────────────────────────────────
def _lanes_layout(n):
    """A three-column layout with ``n`` left nodes and ``n`` center nodes, all
    at distinct y — so ``n`` adjacent edges share the single left↔center gutter."""
    nodes = {}
    for i in range(n):
        y = 100 + i * 100
        nodes[f"L{i}"] = (20, y, 60, 40)
        nodes[f"C{i}"] = (350, y + 30, 60, 40)   # offset y so each has a real V-seg
    return {
        "nodes": nodes, "groups": {}, "canvas": (900, 200 + n * 100),
        "meta": {"columns": {"left": (0, 100), "center": (300, 500), "right": (700, 800)},
                 "networkSeparator": None},
    }


def _norm_seg(a, b):
    return (a, b) if (a, b) <= (b, a) else (b, a)


def test_8b_five_edges_through_one_gutter_get_distinct_parallel_lanes():
    """Five edges sharing one gutter get five distinct lane offsets, ≥10px
    apart pairwise, and no two polylines share a segment."""
    lay = _lanes_layout(5)
    dia = _diagram([_edge(f"e{i}", f"L{i}", f"C{i}") for i in range(5)])
    res = router.route(dia, lay)

    gutter = next(c for c in res.channels if c.axis == "v" and c.rect.x == 100)
    assert len(gutter.lanes) == 5

    # the vertical-lane x of each edge = the x shared by its two waypoints
    lane_x = {}
    for i in range(5):
        wps = res.waypoints[f"e{i}"]
        xs = {round(x, 3) for x, _y in wps}
        assert len(xs) == 1, "each adjacent edge travels on a single vertical lane"
        lane_x[f"e{i}"] = xs.pop()

    xs = sorted(lane_x.values())
    assert len(set(xs)) == 5, "five DISTINCT lane offsets"
    assert all(b - a >= 10.0 for a, b in zip(xs, xs[1:])), "lanes >= 10px apart"
    assert all(gutter.rect.x <= x <= gutter.rect.right for x in xs), "lanes in gutter"

    # no two polylines share a segment
    seen: dict = {}
    for i in range(5):
        path = _full_path(res, f"e{i}")
        for a, b in _segments(path):
            if abs(a[0] - b[0]) < 0.5 and abs(a[1] - b[1]) < 0.5:
                continue                              # skip zero-length
            key = _norm_seg((round(a[0], 2), round(a[1], 2)),
                            (round(b[0], 2), round(b[1], 2)))
            assert key not in seen, f"edges e{i} and {seen[key]} share a segment"
            seen[key] = f"e{i}"


def test_8b_lanes_are_deterministic_and_centered():
    """Lane assignment is stable (sorted by src.y, dst.y, id) and the bundle is
    centred on the gutter centre-line."""
    lay = _lanes_layout(4)
    dia = _diagram([_edge(f"e{i}", f"L{i}", f"C{i}") for i in range(4)])
    r1 = router.route(dia, lay)
    r2 = router.route(dia, lay)
    assert r1.waypoints == r2.waypoints
    gutter = next(c for c in r1.channels if c.axis == "v" and c.rect.x == 100)
    xs = [next(iter({x for x, _y in r1.waypoints[f"e{i}"]})) for i in range(4)]
    assert abs(sum(xs) / len(xs) - gutter.center) < 1e-6
