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
import hashlib
import json
import types
import xml.etree.ElementTree as ET
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


# ── 8c: port distribution by barycenter ──────────────────────────────────────
def _entry_side_from_frac(frac):
    ex, ey = frac
    if ex == 0.0:
        return "L"
    if ex == 1.0:
        return "R"
    if ey == 0.0:
        return "T"
    if ey == 1.0:
        return "B"
    return None


def _side_faced_by_segment(last_wp, entry_pt):
    """Which target side an incoming segment lands on (its heading)."""
    dx = entry_pt[0] - last_wp[0]
    dy = entry_pt[1] - last_wp[1]
    if abs(dx) >= abs(dy):
        return "L" if dx > 0 else "R"      # heading right → hits left face
    return "T" if dy > 0 else "B"          # heading down  → hits top face


def test_8c_three_edges_from_one_side_get_distinct_ordered_fractions():
    """Three edges leaving one box's right side get distinct exitY fractions
    (evenly across [0.25,0.75]), ordered by their target's y."""
    layout = {
        "nodes": {
            "S": (20, 300, 60, 40),
            "T0": (350, 100, 60, 40),      # cy=120
            "T1": (350, 300, 60, 40),      # cy=320
            "T2": (350, 500, 60, 40),      # cy=520
        },
        "groups": {}, "canvas": (900, 700),
        "meta": {"columns": {"left": (0, 100), "center": (300, 500), "right": (700, 800)},
                 "networkSeparator": None},
    }
    dia = _diagram([_edge("e0", "S", "T0"), _edge("e1", "S", "T1"),
                    _edge("e2", "S", "T2")])
    res = router.route(dia, layout)

    # all three leave S's right side
    ex = {eid: res.port_fracs[eid][0] for eid in ("e0", "e1", "e2")}
    assert all(a[0] == 1.0 for a in ex.values()), "all exit the right side"
    exit_y = {eid: a[1] for eid, a in ex.items()}
    assert len(set(exit_y.values())) == 3, "distinct exitY fractions"
    assert set(round(v, 3) for v in exit_y.values()) == {0.25, 0.5, 0.75}
    # ordered by target y: T0(120) < T1(320) < T2(520)
    assert exit_y["e0"] < exit_y["e1"] < exit_y["e2"]


def test_8c_entry_side_faces_last_segment_direction():
    """Each edge's entry port sits on the side its final segment heads into."""
    for path in (V2, NOVA):
        payload = json.loads(path.read_text(encoding="utf-8"))
        diagram = load_script("generate-drawio").parse_json(payload)
        sl = load_script("_skeleton_layout")
        layout = sl.compute_layout(diagram, load_script("generate-drawio").ShapeIndex.load())
        res = router.route(diagram, layout)
        for e in diagram.edges:
            if e.id not in res.waypoints:
                continue
            last_wp = res.waypoints[e.id][-1]
            entry_pt = res.ports[e.id][1]
            faced = _side_faced_by_segment(last_wp, entry_pt)
            got = _entry_side_from_frac(res.port_fracs[e.id][1])
            assert got == faced, (
                f"{path.name} edge {e.id}: entry side {got} != faced {faced}"
            )


def test_8c_lane_keeps_clearance_from_network_separator(gen, sl):
    """No in-gutter vertical lane hugs the NETWORK separator bar within its
    y-range: every center↔right vertical segment stays >= a clearance off it."""
    diagram, layout, res = _fixture_route(gen, sl, NOVA)
    sep = layout["meta"]["networkSeparator"]
    assert sep is not None
    sx, y0, y1 = sep["x"], sep["y0"], sep["y1"]
    for eid, wps in res.waypoints.items():
        for (ax, ay), (bx, by) in _segments(wps):
            if abs(ax - bx) < 0.5 and abs(ax - sx) < 6.0:   # a vertical seg near the bar
                lo, hi = sorted((ay, by))
                if hi >= y0 and lo <= y1:                    # overlaps the bar's y-range
                    pytest.fail(f"edge {eid} vertical lane hugs the separator at x={ax}")


