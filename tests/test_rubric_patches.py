# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""Tests for Task 13 — the visual-rubric patch pipeline.

Three parts, mirroring the deliverables:

  1. ``scripts/apply-rubric-patches.py`` — turns a findings JSON into
     ``diagram.layoutHints`` entries: validates the 7-op vocabulary,
     merges/dedupes idempotently, passes ``null`` (manual) patches through,
     and exits 2 (listing the vocabulary) on an unknown/malformed op.

  2. Engine consumption of ``layoutHints`` — a hint actually changes the
     output: ``order_override`` reorders siblings in ``_skeleton_layout``;
     ``channel_prefer`` pins an edge's channel through the FULL ``route()``
     pipeline (which hardcodes ``reduce_crossings``) — the seam composition.

  3. Review round (unknown-hint observability + engine coverage): an
     unresolved hint (bad group/edge/channel id) now emits a WARNING instead
     of vanishing silently; ``nudge_label`` / ``set_icon_size`` /
     ``set_group_flow`` / ``toggle_separator`` each get an engine-effect
     assertion; ``apply-rubric-patches.py`` routes ALL exit-2 error text to
     stderr and reports every bad patch in one run.
"""
import json
import subprocess
import sys
import types
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from conftest import load_script

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "apply-rubric-patches.py"
V2 = ROOT / "tests" / "fixtures" / "ir-v2-sample.json"

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
    assert a.hint_warnings == b.hint_warnings == c.hint_warnings == []


# ── Part 3: review round — FIX-1 (unresolved-hint observability) ────────────
def test_channel_prefer_unknown_edge_warns():
    """An edge id that doesn't exist in the CURRENT plans is ignored (ids are
    read off the render, never memorised) but must not vanish silently."""
    lay = _pin_layout()
    dia = _dia([_edge("eADJ", "L1", "C1")])
    r = router.route(dia, lay, hints={"channel_prefer": {"does-not-exist": "V1"}})
    assert any("unknown edge" in w and "does-not-exist" in w for w in r.hint_warnings), \
        r.hint_warnings


def test_channel_prefer_unknown_channel_warns():
    lay = _pin_layout()
    dia = _dia([_edge("eADJ", "L1", "C1")])
    r = router.route(dia, lay, hints={"channel_prefer": {"eADJ": "Vzzz"}})
    assert any("unknown channel" in w and "Vzzz" in w for w in r.hint_warnings), \
        r.hint_warnings


def test_channel_prefer_no_warning_when_resolved():
    """The warning path must only fire on an UNRESOLVED hint — a valid pin
    stays silent (no-hints/valid-hints output stays byte-identical)."""
    lay = _pin_layout()
    dia = _dia([_edge("eADJ", "L1", "C1")])
    r = router.route(dia, lay, hints={"channel_prefer": {"eADJ": "V1"}})
    assert r.hint_warnings == []


def test_bogus_layout_hint_emits_warning_on_stderr(capsys):
    """The scenario the review flagged: a layoutHint targeting a NONEXISTENT
    id must surface a WARNING on stderr so Task 14's render→patch→regenerate
    loop can tell "fix applied" from "fix silently discarded"."""
    ir = json.loads(V2.read_text(encoding="utf-8"))
    ir["layoutHints"] = [{"op": "set_zone", "group": "does-not-exist", "value": "left"}]
    dia = gen.parse_json(ir)
    si = gen.ShapeIndex.load()
    gen.emit(dia, si, layout="auto")
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "set_zone" in err
    assert "does-not-exist" in err


def test_unknown_group_hints_all_warn(capsys):
    """set_zone / set_group_flow / order_override each resolve against
    ``diagram.groups`` — an unknown id for ANY of the three must warn."""
    ir = json.loads(V2.read_text(encoding="utf-8"))
    ir["layoutHints"] = [
        {"op": "set_zone", "group": "ghost-zone", "value": "left"},
        {"op": "set_group_flow", "group": "ghost-flow", "value": "col"},
        {"op": "order_override", "group": "ghost-order", "value": ["x"]},
    ]
    dia = gen.parse_json(ir)
    si = gen.ShapeIndex.load()
    gen.emit(dia, si, layout="auto")
    err = capsys.readouterr().err
    assert "set_zone" in err and "ghost-zone" in err
    assert "set_group_flow" in err and "ghost-flow" in err
    assert "order_override" in err and "ghost-order" in err


def test_valid_layout_hint_emits_no_warning(capsys):
    """Regression guard: a hint that DOES resolve must stay silent — the
    warning path only fires on an unresolved target."""
    ir = json.loads(V2.read_text(encoding="utf-8"))
    ir["layoutHints"] = [{"op": "set_zone", "group": "personas", "value": "left"}]
    dia = gen.parse_json(ir)
    si = gen.ShapeIndex.load()
    gen.emit(dia, si, layout="auto")
    err = capsys.readouterr().err
    assert "layoutHint" not in err


def test_no_hints_emit_is_byte_identical(capsys):
    """An IR with no layoutHints at all must not touch stderr with any
    layoutHint warning (no-hints output stays byte-identical)."""
    ir = json.loads(V2.read_text(encoding="utf-8"))
    dia = gen.parse_json(ir)
    si = gen.ShapeIndex.load()
    gen.emit(dia, si, layout="auto")
    err = capsys.readouterr().err
    assert "layoutHint" not in err


# ── Part 3: review round — FIX-2 (engine-effect coverage for the other ops) ─
def test_nudge_label_shifts_to_a_different_slot():
    """``nudge_label`` (``_place_in_slots`` ``skip=1``) moves the flagged
    edge's label to the NEXT free slot instead of the first."""
    lay = _pin_layout()
    e = _edge("eADJ", "L1", "C1")
    e.label = "SCIM"
    dia = _dia([e])
    base = router.route(dia, lay)
    nudged = router.route(dia, lay, hints={"nudge_label": {"eADJ"}})
    assert "eADJ" in base.label_pos and "eADJ" in nudged.label_pos
    assert base.label_pos["eADJ"] != nudged.label_pos["eADJ"], \
        "nudge_label must move the label off its default (first) slot"


