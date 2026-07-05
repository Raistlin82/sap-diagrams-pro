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
    """A tier_box grown taller than its contract height reflows its badge row to
    the true bottom edge — not floating at the contract reference height."""
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
        assert b["y"] == pytest.approx(grown_h - 42.0), \
            "badge row must sit at final_h-42, i.e. bottom-anchored to the grown frame"


# ─────────────────────────────────────────────────────────────────────────────
# meta shape (consumed by the Task 8 channel router) + moved atoms.
# ─────────────────────────────────────────────────────────────────────────────
def test_meta_shape(gen, sl):
    lay = _compute(gen, sl, V2)
    meta = lay["meta"]
    assert set(meta["slots"]) == set(sl.SLOTS)
    # every top-level group appears in exactly one slot
    placed = [gid for ids in meta["slots"].values() for gid in ids]
    top_level = [g.id for g in gen.parse_json(json.loads(V2.read_text())).groups if not g.parent]
    assert sorted(placed) == sorted(top_level)
    # lanes carry flow-ordered node ids per group; ranks cover every node
    assert all(isinstance(v, list) for v in meta["lanes"].values())
    assert set(meta["ranks"]) == {n.id for n in gen.parse_json(json.loads(V2.read_text())).nodes}
    assert meta["identity"] == ["identity"]


def test_return_dict_shape(gen, sl):
    lay = _compute(gen, sl, NOVA)
    assert set(lay) == {"groups", "nodes", "edges", "canvas", "meta"}
    assert lay["edges"] == {}  # routing is Task 8's job
    assert len(lay["canvas"]) == 2


def test_icon_size_moved(sl):
    assert sl.icon_size("L1") == 48
    assert sl.icon_size("L2") == 32