def _gutter_for(res, x0):
    return next(c for c in res.channels if c.axis == "v" and c.rect.x == x0)


def test_allocate_lanes_reconciles_clamp_against_separator_crossing():
    """Neither shipped fixture is dense enough to conflict the in-gutter
    clamp against SEP_CLEARANCE (see the comment in _allocate_lanes), so
    craft one: 5 edges through a 200px gutter with the separator close to
    its right edge. The right-edge clamp alone would pull the bundle back
    far enough to cross the separator (left-most lane at x=248 < sep_x=250)
    -- the reconciliation must fix that WITHOUT re-violating the gutter's
    far edge (a plain further shift alone would land max_x=298, past
    rect.right-4=296) -- it must also shrink the pitch so the bundle fits
    the space actually available between the two."""
    nodes = {}
    for i in range(5):
        y = 100 + i * 100
        nodes[f"L{i}"] = (20, y, 60, 40)
        nodes[f"C{i}"] = (350, y + 30, 60, 40)
    lay = {
        "nodes": nodes, "groups": {}, "canvas": (900, 700),
        "meta": {
            "columns": {"left": (0, 100), "center": (300, 500), "right": (700, 800)},
            "networkSeparator": {"x": 250, "y0": 90, "y1": 550},
        },
    }
    dia = _diagram([_edge(f"e{i}", f"L{i}", f"C{i}") for i in range(5)])
    res = router.route(dia, lay)
    gutter = _gutter_for(res, 100)

    lane_xs = sorted({round(x, 6) for wps in res.waypoints.values() for x, _y in wps})
    assert len(lane_xs) == 5, "still five distinct, collision-free lanes"
    assert min(lane_xs) >= 250.0 - 1e-9, (
        f"left-most lane at x={min(lane_xs)} crosses the separator at x=250"
    )
    assert max(lane_xs) <= gutter.rect.right - 4.0 + 1e-9, (
        f"right-most lane at x={max(lane_xs)} spills past the gutter's far "
        f"edge at x={gutter.rect.right - 4.0} -- into the next column"
    )


def test_allocate_lanes_never_spills_past_gutter_far_edge_dense_real_geometry():
    """The HARD invariant (never spill a lane past the gutter into the next
    column) must hold even when it can no longer coexist with SEP_CLEARANCE
    at all -- using the actual production geometry (ZONE_HGAP=96 gutter,
    separator at the gutter's exact centre, per _skeleton_layout.py) with
    six direct edges sharing one gutter (an ordinary count for an L1 SAP
    diagram, e.g. several services all pointing at one shared target) -- not
    a contrived extreme. A shift-only reconciliation fixes the near side by
    pushing the far side 12px past the gutter's edge; the pitch must shrink
    instead so the far edge is respected exactly, even giving up on
    SEP_CLEARANCE (and, in this dense a case, even on "never cross the
    separator" at all -- the softer of the two)."""
    # L{i}/C{i} deliberately sit in the CENTER/RIGHT columns (not left/center)
    # so the edge routes through the center<->right gutter -- the one that
    # carries the NETWORK separator in production (_skeleton_layout.py).
    x0, cw = 300.0, 200.0                 # the "center" column
    gx0 = x0 + cw                         # gutter starts at the center column's right edge
    gw = 96.0                             # ZONE_HGAP
    sep_x = gx0 + gw / 2.0                # separator at the gutter's exact centre
    nodes = {}
    for i in range(6):
        y = 100 + i * 100
        nodes[f"L{i}"] = (x0 + 20, y, 60, 40)              # center column, cx=350
        nodes[f"C{i}"] = (gx0 + gw + 20, y + 30, 60, 40)   # right column, cx=650
    lay = {
        "nodes": nodes, "groups": {}, "canvas": (1200, 900),
        "meta": {
            "columns": {"left": (0, 100), "center": (x0, x0 + cw),
                        "right": (gx0 + gw, gx0 + gw + 200)},
            "networkSeparator": {"x": sep_x, "y0": 90, "y1": 650},
        },
    }
    dia = _diagram([_edge(f"e{i}", f"L{i}", f"C{i}") for i in range(6)])
    res = router.route(dia, lay)
    gutter = _gutter_for(res, gx0)
    assert gutter.rect.w == gw

    lane_xs = sorted({round(x, 6) for wps in res.waypoints.values() for x, _y in wps})
    assert len(lane_xs) == 6, "still six distinct, collision-free lanes"
    assert all(b - a >= 10.0 - 1e-9 for a, b in zip(lane_xs, lane_xs[1:])), (
        "lanes must never drop below the 10px legibility floor"
    )
    assert max(lane_xs) <= gutter.rect.right - 4.0 + 1e-9, (
        f"right-most lane at x={max(lane_xs)} spills past the gutter's far "
        f"edge at x={gutter.rect.right - 4.0} -- into the RIGHT column's content"
    )
    assert min(lane_xs) >= gutter.rect.x + 4.0 - 1e-9, (
        "left-most lane must not spill past the gutter's near edge either"
    )