def _icon_ir():
    return {
        "metadata": {"title": "T", "level": "L1"},
        "groups": [{"id": "g", "type": "btp-layer", "position": "center"}],
        "nodes": [{"id": "a", "group": "g", "label": "A", "genericIcon": "user"}],
        "edges": [],
    }


def test_set_icon_size_changes_node_footprint():
    """``set_icon_size`` threads ``icon_dim`` into every v1 icon footprint
    (``_ICON_SIZE_PX``) — L must yield a strictly larger box than M."""
    si = gen.ShapeIndex.load()
    hints_m = {"zone": {}, "flow": {}, "order": {}, "icon_size": "M",
               "separator": None, "channel_prefer": {}, "nudge_label": set()}
    hints_l = dict(hints_m, icon_size="L")

    m = sl.compute_layout(gen.parse_json(_icon_ir()), si, hints=hints_m)
    l = sl.compute_layout(gen.parse_json(_icon_ir()), si, hints=hints_l)
    _, _, mw, mh = m["nodes"]["a"]
    _, _, lw, lh = l["nodes"]["a"]
    assert lh > mh, f"set_icon_size L must be taller than M, got L={lh} M={mh}"
    assert lw >= mw, f"set_icon_size L must not be narrower than M, got L={lw} M={mw}"


def _flow_ir():
    return {
        "metadata": {"title": "T", "level": "L1"},
        "groups": [{"id": "g", "type": "btp-layer", "position": "center"}],
        "nodes": [
            {"id": "a", "group": "g", "label": "Alpha"},
            {"id": "b", "group": "g", "label": "Beta"},
            {"id": "c", "group": "g", "label": "Gamma"},
        ],
        "edges": [],
    }


def test_set_group_flow_changes_intra_packing():
    """``set_group_flow`` overrides ``_pack_mode``'s row/col/grid choice — a
    ``col`` hint must stack the group's nodes in a single column instead of
    the default single row."""
    si = gen.ShapeIndex.load()

    row = sl.compute_layout(gen.parse_json(_flow_ir()), si)   # default: row (n<=4)
    ys_row = {row["nodes"][n][1] for n in ("a", "b", "c")}
    assert len(ys_row) == 1, "default packing of 3 siblings should be a single row"

    dia_col = gen.parse_json(_flow_ir())
    dia_col.groups[0].flow = "col"      # what emit() does for a set_group_flow hint
    col = sl.compute_layout(dia_col, si)
    xs_col = {col["nodes"][n][0] for n in ("a", "b", "c")}
    ys_col = [col["nodes"][n][1] for n in ("a", "b", "c")]
    assert len(xs_col) == 1, "set_group_flow=col must stack nodes in a single column"
    assert len(set(ys_col)) == 3 and ys_col == sorted(ys_col), \
        "columned nodes must be stacked top-to-bottom, not overlapping"


