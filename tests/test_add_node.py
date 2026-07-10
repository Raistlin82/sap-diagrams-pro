# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""tests/test_add_node.py — the hybrid scaffold-path surgical add-node step.

add-node.py drops a new node into an existing group of a scaffolded .drawio.
This file exercises ONLY ``--mode slot`` (the ``--mode append`` reflow path is a
separate later task). Slot mode:
  * resolves the node's icon via the emitter's own ShapeIndex (service name or a
    generic-icon key) and builds a single styled vertex parented to the group,
  * places it by scanning the group's content box on the 10px grid for the first
    W×H rectangle that lies inside the frame and overlaps no existing child,
    starting the scan from ``--near``'s rect,
  * snaps the placed rect to the grid, and
  * prints ``{"id": "<newid>"}`` with ``--json``.
It saves via the shared _drawio_edit.save helper (which writes a .bak first).
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from conftest import load_script

add_node = load_script("add-node")
edit = load_script("_drawio_edit")

from _geom_checks import Rect  # noqa: E402  (SCRIPTS on sys.path via conftest)

# Group content-box extent the fixture bakes in (group-local coords).
GROUP_W, GROUP_H = 800.0, 400.0


def _fixture(tmp_path: Path) -> Path:
    """A group ``g`` (with geometry) holding one child node ``n`` (with
    geometry), plus the mandatory root cells 0/1. ``n`` sits near the top-left
    so a slot-placed sibling has to be pushed clear of it."""
    mx = (
        '<mxfile><diagram><mxGraphModel><root>'
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        '<mxCell id="g" value="Group" parent="1" vertex="1" style="rounded=1;">'
        f'<mxGeometry x="100" y="80" width="{int(GROUP_W)}" height="{int(GROUP_H)}" as="geometry"/></mxCell>'
        '<mxCell id="n" value="Existing" parent="g" vertex="1" style="rounded=1;">'
        '<mxGeometry x="40" y="40" width="200" height="70" as="geometry"/></mxCell>'
        '</root></mxGraphModel></diagram></mxfile>'
    )
    f = tmp_path / "d.drawio"
    f.write_text(mx, encoding="utf-8")
    return f


def _cells(path: Path) -> list[ET.Element]:
    return list(ET.parse(path).getroot().iter("mxCell"))


def _new_node(path: Path) -> ET.Element:
    """The freshly added Cloud ALM vertex (value uniquely identifies it)."""
    hits = [c for c in _cells(path)
            if c.get("value") == "Cloud ALM" and c.get("vertex") == "1"]
    assert len(hits) == 1, f"expected exactly one new node, got {len(hits)}"
    return hits[0]


def test_add_node_slot_resolves_icon(tmp_path):
    f = _fixture(tmp_path)
    rc = add_node.main([
        str(f), "--group", "g", "--label", "Cloud ALM",
        "--service", "Cloud ALM", "--mode", "slot", "--near", "n",
    ])
    assert rc == 0
    node = _new_node(f)
    assert node.get("parent") == "g"
    assert node.get("value") == "Cloud ALM"
    assert "image=" in (node.get("style") or ""), "icon not resolved into style"
    assert Path(str(f) + ".bak").exists()


def test_add_node_slot_grid_snapped_no_overlap(tmp_path):
    f = _fixture(tmp_path)
    rc = add_node.main([
        str(f), "--group", "g", "--label", "Cloud ALM",
        "--service", "Cloud ALM", "--mode", "slot", "--near", "n",
    ])
    assert rc == 0
    node = _new_node(f)
    x, y, w, h = edit.geometry(node)

    # every coordinate on the 10px grid
    for v in (x, y, w, h):
        assert v % 10 == 0, f"{v} not on the 10px grid"

    # the rect lies inside the group frame (group-local content box)
    assert 0 <= x and x + w <= GROUP_W
    assert 0 <= y and y + h <= GROUP_H

    # and overlaps NO existing child of g
    new_rect = Rect(x, y, w, h)
    existing = [c for c in _cells(f)
                if c.get("parent") == "g" and c.get("id") != node.get("id")]
    assert existing, "fixture should have a pre-existing child"
    for c in existing:
        cx, cy, cw, ch = edit.geometry(c)
        assert not new_rect.intersects(Rect(cx, cy, cw, ch)), \
            f"new node {new_rect} overlaps existing child {c.get('id')}"