# ── 8d: waypoint emission into the .drawio ────────────────────────────────────
def _stable(prefix, key):
    return f"{prefix}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:8]}"


def _cells_by_id(root):
    return {c.get("id"): c for c in root.iter("mxCell") if c.get("id")}


def _abs_topleft(cell_id, cells, _seen=None):
    """Absolute (x, y) of a cell, walking the parent chain (like the layout /
    validator do): a cell's mxGeometry is relative to its non-layer parent."""
    _seen = _seen or set()
    if cell_id in _seen or cell_id not in cells:
        return 0.0, 0.0
    _seen.add(cell_id)
    cell = cells[cell_id]
    geom = cell.find("mxGeometry")
    if geom is None:
        return 0.0, 0.0
    x = float(geom.get("x", "0") or 0)
    y = float(geom.get("y", "0") or 0)
    parent = cell.get("parent")
    if parent and parent not in ("0", "1"):
        px, py = _abs_topleft(parent, cells, _seen)
        x, y = x + px, y + py
    return x, y


def _node_abs_from_drawio(root, cells, diagram):
    """Reconstruct {node_id: (x,y,w,h)} from the emitted file — exactly the
    drawn geometry the emitter fed the router (node_abs_geom)."""
    out = {}
    for n in diagram.nodes:
        cid = _stable("n", n.id)
        cell = cells.get(cid)
        if cell is None:
            continue
        geom = cell.find("mxGeometry")
        if geom is None:
            continue
        x, y = _abs_topleft(cid, cells)
        out[n.id] = (x, y, float(geom.get("width", "0")), float(geom.get("height", "0")))
    return out


def _parse_style(style):
    out = {}
    for chunk in (style or "").split(";"):
        if "=" in chunk:
            k, _, v = chunk.partition("=")
            out[k.strip()] = v.strip()
    return out


