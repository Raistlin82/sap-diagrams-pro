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
