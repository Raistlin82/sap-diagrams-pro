# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""Tests for the skeleton slot layout + flow ordering (Task 6).

The skeleton engine replaces the zone engine: it assigns top-level groups to
slots (left / top / center / right / bottom), sizes molecule frames to contain
their contents (footprint-driven, reconciling Task 5's molecules), and orders
each lane's nodes by flow rank. These tests pin the load-bearing behaviours:
deterministic columns, flow ordering, nested-subaccount containment, the
identity-placement rule, the governance top band, and the tier-box reflow.
"""
import copy
import json
from pathlib import Path

import pytest

from conftest import load_script

ROOT = Path(__file__).resolve().parent.parent
NOVA = ROOT / "demo" / "nova" / "nova-L1.json"
V2 = ROOT / "tests" / "fixtures" / "ir-v2-sample.json"

PERSONAS = ("ap-clerk", "approver", "auditor")


@pytest.fixture(scope="module")
def gen():
    return load_script("generate-drawio")


@pytest.fixture(scope="module")
def sl():
    return load_script("_skeleton_layout")


def _compute(gen, sl, path, mutate=None):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if mutate:
        payload = mutate(copy.deepcopy(payload))
    diagram = gen.parse_json(payload)
    return sl.compute_layout(diagram, gen.ShapeIndex.load())


def _box(lay, gid):
    return lay["groups"][gid]  # (x, y, w, h)


def _x_right(lay, nid):
    x, _y, w, _h = lay["nodes"][nid]
    return x + w


def _contains(inner, outer, pad=1):
    ix, iy, iw, ih = inner
    ox, oy, ow, oh = outer
    return (ix >= ox - pad and iy >= oy - pad
            and ix + iw <= ox + ow + pad and iy + ih <= oy + oh + pad)


# ─────────────────────────────────────────────────────────────────────────────
# Core assertions from the plan.
# ─────────────────────────────────────────────────────────────────────────────
def test_columns_nova(gen, sl):
    """nova-L1: personas LEFT of BTP, backend apps RIGHT of BTP; deterministic."""
    lay1 = _compute(gen, sl, NOVA)
    lay2 = _compute(gen, sl, NOVA)
    assert lay1 == lay2  # byte-identical re-run (no datetime/random)

    btp = _box(lay1, "btp")
    assert max(_x_right(lay1, n) for n in PERSONAS) < btp[0]
    sap_cloud = _box(lay1, "sap-cloud")
    assert sap_cloud[0] > btp[0] + btp[2]


def test_flow_order_in_lane(gen, sl):
    """Within the BTP lane the flow rank sets reading order: is (feeds cap) sits
    left of cap. is → cap comes from e4 (is→cap); cap is deeper in the DAG."""
    lay = _compute(gen, sl, NOVA)
    n = lay["nodes"]
    assert n["is"][0] < n["cap"][0]
    # the lane order is exposed for the router, ranked before edge-less nodes
    order = lay["meta"]["lanes"]["btp"]
    assert order.index("is") < order.index("cap")


def test_flow_order_ranks_chain(gen, sl):
    """A clean a→b→c chain in one lane ranks 0/1/2 and packs in that x-order."""
    ir = {
        "metadata": {"title": "chain", "level": "L1"},
        "groups": [{"id": "g", "type": "btp-layer", "label": "G", "position": "center", "flow": "row"}],
        "nodes": [
            {"id": "c", "label": "C", "group": "g"},
            {"id": "a", "label": "A", "group": "g"},
            {"id": "b", "label": "B", "group": "g"},
        ],
        "edges": [
            {"id": "e1", "source": "a", "target": "b", "style": "solid"},
            {"id": "e2", "source": "b", "target": "c", "style": "solid"},
        ],
    }
    lay = sl.compute_layout(gen.parse_json(ir), gen.ShapeIndex.load())
    assert lay["meta"]["ranks"] == {"a": 0, "b": 1, "c": 2}
    assert lay["meta"]["lanes"]["g"] == ["a", "b", "c"]
    n = lay["nodes"]
    assert n["a"][0] < n["b"][0] < n["c"][0]


def test_edgeless_nodes_trail_ranked(gen, sl):
    """Edge-less nodes keep IR order AFTER the flow-connected ones."""
    ir = {
        "metadata": {"title": "trail", "level": "L1"},
        "groups": [{"id": "g", "type": "btp-layer", "label": "G", "position": "center"}],
        "nodes": [
            {"id": "lonely", "label": "Lonely", "group": "g"},
            {"id": "src", "label": "Src", "group": "g"},
            {"id": "dst", "label": "Dst", "group": "g"},
        ],
        "edges": [{"id": "e1", "source": "src", "target": "dst", "style": "solid"}],
    }
    lay = sl.compute_layout(gen.parse_json(ir), gen.ShapeIndex.load())
    assert lay["meta"]["lanes"]["g"] == ["src", "dst", "lonely"]


def test_nested_subaccounts(gen, sl):
    """v2 fixture: production frame fully inside test frame; both inside BTP."""
    lay = _compute(gen, sl, V2)
    prod = _box(lay, "subaccount-production")
    test = _box(lay, "subaccount-test")
    btp = _box(lay, "btp")
    assert _contains(prod, test), f"prod {prod} not inside test {test}"
    assert _contains(test, btp), f"test {test} not inside btp {btp}"
    assert _contains(prod, btp)


def test_identity_slot_by_parent(gen, sl):
    """Identity group: TOP-LEVEL ⇒ below the BTP frame, same column; PARENTED to
    BTP ⇒ its bbox sits inside the BTP frame (bottom band)."""
    # top-level (as authored in the fixture)
    lay = _compute(gen, sl, V2)
    ident = _box(lay, "identity")
    btp = _box(lay, "btp")
    assert ident[1] >= btp[1] + btp[3], "top-level identity must be BELOW btp"
    # same column ⇒ horizontal centres align (both centred in the center column)
    assert abs((ident[0] + ident[2] / 2) - (btp[0] + btp[2] / 2)) <= 1

    # parented to btp ⇒ contained inside the btp frame
    def _reparent(p):
        for g in p["groups"]:
            if g["id"] == "identity":
                g["parent"] = "btp"
                g.pop("position", None)
        return p

    lay2 = _compute(gen, sl, V2, mutate=_reparent)
    ident2 = _box(lay2, "identity")
    btp2 = _box(lay2, "btp")
    assert _contains(ident2, btp2), f"parented identity {ident2} not inside btp {btp2}"


def test_identity_techid_marker_matches_shape_index_techid(gen, sl):
    """The identity-group detection relies on matching the resolved service's
    techId against ``_IDENTITY_TECHID_MARKERS``. The SVG re-harvest gave
    Identity Provisioning the clean techId "32072-identity-provisioning_sd"
    (the old data misspelled it "...identity-provisoning"; both spellings stay
    in the marker tuple for back-compat). Uses the "IP" alias (not a literal
    "identity provisioning" substring) so a hit can ONLY come through the
    techId path, not the (already-working) service-name marker path."""
    from types import SimpleNamespace as NS
    shape_index = gen.ShapeIndex.load()
    resolved = shape_index.resolve("IP")
    assert resolved and "identity-provisioning" in resolved["techId"].lower(), \
        "fixture assumption: 'IP' resolves to the Identity Provisioning entry"
    node = NS(id="n", service="IP")
    assert sl._is_identity_group(NS(id="g"), {"g": [node]}, shape_index)


def test_governance_above_center(gen, sl):
    """A governance group is banded across the TOP, above the center column."""
    lay = _compute(gen, sl, V2)
    gov = _box(lay, "governance")
    btp = _box(lay, "btp")
    assert gov[1] < btp[1]
    assert lay["meta"]["slots"]["top"] == ["governance"]


# ─────────────────────────────────────────────────────────────────────────────
# Footprint-driven sizing (the Task-5 reconciliation) + no molecule overlap.
# ─────────────────────────────────────────────────────────────────────────────
def _overlap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def test_molecule_frames_contain_children(gen, sl):
    """Every molecule frame is sized to hold its child nodes — the whole point of
    Task 6 (icon-sized footprints used to let product boxes overflow)."""
    payload = json.loads(V2.read_text(encoding="utf-8"))
    diagram = gen.parse_json(payload)
    lay = sl.compute_layout(diagram, gen.ShapeIndex.load())
    node_group = {n.id: n.group for n in diagram.nodes}
    for nid, geo in lay["nodes"].items():
        g = node_group.get(nid)
        if g in lay["groups"]:
            assert _contains(geo, lay["groups"][g]), \
                f"node {nid} {geo} overflows its frame {g} {lay['groups'][g]}"


def test_no_top_level_overlap(gen, sl):
    """Top-level slot frames must not overlap (acceptance the plan hinges on)."""
    for path in (NOVA, V2):
        payload = json.loads(path.read_text(encoding="utf-8"))
        diagram = gen.parse_json(payload)
        lay = sl.compute_layout(diagram, gen.ShapeIndex.load())
        tl = [g.id for g in diagram.groups if not g.parent and g.id in lay["groups"]]
        for i in range(len(tl)):
            for j in range(i + 1, len(tl)):
                assert not _overlap(lay["groups"][tl[i]], lay["groups"][tl[j]]), \
                    f"{path.name}: {tl[i]} overlaps {tl[j]}"


def test_product_footprint_exceeds_icon(gen, sl):
    """A product node reserves its real (large) molecule box, not an icon cell."""
    lay = _compute(gen, sl, V2)
    _x, _y, w, h = lay["nodes"]["bpa"]
    assert w > 300 and h > 150  # product box, far bigger than a 48px icon


def test_tier_box_reflow_keeps_badges_at_bottom(gen, sl):
    """A tier_box grown taller than its contract height reflows its badge row
    so it stays fully INSIDE the frame (badge bottom edge never past the
    frame's own bottom border) and anchored to the bottom band, not floating
    mid-box. (Review fix: this used to assert the buggy fixed offset
    ``grown_h - 42.0``, which put a 55px-tall hyperscaler badge's bottom edge
    ~13px PAST the frame border — enshrining the bug instead of catching it.)
    """
    M = load_script("_molecules")
    contract = M.load_contract()
    from types import SimpleNamespace as NS
    g = NS(id="t", label="Private", kind="private",
           badges={"hyperscalers": ["azure"], "runtimes": ["cloud-foundry"]})
    grown_h = 260.0
    cells = M.tier_box(g, contract, size=(240.0, grown_h))
    assert (cells[0]["w"], cells[0]["h"]) == (240.0, grown_h)
    badges = [c for c in cells if c["id"].startswith("badge-")]
    assert badges, "expected badge slots"
    for b in badges:
        badge_bottom = b["y"] + b["h"]
        assert badge_bottom <= grown_h, \
            f"badge {b['id']!r} bottom {badge_bottom} overflows the grown frame ({grown_h})"
        assert b["y"] > grown_h / 2, \
            f"badge {b['id']!r} must sit in the bottom half of the frame"
    # The row is top-aligned at a shared y (shorter badges, e.g. the 32px
    # runtime badge, sit above their own bottom edge within that shared row —
    # that's unrelated pre-existing row layout, not this fix) — so "hugs the
    # bottom band" is a property of the row's overall bottom (its TALLEST
    # badge), not each individual badge.
    row_bottom = max(b["y"] + b["h"] for b in badges)
    assert grown_h - row_bottom <= 20, \
        f"badge row must hug the bottom band, not float mid-box " \
        f"(gap to frame bottom = {grown_h - row_bottom})"


def test_tier_box_badges_stay_inside_layout_computed_frame(gen, sl):
    """Integration: run the real skeleton layout on ir-v2-sample, take the
    FINAL frame size ``compute_layout`` assigns the fixture's
    ``cloud-tier-right`` group (which carries a hyperscaler + a runtime
    badge), and render that group's tier_box at that exact size — the same
    two-step ``generate-drawio.py`` performs at emit time. The emitted badge
    cells must sit fully inside their frame (badge bottom ≤ frame bottom):
    proof that frame_insets' RESERVE and tier_box's DRAW offset agree end to
    end, not just in the synthetic unit test above."""
    M = load_script("_molecules")
    contract = M.load_contract()
    payload = json.loads(V2.read_text(encoding="utf-8"))
    diagram = gen.parse_json(payload)
    lay = sl.compute_layout(diagram, gen.ShapeIndex.load())
    gid = "cloud-tier-right"
    _x, _y, box_w, box_h = lay["groups"][gid]
    group = next(g for g in diagram.groups if g.id == gid)
    assert group.badges, "fixture must carry badges to exercise the reflow"
    cells = M.tier_box(group, contract, size=(box_w, box_h))
    badges = [c for c in cells if c["id"].startswith("badge-")]
    assert badges, "expected badge slots for the fixture's hyperscaler/runtime badges"
    for b in badges:
        assert b["y"] + b["h"] <= box_h, (
            f"badge {b['id']!r} bottom {b['y'] + b['h']} overflows the frame "
            f"computed by compute_layout (h={box_h})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Cyclic / self group-parent must not silently drop groups (FIX-3).
# ─────────────────────────────────────────────────────────────────────────────
def test_cyclic_group_parent_falls_back_to_top_level(gen, sl, capsys):
    """A 2-node parent cycle (cycle-a.parent=cycle-b, cycle-b.parent=cycle-a)
    must not silently vanish: compute_layout warns on stderr AND still places
    both groups (and their nodes) — at top level, as a fallback — instead of
    dropping them because neither is reachable from a real (parent=None)
    root."""
    ir = {
        "metadata": {"title": "cycle", "level": "L1"},
        "groups": [
            {"id": "cycle-a", "type": "btp-layer", "label": "A", "parent": "cycle-b"},
            {"id": "cycle-b", "type": "btp-layer", "label": "B", "parent": "cycle-a"},
        ],
        "nodes": [
            {"id": "na", "label": "NA", "group": "cycle-a"},
            {"id": "nb", "label": "NB", "group": "cycle-b"},
        ],
        "edges": [],
    }
    lay = sl.compute_layout(gen.parse_json(ir), gen.ShapeIndex.load())

    # Nothing silently disappeared: both groups and both nodes ARE placed.
    assert "cycle-a" in lay["groups"] and "cycle-b" in lay["groups"]
    assert "na" in lay["nodes"] and "nb" in lay["nodes"]
    assert not _overlap(lay["groups"]["cycle-a"], lay["groups"]["cycle-b"])

    stderr = capsys.readouterr().err
    assert "cyclic/unreachable parent" in stderr
    assert "cycle-a" in stderr
    assert "cycle-b" in stderr


# ─────────────────────────────────────────────────────────────────────────────
# meta shape (consumed by the Task 8 channel router) + moved atoms.
# ─────────────────────────────────────────────────────────────────────────────
def test_meta_shape(gen, sl):
    lay = _compute(gen, sl, V2)
    meta = lay["meta"]
    diagram = gen.parse_json(json.loads(V2.read_text()))
    all_group_ids = {g.id for g in diagram.groups}

    assert set(meta["slots"]) == set(sl.SLOTS)
    # every top-level group appears in exactly one slot
    placed = [gid for ids in meta["slots"].values() for gid in ids]
    top_level = [g.id for g in diagram.groups if not g.parent]
    assert sorted(placed) == sorted(top_level)

    # slot_of is the reverse of slots: exactly the top-level groups, each
    # mapped to the one slot it was placed into.
    assert set(meta["slot_of"]) == set(top_level)
    for gid in top_level:
        assert gid in meta["slots"][meta["slot_of"][gid]]

    # lanes carry flow-ordered node ids per group, and now cover EVERY group —
    # a node-less CONTAINER (e.g. "btp", which only nests subaccounts, and
    # "subaccount-test", which only nests subaccount-production) gets an
    # empty list rather than being absent.
    assert all(isinstance(v, list) for v in meta["lanes"].values())
    assert set(meta["lanes"]) == all_group_ids
    assert meta["lanes"]["btp"] == []
    assert meta["lanes"]["subaccount-test"] == []
    assert meta["lanes"]["subaccount-production"], "leaf frame must keep its real lane"

    assert set(meta["ranks"]) == {n.id for n in diagram.nodes}
    assert meta["identity"] == ["identity"]

    # columns: x-extent per column, ordered left-to-right with no overlap —
    # the shared source of truth for Task 7's NETWORK separator / Task 8's
    # router.
    assert set(meta["columns"]) == {"left", "center", "right"}
    for name, (x0, x1) in meta["columns"].items():
        assert x0 <= x1, f"column {name!r} has a backwards extent {(x0, x1)}"
    assert meta["columns"]["left"][1] <= meta["columns"]["center"][0]
    assert meta["columns"]["center"][1] <= meta["columns"]["right"][0]


def test_return_dict_shape(gen, sl):
    lay = _compute(gen, sl, NOVA)
    assert set(lay) == {"groups", "nodes", "edges", "canvas", "meta"}
    assert lay["edges"] == {}  # routing is Task 8's job
    assert len(lay["canvas"]) == 2


def test_icon_size_moved(sl):
    assert sl.icon_size("L1") == 48
    assert sl.icon_size("L2") == 32


# ─────────────────────────────────────────────────────────────────────────────
# NETWORK separator (Task 7): a vertical bar in the center→right gutter, spanning
# the right stack, emitted only when the right column holds ≥1 group.
# ─────────────────────────────────────────────────────────────────────────────
def test_network_separator_sits_in_center_right_gutter(gen, sl):
    """The separator x is the midpoint of the center→right gutter, strictly
    between the center column's right edge and the right column's left edge."""
    lay = _compute(gen, sl, V2)
    sep = lay["meta"]["networkSeparator"]
    assert sep is not None, "v2 fixture has a right stack ⇒ separator expected"
    cols = lay["meta"]["columns"]
    assert cols["center"][1] < sep["x"] < cols["right"][0]
    # midpoint of the gutter (single source of truth = meta.columns)
    assert sep["x"] == int(round((cols["center"][1] + cols["right"][0]) / 2))


def test_network_separator_spans_the_right_stack(gen, sl):
    """The bar's y-range covers the right column's own vertical extent: the min
    top and max bottom of every group placed in the right column."""
    lay = _compute(gen, sl, V2)
    sep = lay["meta"]["networkSeparator"]
    right_ids = lay["meta"]["slots"]["right"]
    assert right_ids, "fixture must have right-column groups"
    tops = [lay["groups"][g][1] for g in right_ids]
    bots = [lay["groups"][g][1] + lay["groups"][g][3] for g in right_ids]
    assert abs(sep["y0"] - min(tops)) <= 2
    assert abs(sep["y1"] - max(bots)) <= 2
    assert sep["y0"] < sep["y1"]


def test_network_separator_opt_out_via_metadata(gen, sl):
    """metadata.networkSeparator == false removes the separator (default on)."""
    def _off(p):
        p.setdefault("metadata", {})["networkSeparator"] = False
        return p
    lay = _compute(gen, sl, V2, mutate=_off)
    assert lay["meta"]["networkSeparator"] is None


def test_network_separator_absent_without_right_column(gen, sl):
    """No group in the right column ⇒ no separator (nothing to separate from)."""
    ir = {
        "metadata": {"title": "no-right", "level": "L1"},
        "groups": [
            {"id": "u", "type": "user", "label": "Users", "position": "left"},
            {"id": "btp", "type": "btp-layer", "label": "BTP", "position": "center"},
        ],
        "nodes": [
            {"id": "p", "label": "Person", "group": "u", "genericIcon": "user"},
            {"id": "svc", "label": "Svc", "group": "btp"},
        ],
        "edges": [],
    }
    lay = sl.compute_layout(gen.parse_json(ir), gen.ShapeIndex.load())
    assert lay["meta"]["slots"]["right"] == []
    assert lay["meta"]["networkSeparator"] is None


def test_network_separator_present_for_nova_backends(gen, sl):
    """nova-L1 has SAP-app / ops backends in the right column ⇒ separator on."""
    lay = _compute(gen, sl, NOVA)
    assert lay["meta"]["slots"]["right"], "nova must have a right stack"
    assert lay["meta"]["networkSeparator"] is not None


def test_network_separator_deterministic(gen, sl):
    lay1 = _compute(gen, sl, V2)
    lay2 = _compute(gen, sl, V2)
    assert lay1["meta"]["networkSeparator"] == lay2["meta"]["networkSeparator"]


# ─────────────────────────────────────────────────────────────────────────────
# FIX-2 (review): guard-rail — a frame's top-band title must never overlap its
# packed children. The invariant (min(child.y) >= title.y + title.h) holds by
# construction because frame_insets reserves pad_top >= the title band, but
# nothing tested it: a future TITLE_H / contract padTop bump could silently
# reintroduce the overlap. Exercised END TO END per frame type (build the frame
# via compute_layout, draw the title via the molecule builder at the SAME size),
# most importantly the subaccount-with-chip case (chip + title share the header).
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("gid,group,builder,show_chip", [
    ("sa", {"id": "sa", "type": "subaccount",
            "label": "A Very Long Subaccount Name That Would Overlap Its Content",
            "position": "center"}, "subaccount_frame", True),
    ("gov", {"id": "gov", "type": "governance",
             "label": "A Very Long Governance Band Title That Would Overlap",
             "position": "top"}, "governance_strip", None),
    ("tier", {"id": "tier", "type": "cloud-tier", "kind": "public",
              "label": "A Very Long Cloud Tier Title That Would Overlap",
              "position": "right"}, "tier_box", None),
    ("ca", {"id": "ca", "type": "custom-app",
            "label": "A Very Long Custom App Title That Would Overlap Content",
            "position": "right"}, "custom_app_box", None),
])
def test_frame_title_never_overlaps_children(gen, sl, gid, group, builder, show_chip):
    M = load_script("_molecules")
    contract = M.load_contract()
    ir = {"metadata": {"title": "t", "level": "L1"}, "groups": [group],
          "nodes": [{"id": "n1", "label": "Node One", "group": gid, "service": "Event Mesh"}],
          "edges": []}
    diagram = gen.parse_json(ir)
    lay = sl.compute_layout(diagram, gen.ShapeIndex.load())
    _fx, fy, fw, fh = lay["groups"][gid]
    grp = next(g for g in diagram.groups if g.id == gid)
    fn = getattr(M, builder)
    cells = (fn(grp, contract, size=(fw, fh), show_chip=show_chip)
             if show_chip is not None else fn(grp, contract, size=(fw, fh)))
    title = [c for c in cells if c["id"] == "frame-title"][0]
    title_abs_bottom = fy + title["y"] + title["h"]
    min_child_y = min(lay["nodes"][n.id][1]
                      for n in diagram.nodes if n.group == gid)
    assert min_child_y >= title_abs_bottom, (
        f"{gid}: first child at y={min_child_y} overlaps the title band "
        f"(bottom {title_abs_bottom})")


# ─────────────────────────────────────────────────────────────────────────────
# FIX-4 (review, DRY): the label-advance constant lives once, in
# _molecules.CHAR_W; _skeleton_layout re-exports it (its _text_w uses the same
# value) so the frame-min estimate and the layout label-width estimate can't
# drift apart. No duplicated 6.6 literal.
# ─────────────────────────────────────────────────────────────────────────────
def test_char_width_centralized_in_molecules(sl):
    M = load_script("_molecules")
    assert M.CHAR_W == 6.6
    assert sl.CHAR_W == M.CHAR_W
    # both scale by the shared advance (mid-range string dodges the clamps)
    s = "x" * 15
    assert M._title_w(s) == pytest.approx(min(240.0, max(40.0, 15 * M.CHAR_W + 12.0)))
    assert sl._text_w(s) == pytest.approx(
        min(sl.TEXT_MAX, max(sl.TEXT_MIN, 15 * M.CHAR_W + 12)))


def test_governance_renders_as_full_width_ribbon(gen, sl):
    """A single top-level governance group spans the composition width — a ribbon
    across the top — instead of a content-sized box that misreads as a second
    SAP BTP account floating above the diagram (the RTI-diagram finding)."""
    ir = {
        "metadata": {"title": "gov", "level": "L1"},
        "groups": [
            {"id": "gov", "type": "governance", "label": "Governance", "position": "top"},
            {"id": "btp", "type": "btp-layer", "label": "SAP BTP", "position": "center"},
            {"id": "sys", "type": "sap-app", "label": "Backends", "position": "right"},
        ],
        "nodes": [
            {"id": "alm", "label": "Cloud ALM", "group": "gov", "service": "Cloud ALM"},
            {"id": "svc", "label": "Svc", "group": "btp"},
            {"id": "s4", "label": "S/4HANA", "group": "sys", "service": "SAP S/4HANA"},
        ],
        "edges": [],
    }
    lay = sl.compute_layout(gen.parse_json(ir), gen.ShapeIndex.load())
    gx, gy, gw, gh = lay["groups"]["gov"]
    bx, by, bw, bh = lay["groups"]["btp"]
    sx, sy, sw, sh = lay["groups"]["sys"]
    assert gx <= bx + 1, "ribbon starts at/left of the center frame"
    assert gx + gw >= sx + sw - 2, "ribbon reaches the right column's right edge"
    assert gw > bw, "ribbon is wider than the center BTP frame"
    assert gy + gh <= by, "ribbon is a top band, above the columns"