@pytest.mark.parametrize("path", [V2, NOVA], ids=["ir-v2", "nova"])
def test_8d_every_edge_has_array_points_matching_router_and_anchors(gen, sl, path):
    """Every emitted edge carries an <Array as="points"> whose mxPoints match
    the router output (±1px, reconstructed from the drawn geometry) plus
    exitX/exitY/entryX/entryY in its style."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    diagram = gen.parse_json(payload)
    layout = sl.compute_layout(diagram, gen.ShapeIndex.load())
    xml = gen.emit(diagram, layout="auto")
    root = ET.fromstring(xml)
    cells = _cells_by_id(root)

    router_layout = dict(layout)
    router_layout["nodes"] = _node_abs_from_drawio(root, cells, diagram)
    res = router.route(diagram, router_layout)

    for e in diagram.edges:
        cid = _stable("e", e.id)
        cell = cells.get(cid)
        assert cell is not None, f"edge {e.id} not emitted"
        arr = cell.find("./mxGeometry/Array[@as='points']")
        assert arr is not None, f"edge {e.id} has no <Array as='points'>"
        pts = [(float(p.get("x")), float(p.get("y"))) for p in arr.findall("mxPoint")]
        assert len(pts) >= 1, f"edge {e.id} has no mxPoint"

        expected = res.waypoints[e.id]
        assert len(pts) == len(expected), f"edge {e.id} waypoint count mismatch"
        for (px, py), (ex, ey) in zip(pts, expected):
            assert abs(px - ex) <= 1.0 and abs(py - ey) <= 1.0, (
                f"edge {e.id} mxPoint ({px},{py}) != router ({ex},{ey}) ±1px"
            )

        style = _parse_style(cell.get("style"))
        for k in ("exitX", "exitY", "entryX", "entryY"):
            assert k in style, f"edge {e.id} style missing {k}"


@pytest.mark.parametrize("path", [V2, NOVA], ids=["ir-v2", "nova"])
def test_8d_validate_drawio_zero_critical(gen, sl, tmp_path, path):
    """The generated .drawio passes validate-drawio.py with 0 CRITICAL."""
    validate = load_script("validate-drawio")
    payload = json.loads(path.read_text(encoding="utf-8"))
    diagram = gen.parse_json(payload)
    xml = gen.emit(diagram, layout="auto")
    out = tmp_path / f"{path.stem}.drawio"
    out.write_text(xml, encoding="utf-8")
    issues = validate.validate(out)
    critical = [i for i in issues if i.severity == "CRITICAL"]
    assert not critical, f"{path.name}: {[i.message for i in critical]}"


# ── 8e: collision-free pill & label slots ────────────────────────────────────
def _rect_at(center, dims):
    (cx, cy), (w, h) = center, dims
    return Rect(cx - w / 2, cy - h / 2, w, h)


@pytest.mark.parametrize("path", [V2, NOVA], ids=["ir-v2", "nova"])
def test_8e_pills_and_labels_are_collision_free(gen, sl, path):
    """No pill/label rect overlaps any node/box or any other pill/label rect,
    and no LABEL rect is crossed by a foreign edge segment — all checked with
    the _geom_checks kernel the router used."""
    diagram, layout, res = _fixture_route(gen, sl, path)
    node_rects = [Rect(*t) for t in layout["nodes"].values()]

    # collect every placed pill/label as (eid, kind, rect)
    placed = []
    for eid, c in res.pill_pos.items():
        e = next(x for x in diagram.edges if x.id == eid)
        placed.append((eid, "pill", _rect_at(c, router.pill_dims(e.pill))))
    for eid, c in res.label_pos.items():
        e = next(x for x in diagram.edges if x.id == eid)
        placed.append((eid, "label", _rect_at(c, router.label_dims(e.label))))
    assert placed, "fixture must exercise some pills/labels"

    # 1. no pill/label overlaps any node box
    for eid, kind, r in placed:
        for nr in node_rects:
            assert not gc.rects_overlap(r, nr), f"{kind} {eid} overlaps a node"

    # 2. no pill/label overlaps another pill/label
    for i in range(len(placed)):
        for j in range(i + 1, len(placed)):
            assert not gc.rects_overlap(placed[i][2], placed[j][2]), (
                f"{placed[i][1]} {placed[i][0]} overlaps {placed[j][1]} {placed[j][0]}"
            )

    # 3. no LABEL rect is crossed by a FOREIGN edge segment
    segs_by_edge = {eid: _segments(_full_path(res, eid)) for eid in res.waypoints}
    for eid, kind, r in placed:
        if kind != "label":
            continue
        for other, segs in segs_by_edge.items():
            if other == eid:
                continue
            for a, b in segs:
                assert not gc.seg_intersects_rect(a, b, r), (
                    f"label {eid} crossed by foreign edge {other}"
                )


def test_8e_pill_starts_from_longest_segment_midpoint():
    """With space available, a lone edge's pill lands on its longest segment's
    midpoint (the base slot, no shift needed)."""
    lay = _synthetic()
    dia = _diagram([_edge("e1", "L1", "C1", pill="SCIM")])
    res = router.route(dia, lay)
    path = _full_path(res, "e1")
    a, b = router._longest_segment(path)
    mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    px, py = res.pill_pos["e1"]
    assert abs(px - mid[0]) < 1e-6 and abs(py - mid[1]) < 1e-6


def test_8e_pill_and_label_on_same_edge_do_not_overlap():
    """An edge carrying BOTH a pill and a label gets two non-overlapping slots."""
    lay = _synthetic()
    dia = _diagram([_edge("e1", "L1", "C1", pill="SCIM", label="Provision users")])
    res = router.route(dia, lay)
    pr = _rect_at(res.pill_pos["e1"], router.pill_dims("SCIM"))
    lr = _rect_at(res.label_pos["e1"], router.label_dims("Provision users"))
    assert not gc.rects_overlap(pr, lr)


def test_8e_deterministic_slots(gen, sl):
    """Pill/label slots are byte-identical across runs."""
    for path in (V2, NOVA):
        _d1, _l1, r1 = _fixture_route(gen, sl, path)
        _d2, _l2, r2 = _fixture_route(gen, sl, path)
        assert r1.pill_pos == r2.pill_pos
        assert r1.label_pos == r2.label_pos


# ── review round: the plan / assign_ports_lanes / build_waypoints seam ───────
# route() used to be monolithic (plan -> ports -> lanes -> waypoints inline),
# which meant Task 9's greedy lane/port reordering search could only fork the
# internals. These tests pin: (1) the composed default path is
# byte-identical to route()'s own output (the refactor must not change
# today's diagrams), and (2) a caller-supplied lane_order/port_order (the
# lever Task 9 needs) deterministically changes the result.
def test_seam_route_equals_reduce_assign_build_composition(gen, sl):
    """route() IS the composition plan() -> reduce_crossings() ->
    assign_ports_lanes(with those winners) -> build_waypoints(), not a parallel
    implementation that could drift from it. Reproducing it by hand on both
    real fixtures must yield route()'s exact output -- the seam Task 9 (and
    Task 12/13) drive stays load-bearing."""
    for path in (V2, NOVA):
        diagram, layout, expected = _fixture_route(gen, sl, path)

        plans = router.plan(diagram, layout)
        lane_order, port_order = router.reduce_crossings(plans, layout)
        port_fracs, lane_offsets = router.assign_ports_lanes(
            plans, layout, lane_order=lane_order, port_order=port_order)
        waypoints, pill_pos, label_pos, crossings, slot_fallbacks = (
            router.build_waypoints(plans, port_fracs, lane_offsets, layout)
        )

        assert waypoints == expected.waypoints
        assert port_fracs == expected.port_fracs
        assert pill_pos == expected.pill_pos
        assert label_pos == expected.label_pos
        assert crossings == expected.crossings
        assert slot_fallbacks == expected.slot_fallbacks == []


def test_seam_default_composition_is_deterministic_and_reducible(gen, sl):
    """The default (None lane_order/port_order) composition is still callable
    and deterministic, and reduce_crossings never INCREASES the crossing count
    versus that default -- the crossing-reduction lever only ever helps."""
    for path in (V2, NOVA):
        diagram, layout, _expected = _fixture_route(gen, sl, path)
        plans = router.plan(diagram, layout)

        pf_def, lo_def = router.assign_ports_lanes(plans, layout)
        wp_def, *_rest = router.build_waypoints(plans, pf_def, lo_def, layout)
        wp_def2, *_rest2 = router.build_waypoints(plans, pf_def, lo_def, layout)
        assert wp_def == wp_def2                       # deterministic default path

        lane_order, port_order = router.reduce_crossings(plans, layout)
        pf, lo = router.assign_ports_lanes(
            plans, layout, lane_order=lane_order, port_order=port_order)
        _wp, _pp, _lp, crossings, _fb = router.build_waypoints(plans, pf, lo, layout)
        _wp0, _pp0, _lp0, crossings_def, _fb0 = router.build_waypoints(
            plans, pf_def, lo_def, layout)
        assert crossings <= crossings_def


def test_seam_plan_exposes_channels_for_route_result(gen, sl):
    """plan()'s return carries the SAME Channel objects route() republishes
    as RouteResult.channels (not a second, un-mutated rebuild) -- the lever
    a reordering search needs to inspect a channel's final lane order."""
    diagram, layout, expected = _fixture_route(gen, sl, NOVA)
    plans = router.plan(diagram, layout)
    assert plans.channels                                  # non-empty
    assert [c.id for c in plans.channels] == [c.id for c in expected.channels]


