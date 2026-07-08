# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""Tests for Task 13 — the visual-rubric patch pipeline.

Two halves, mirroring the two deliverables:

  1. ``scripts/apply-rubric-patches.py`` — turns a findings JSON into
     ``diagram.layoutHints`` entries: validates the 7-op vocabulary,
     merges/dedupes idempotently, passes ``null`` (manual) patches through,
     and exits 2 (listing the vocabulary) on an unknown/malformed op.

  2. Engine consumption of ``layoutHints`` — a hint actually changes the
     output: ``order_override`` reorders siblings in ``_skeleton_layout``;
     ``channel_prefer`` pins an edge's channel through the FULL ``route()``
     pipeline (which hardcodes ``reduce_crossings``) — the seam composition.
"""
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest
from conftest import load_script

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "apply-rubric-patches.py"

arp = load_script("apply-rubric-patches")
router = load_script("_channel_router")
sl = load_script("_skeleton_layout")
gen = load_script("generate-drawio")


# ── Part 1: apply-rubric-patches.py ──────────────────────────────────────────
def _finding(patch, rule="r", loc="l"):
    return {"rule": rule, "location": loc, "patch": patch}


def test_apply_adds_hints_to_layouthints():
    ir = {"metadata": {"title": "T", "level": "L1"}, "groups": [], "nodes": [], "edges": []}
    findings = [
        _finding({"op": "set_zone", "group": "personas", "value": "left"}),
        _finding({"op": "channel_prefer", "edge": "e3", "value": "V1"}),
    ]
    out = arp.apply(ir, findings)
    hints = out["layoutHints"]
    assert {"op": "set_zone", "group": "personas", "value": "left"} in hints
    assert {"op": "channel_prefer", "edge": "e3", "value": "V1"} in hints
    assert len(hints) == 2


def test_apply_is_idempotent():
    ir = {"metadata": {"title": "T", "level": "L1"}, "groups": [], "nodes": [], "edges": []}
    findings = [
        _finding({"op": "set_zone", "group": "g1", "value": "right"}),
        _finding({"op": "toggle_separator", "value": True}),
    ]
    once = arp.apply(json.loads(json.dumps(ir)), findings)
    twice = arp.apply(json.loads(json.dumps(once)), findings)
    assert once == twice


def test_apply_later_hint_supersedes_same_target():
    ir = {"metadata": {"title": "T", "level": "L1"}, "groups": [], "nodes": [], "edges": [],
          "layoutHints": [{"op": "set_zone", "group": "g1", "value": "left"}]}
    findings = [_finding({"op": "set_zone", "group": "g1", "value": "right"})]
    out = arp.apply(ir, findings)
    zones = [h for h in out["layoutHints"] if h["op"] == "set_zone" and h.get("group") == "g1"]
    assert len(zones) == 1
    assert zones[0]["value"] == "right"


def test_apply_passes_null_patch_through_without_adding_hint():
    ir = {"metadata": {"title": "T", "level": "L1"}, "groups": [], "nodes": [], "edges": []}
    findings = [
        _finding(None, rule="comp-legend-present"),
        _finding({"op": "set_zone", "group": "g1", "value": "center"}),
    ]
    out = arp.apply(ir, findings)
    assert out["layoutHints"] == [{"op": "set_zone", "group": "g1", "value": "center"}]


def test_apply_accepts_bare_patch_finding():
    ir = {"metadata": {"title": "T", "level": "L1"}, "groups": [], "nodes": [], "edges": []}
    findings = [{"op": "set_icon_size", "value": "L"}]
    out = arp.apply(ir, findings)
    assert {"op": "set_icon_size", "value": "L"} in out["layoutHints"]


@pytest.mark.parametrize("patch", [
    {"op": "recolor", "group": "g", "value": "blue"},          # unknown op
    {"op": "set_zone", "group": "g", "value": "middle"},        # bad value domain
    {"op": "set_group_flow", "group": "g", "value": "diagonal"},
    {"op": "set_icon_size", "value": "XL"},
    {"op": "channel_prefer", "value": "V1"},                    # missing edge
    {"op": "set_zone", "value": "left"},                        # missing group
])
def test_validate_rejects_bad_patch(patch):
    with pytest.raises(arp.PatchError):
        arp.validate_patch(patch, "finding[0]")


def test_cli_unknown_op_exits_2_and_lists_vocabulary(tmp_path):
    ir = tmp_path / "ir.json"
    ir.write_text(json.dumps({"metadata": {"title": "T", "level": "L1"},
                              "groups": [], "nodes": [], "edges": []}))
    findings = tmp_path / "f.json"
    findings.write_text(json.dumps([{"patch": {"op": "make_it_blue", "value": "x"}}]))
    r = subprocess.run(
        [sys.executable, str(SCRIPT), str(ir), "--findings", str(findings)],
        capture_output=True, text=True,
    )
    assert r.returncode == 2
    msg = r.stdout + r.stderr
    # every allowed op is named in the actionable error
    for op in ("set_group_flow", "set_zone", "order_override", "nudge_label",
               "channel_prefer", "set_icon_size", "toggle_separator"):
        assert op in msg


def test_cli_writes_out_and_exits_0(tmp_path):
    ir = tmp_path / "ir.json"
    ir.write_text(json.dumps({"metadata": {"title": "T", "level": "L1"},
                              "groups": [], "nodes": [], "edges": []}))
    findings = tmp_path / "f.json"
    findings.write_text(json.dumps([_finding({"op": "toggle_separator", "value": False})]))
    out = tmp_path / "out.json"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), str(ir), "--findings", str(findings), "--out", str(out)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    written = json.loads(out.read_text())
    assert {"op": "toggle_separator", "value": False} in written["layoutHints"]


# ── Part 2: engine consumption ───────────────────────────────────────────────
def _order_ir():
    return {
        "metadata": {"title": "T", "level": "L1"},
        "groups": [{"id": "g", "type": "btp-layer", "position": "center"}],
        "nodes": [
            {"id": "a", "group": "g", "label": "Alpha"},
            {"id": "b", "group": "g", "label": "Beta"},
            {"id": "c", "group": "g", "label": "Gamma"},
        ],
        "edges": [
            {"id": "e1", "source": "a", "target": "b"},
            {"id": "e2", "source": "b", "target": "c"},
        ],
    }


def test_order_override_changes_sibling_x_order():
    si = gen.ShapeIndex.load()
    dia = gen.parse_json(_order_ir())
    base = sl.compute_layout(dia, si)                       # default flow rank: a<b<c
    ax, bx, cx = (base["nodes"][n][0] for n in ("a", "b", "c"))
    assert ax < bx < cx

    dia2 = gen.parse_json(_order_ir())
    hints = {"zone": {}, "flow": {}, "order": {"g": ["c", "b", "a"]},
             "icon_size": None, "separator": None, "channel_prefer": {},
             "nudge_label": set()}
    over = sl.compute_layout(dia2, si, hints=hints)
    ax2, bx2, cx2 = (over["nodes"][n][0] for n in ("a", "b", "c"))
    assert cx2 < bx2 < ax2, "order_override must reverse the packed x-order"


def _pin_layout():
    """Three columns → gutters V0 (LC, x in [100,300]) and V1 (CR, x in [500,700])."""
    return {
        "nodes": {
            "L1": (20, 360, 60, 40),
            "C1": (350, 300, 60, 40),
            "C2": (350, 500, 60, 40),
            "R1": (720, 300, 60, 40),
        },
        "groups": {},
        "canvas": (900, 700),
        "meta": {
            "columns": {"left": (0, 100), "center": (300, 500), "right": (700, 800)},
            "networkSeparator": None,
        },
    }


def _edge(eid, s, t):
    return types.SimpleNamespace(id=eid, source=s, target=t, flowFamily=None,
                                 pill=None, label=None, kind="default")


def _dia(edges):
    return types.SimpleNamespace(edges=edges)


def test_channel_prefer_survives_reduce_crossings():
    """An adjacent L1→C1 edge naturally routes through gutter V0. A
    channel_prefer hint pins it to V1; the pin must survive route()'s
    hardcoded reduce_crossings (which only permutes lane/port ORDER within a
    channel, never channel MEMBERSHIP) — the Task 13 seam composition."""
    lay = _pin_layout()
    dia = _dia([_edge("eADJ", "L1", "C1"), _edge("eB", "C1", "C2")])

    # default: eADJ lands in V0 (the shared left↔center gutter)
    base = router.route(dia, lay)
    v0 = next(c for c in base.channels if c.id == "V0")
    assert "eADJ" in v0.lanes

    # pinned: eADJ must instead land in V1, and its waypoints ride that gutter
    pinned = router.route(dia, lay, hints={"channel_prefer": {"eADJ": "V1"}})
    v1 = next(c for c in pinned.channels if c.id == "V1")
    assert "eADJ" in v1.lanes, "channel_prefer pin did not survive route()"
    for x, _y in pinned.waypoints["eADJ"]:
        assert v1.rect.x <= x <= v1.rect.right


def test_no_hints_route_is_byte_identical():
    """hints=None (and an empty-hint dict) reproduce the pre-Task-13 route."""
    lay = _pin_layout()
    dia = _dia([_edge("eADJ", "L1", "C1"), _edge("eB", "C1", "C2")])
    a = router.route(dia, lay)
    b = router.route(dia, lay, hints=None)
    c = router.route(dia, lay, hints={"channel_prefer": {}, "nudge_label": set()})
    assert a.waypoints == b.waypoints == c.waypoints
    assert a.pill_pos == b.pill_pos == c.pill_pos
    assert a.label_pos == b.label_pos == c.label_pos