def test_add_node_returns_id_json(tmp_path, capsys):
    f = _fixture(tmp_path)
    rc = add_node.main([
        str(f), "--group", "g", "--label", "Cloud ALM",
        "--service", "Cloud ALM", "--mode", "slot", "--near", "n", "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "id" in payload
    ids = {c.get("id") for c in _cells(f)}
    assert payload["id"] in ids, "reported id must name a real cell"


# ─────────────────────────────────────────────────────────────────────────────
# --mode append (localized group reflow)
#
# Append adds the new node as a CHILD of the target group, then re-packs ONLY
# that group's vertex children with the engine's own packing (row/grid,
# NODE_GAP spacing) and grows the frame to contain them — its own x/y stay put.
# Growth is localized: if the grown frame runs into exactly ONE top-level
# sibling it shifts that sibling clear; ≥2 siblings → it refuses (no reflow can
# be localized) and leaves the file untouched.
# ─────────────────────────────────────────────────────────────────────────────
def _append_fixture(tmp_path: Path, *, siblings: str = "") -> Path:
    """Group ``g`` (480×120 at 100,80) with two 200×70 children in a row and
    20px top-left insets / 30px right-bottom margins, so a re-pack visibly grows
    the frame. ``siblings`` injects extra top-level cells beside ``g``.

    Group-local child coords (relative to ``g``): c1 at (20,20), c2 at (250,20)
    → content spans x[20,450] y[20,90]; with g 480×120 that leaves a 30px right
    and 30px bottom margin. All coordinates on the 10px grid.
    """
    mx = (
        '<mxfile><diagram><mxGraphModel><root>'
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        '<mxCell id="g" value="Group" parent="1" vertex="1" style="rounded=1;">'
        '<mxGeometry x="100" y="80" width="480" height="120" as="geometry"/></mxCell>'
        '<mxCell id="c1" value="One" parent="g" vertex="1" style="rounded=1;">'
        '<mxGeometry x="20" y="20" width="200" height="70" as="geometry"/></mxCell>'
        '<mxCell id="c2" value="Two" parent="g" vertex="1" style="rounded=1;">'
        '<mxGeometry x="250" y="20" width="200" height="70" as="geometry"/></mxCell>'
        f'{siblings}'
        '</root></mxGraphModel></diagram></mxfile>'
    )
    f = tmp_path / "d.drawio"
    f.write_text(mx, encoding="utf-8")
    return f


def test_add_node_append_reflows_group_only(tmp_path):
    f = _append_fixture(tmp_path)
    rc = add_node.main([
        str(f), "--group", "g", "--label", "Cloud ALM",
        "--service", "Cloud ALM", "--mode", "append",
    ])
    assert rc == 0

    cells = _cells(f)
    g = next(c for c in cells if c.get("id") == "g")
    gx, gy, gw, gh = edit.geometry(g)
    assert gw > 480, "the group frame must have grown to fit the third child"

    kids = [c for c in cells if c.get("parent") == "g" and c.get("vertex") == "1"]
    assert len(kids) == 3, "the appended node is a child of g"
    rects = [edit.geometry(c) for c in kids]

    for x, y, w, h in rects:                     # every child inside the grown frame
        assert 0 <= x and x + w <= gw, f"child {x, y, w, h} escapes g width {gw}"
        assert 0 <= y and y + h <= gh, f"child {x, y, w, h} escapes g height {gh}"

    R = [Rect(*r) for r in rects]                # pairwise non-overlapping
    for i in range(len(R)):
        for j in range(i + 1, len(R)):
            assert not R[i].intersects(R[j]), f"reflowed children overlap: {rects}"

    # engine row-pack of three equal-height boxes ⇒ a single row, grid-aligned
    assert len({y for _, y, _, _ in rects}) == 1, "row pack should share one y"
    for x, y, w, h in rects:
        assert x % 10 == 0 and y % 10 == 0, f"reflowed child {x, y} off the grid"


def test_add_node_append_keeps_siblings(tmp_path, capsys):
    # g2 sits flush against g's right edge (x=580 == 100+480) and extends past the
    # grown frame; g3 is far away and must never be touched.
    siblings = (
        '<mxCell id="g2" value="Right" parent="1" vertex="1" style="rounded=1;">'
        '<mxGeometry x="580" y="80" width="250" height="120" as="geometry"/></mxCell>'
        '<mxCell id="g3" value="Far" parent="1" vertex="1" style="rounded=1;">'
        '<mxGeometry x="1300" y="80" width="200" height="120" as="geometry"/></mxCell>'
    )
    f = _append_fixture(tmp_path, siblings=siblings)
    rc = add_node.main([
        str(f), "--group", "g", "--label", "Cloud ALM",
        "--service", "Cloud ALM", "--mode", "append", "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["shifted"] == ["g2"], "the one grown-into sibling is reported"
    assert "id" in payload

    cells = _cells(f)
    g = next(c for c in cells if c.get("id") == "g")
    gx, gy, gw, gh = edit.geometry(g)
    g2x, _, _, _ = edit.geometry(next(c for c in cells if c.get("id") == "g2"))

    # g2 was pushed right by exactly the overlap (grown right edge − its old left),
    # landing flush against the grown frame's right edge — no more, no less.
    overlap = (gx + gw) - 580.0
    assert overlap > 0
    assert g2x == 580.0 + overlap == gx + gw

    # g3 was never reached, so it did not move.
    g3x, _, _, _ = edit.geometry(next(c for c in cells if c.get("id") == "g3"))
    assert g3x == 1300.0, "a sibling the frame never reached must stay put"


def test_add_node_append_errors_when_no_clean_reflow(tmp_path, capsys):
    # Two siblings, both flush against g's right edge and stacked — the grown
    # frame runs into BOTH, so no single-sibling shift can localize the growth.
    siblings = (
        '<mxCell id="g2" value="TopRight" parent="1" vertex="1" style="rounded=1;">'
        '<mxGeometry x="580" y="80" width="250" height="60" as="geometry"/></mxCell>'
        '<mxCell id="g3" value="BotRight" parent="1" vertex="1" style="rounded=1;">'
        '<mxGeometry x="580" y="150" width="250" height="60" as="geometry"/></mxCell>'
    )
    f = _append_fixture(tmp_path, siblings=siblings)
    before = f.read_bytes()

    rc = add_node.main([
        str(f), "--group", "g", "--label", "Cloud ALM",
        "--service", "Cloud ALM", "--mode", "append",
    ])
    assert rc == 2, "≥2-sibling collision is a hard refusal"
    err = capsys.readouterr().err
    assert "slot" in err.lower(), "the error should point the caller at --mode slot"

    assert f.read_bytes() == before, "the file must be unchanged on a refusal"
    assert not Path(str(f) + ".bak").exists(), "nothing saved ⇒ no .bak either"