def test_seam_custom_lane_order_changes_waypoints_deterministically():
    """A lane_order hook that reverses each channel's default-sorted group
    (Task 9's crossing-reduction search reorders lanes this way) must yield
    DIFFERENT waypoints from the default path, and the SAME waypoints again
    on a repeat call -- deterministic, not merely different."""
    lay = _lanes_layout(5)
    dia = _diagram([_edge(f"e{i}", f"L{i}", f"C{i}") for i in range(5)])
    plans = router.plan(dia, lay)

    port_fracs, lane_default = router.assign_ports_lanes(plans, lay)
    wp_default, *_ = router.build_waypoints(plans, port_fracs, lane_default, lay)

    def reversed_order(group):
        return list(reversed(group))

    _, lane_reversed = router.assign_ports_lanes(plans, lay, lane_order=reversed_order)
    wp_reversed, *_rest = router.build_waypoints(plans, port_fracs, lane_reversed, lay)
    assert wp_reversed != wp_default, "reversing lane order must change waypoints"

    # determinism: same custom order, same input -> byte-identical output
    _, lane_reversed_2 = router.assign_ports_lanes(plans, lay, lane_order=reversed_order)
    wp_reversed_2, *_rest2 = router.build_waypoints(plans, port_fracs, lane_reversed_2, lay)
    assert wp_reversed_2 == wp_reversed

    # and the DEFAULT path is unaffected by having exercised a custom one
    port_fracs_again, lane_default_again = router.assign_ports_lanes(plans, lay)
    wp_default_again, *_rest3 = router.build_waypoints(
        plans, port_fracs_again, lane_default_again, lay)
    assert wp_default_again == wp_default