def _sep_present(xml_text: str) -> bool:
    mol = load_script("_molecules")
    sep_style = mol.load_contract()["molecules"]["network-separator"]["style"]
    root = ET.fromstring(xml_text)
    return any(c.get("edge") == "1" and c.get("style", "").startswith(sep_style)
               for c in root.iter("mxCell"))


def test_toggle_separator_false_removes_when_not_explicit():
    """metadata has no ``networkSeparator`` key ⇒ the author never opted
    in/out explicitly, so a ``toggle_separator:false`` hint is free to
    remove the auto-detected bar."""
    ir = json.loads(V2.read_text(encoding="utf-8"))
    assert "networkSeparator" not in ir["metadata"]
    ir["layoutHints"] = [{"op": "toggle_separator", "value": False}]
    dia = gen.parse_json(ir)
    si = gen.ShapeIndex.load()
    xml_text = gen.emit(dia, si, layout="auto")
    assert not _sep_present(xml_text)


def test_toggle_separator_false_loses_to_explicit_metadata_true():
    """The subtle precedence path: an EXPLICIT ``metadata.networkSeparator:
    true`` is the author's direct instruction and WINS over a
    ``toggle_separator:false`` rubric hint (``_network_separator_explicit``)."""
    ir = json.loads(V2.read_text(encoding="utf-8"))
    ir["metadata"]["networkSeparator"] = True
    ir["layoutHints"] = [{"op": "toggle_separator", "value": False}]
    dia = gen.parse_json(ir)
    assert dia._network_separator_explicit is True
    si = gen.ShapeIndex.load()
    xml_text = gen.emit(dia, si, layout="auto")
    assert _sep_present(xml_text), \
        "explicit metadata.networkSeparator=true must win over toggle_separator:false"


# ── Part 3: review round — FIX-3 (apply-rubric-patches stderr contract) ─────
def test_cli_error_output_goes_to_stderr_only(tmp_path):
    """Every exit-2 error line is on STDERR — I/O errors already went there;
    Task 14's loop captures errors from stderr only."""
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
    assert r.stdout == ""
    assert "ERROR" in r.stderr


def test_cli_reports_every_bad_patch_not_just_the_first(tmp_path):
    """Collect-all, not fail-fast: a findings list with TWO bad patches must
    report BOTH in a single run."""
    ir = tmp_path / "ir.json"
    ir.write_text(json.dumps({"metadata": {"title": "T", "level": "L1"},
                              "groups": [], "nodes": [], "edges": []}))
    findings = tmp_path / "f.json"
    findings.write_text(json.dumps([
        {"patch": {"op": "make_it_blue", "value": "x"}},
        {"patch": {"op": "set_zone", "group": "g", "value": "middle"}},
    ]))
    r = subprocess.run(
        [sys.executable, str(SCRIPT), str(ir), "--findings", str(findings)],
        capture_output=True, text=True,
    )
    assert r.returncode == 2
    assert r.stdout == ""
    assert "finding[0]" in r.stderr and "finding[1]" in r.stderr
    assert r.stderr.count("ERROR finding[") == 2


def test_collect_patches_raises_patcherrors_with_every_problem():
    findings = [
        {"patch": {"op": "bogus"}},
        {"patch": {"op": "set_zone", "group": "g", "value": "nope"}},
        {"patch": {"op": "toggle_separator", "value": True}},   # valid: not an error
    ]
    with pytest.raises(arp.PatchErrors) as exc_info:
        arp._collect_patches(findings)
    assert len(exc_info.value.errors) == 2
    assert all(isinstance(e, arp.PatchError) for e in exc_info.value.errors)


def test_apply_still_all_or_nothing_on_bad_patches():
    """Bad patches must not partially land — ``apply()`` raises before
    ``ir['layoutHints']`` is ever mutated, matching the pre-review contract."""
    ir = {"metadata": {"title": "T", "level": "L1"}, "groups": [], "nodes": [], "edges": []}
    findings = [
        _finding({"op": "set_zone", "group": "g1", "value": "left"}),   # valid
        _finding({"op": "recolor", "group": "g", "value": "blue"}),     # invalid
    ]
    with pytest.raises(arp.PatchErrors):
        arp.apply(ir, findings)
    assert "layoutHints" not in ir