def test_seam_custom_port_order_changes_port_fracs_deterministically():
    """Symmetric to the lane_order test above, for the per-side port groups
    lever: a port_order hook that reverses a 3-way exit group changes
    port_fracs, deterministically, without touching the default path."""
    layout = {
        "nodes": {
            "S": (20, 300, 60, 40),
            "T0": (350, 100, 60, 40),
            "T1": (350, 300, 60, 40),
            "T2": (350, 500, 60, 40),
        },
        "groups": {}, "canvas": (900, 700),
        "meta": {"columns": {"left": (0, 100), "center": (300, 500), "right": (700, 800)},
                 "networkSeparator": None},
    }
    dia = _diagram([_edge("e0", "S", "T0"), _edge("e1", "S", "T1"),
                    _edge("e2", "S", "T2")])
    plans = router.plan(dia, layout)
    default_fracs, _ = router.assign_ports_lanes(plans, layout)

    def reversed_order(group):
        return list(reversed(group))

    reordered_fracs, _ = router.assign_ports_lanes(plans, layout, port_order=reversed_order)
    assert reordered_fracs != default_fracs

    reordered_fracs_2, _ = router.assign_ports_lanes(plans, layout, port_order=reversed_order)
    assert reordered_fracs_2 == reordered_fracs

    default_fracs_again, _ = router.assign_ports_lanes(plans, layout)
    assert default_fracs_again == default_fracs


def test_seam_port_groups_introspection_matches_default_ordering():
    """port_groups() (the read-only lever for a custom port_order) exposes
    the SAME per-side grouping/order _assign_ports uses by default."""
    layout = {
        "nodes": {
            "S": (20, 300, 60, 40),
            "T0": (350, 100, 60, 40),
            "T1": (350, 300, 60, 40),
            "T2": (350, 500, 60, 40),
        },
        "groups": {}, "canvas": (900, 700),
        "meta": {"columns": {"left": (0, 100), "center": (300, 500), "right": (700, 800)},
                 "networkSeparator": None},
    }
    dia = _diagram([_edge("e0", "S", "T0"), _edge("e1", "S", "T1"),
                    _edge("e2", "S", "T2")])
    plans = router.plan(dia, layout)
    exit_groups, _entry_groups = router.port_groups(plans)
    group = exit_groups[("S", "R")]
    assert [p.eid for p in group] == ["e0", "e1", "e2"]      # T0(y120) < T1 < T2


# ── review round: slot-exhaustion signal (RouteResult.slot_fallbacks) ────────
def test_slot_fallbacks_empty_on_real_fixtures(gen, sl):
    """Neither shipped fixture exhausts the pill/label slot scan window."""
    for path in (V2, NOVA):
        _d, _l, res = _fixture_route(gen, sl, path)
        assert res.slot_fallbacks == []


def test_place_in_slots_reports_exhaustion():
    """_place_in_slots itself must report placed_ok=False -- not silently
    succeed -- when every candidate in its scan window collides with an
    obstacle; it still returns a usable (if possibly colliding) fallback
    position (the segment midpoint) so callers always have something to
    render."""
    seg = ((200.0, 320.0), (350.0, 320.0))
    dims = router.pill_dims("SCIM")
    giant = Rect(0.0, -50.0, 500.0, 750.0)          # blankets the whole scan grid
    center, rect, ok = router._place_in_slots(seg, dims, [giant], [])
    assert ok is False
    assert center == (275.0, 320.0)                 # unchecked segment midpoint


def test_slot_fallbacks_flags_edge_in_crafted_dense_case():
    """End-to-end wiring: when a pill's slot scan is exhausted, route()
    surfaces the edge id in RouteResult.slot_fallbacks rather than accepting
    the fallback position silently like an ordinary collision-free slot."""
    lay = _synthetic()
    lay = dict(lay)
    lay["nodes"] = dict(lay["nodes"])
    # A wall-sized "node" blankets e1's pill scan grid (its longest segment
    # runs (200,320)-(350,320); see test_8e_pill_starts_from_longest_
    # segment_midpoint for the same geometry) without moving the gutter's
    # centre-line (a "v" channel's centre is x-only, from meta.columns).
    lay["nodes"]["WALL"] = (0.0, -50.0, 500.0, 750.0)
    dia = _diagram([_edge("e1", "L1", "C1", pill="SCIM")])
    res = router.route(dia, lay)
    assert res.slot_fallbacks == ["e1"]
    assert "e1" in res.pill_pos                     # still placed (fallback position)


# ── review round: _channel_router_module() reuses an already-loaded module ──
def test_channel_router_module_reuses_already_loaded_module(gen):
    """generate-drawio's lazy _channel_router loader must check sys.modules
    FIRST (the same guarded pattern _load_sibling / conftest.load_script
    use) instead of unconditionally exec'ing a second copy -- which would
    clobber sys.modules["_channel_router"] and leave two non-identical
    Channel/RouteResult classes alive in the same process."""
    import sys as _sys
    canonical = load_script("_channel_router")
    assert _sys.modules.get("_channel_router") is canonical

    saved = gen._CHANNEL_ROUTER_MOD
    gen._CHANNEL_ROUTER_MOD = None            # force _channel_router_module() to re-check
    try:
        assert gen._channel_router_module() is canonical
    finally:
        gen._CHANNEL_ROUTER_MOD = saved       # don't leak state into other tests


# ── Task 9A: greedy crossing reduction via lane/port reorder ─────────────────
def _reducible_x_layout():
    """A crafted 4-edge 'X': four edges L{i}->C{i} sharing the single
    left<->center gutter, whose DEFAULT (barycenter, by src.y) lane order
    leaves 6 segment/segment crossings but a reordering exists that leaves 1.
    (src/dst y's found by search — see the task's 'crafted X case'.)"""
    ys_src = [420, 80, 120, 320]
    ys_dst = [500, 480, 80, 460]
    nodes = {}
    edges = []
    for i in range(4):
        nodes[f"L{i}"] = (20, ys_src[i], 60, 40)
        nodes[f"C{i}"] = (350, ys_dst[i], 60, 40)
        edges.append(_edge(f"e{i}", f"L{i}", f"C{i}"))
    layout = {
        "nodes": nodes, "groups": {}, "canvas": (900, 700),
        "meta": {"columns": {"left": (0, 100), "center": (300, 500), "right": (700, 800)},
                 "networkSeparator": None},
    }
    return layout, _diagram(edges)


def _default_crossings(diagram, layout):
    """Crossings of the DEFAULT (no reorder) channel routing — the baseline the
    greedy improves on."""
    plans = router.plan(diagram, layout)
    pf, lo = router.assign_ports_lanes(plans, layout)
    paths = {}
    for p in plans:
        efr, nfr = pf[p.eid]
        ex = router._abs_port(p.src, efr)
        en = router._abs_port(p.dst, nfr)
        paths[p.eid] = [ex] + router._build_waypoints(p, ex, en, lo.get(p.eid, 0.0)) + [en]
    return router._count_crossings(paths)


def test_9a_greedy_reduces_crafted_x_crossings_to_at_most_one():
    """The crafted 4-edge X starts with 6 crossings in the default order;
    route() (which runs reduce_crossings) brings it down to <= 1 -- the
    deterministic greedy reorder pass genuinely minimises crossings, not just
    'runs'."""
    layout, diagram = _reducible_x_layout()
    assert _default_crossings(diagram, layout) == 6      # documents the baseline
    res = router.route(diagram, layout)
    assert res.crossings <= 1, f"greedy left {res.crossings} crossings (want <= 1)"


def test_9a_reduce_crossings_is_deterministic_and_a_true_permutation():
    """reduce_crossings returns hooks that (a) reduce the crafted-X crossings
    identically on a repeat call and (b) are TRUE permutations -- feeding them
    to assign_ports_lanes never drops or invents an edge (all 4 lane offsets
    present)."""
    layout, diagram = _reducible_x_layout()
    plans = router.plan(diagram, layout)
    lane1, port1 = router.reduce_crossings(plans, layout)
    lane2, port2 = router.reduce_crossings(plans, layout)
    # deterministic: apply each and compare the resulting lane offsets
    pf1, lo1 = router.assign_ports_lanes(plans, layout, lane_order=lane1, port_order=port1)
    pf2, lo2 = router.assign_ports_lanes(plans, layout, lane_order=lane2, port_order=port2)
    assert lo1 == lo2 and pf1 == pf2
    assert set(lo1) == {f"e{i}" for i in range(4)}       # every edge kept a lane


def test_9a_reduce_crossings_declines_reorder_that_would_break_a_slot(gen, sl):
    """On nova-L1 a naive-crossing-optimal PORT reshuffle would shove an edge's
    label into a slot that can't be placed collision-free; reduce_crossings's
    final (slot_fallbacks, crossings) accept/reject guard declines it, so
    route() keeps every pill/label collision-free (no slot fallback) -- Task
    8's invariant is not sacrificed for a crossing."""
    _d, _l, res = _fixture_route(gen, sl, NOVA)
    assert res.slot_fallbacks == []

