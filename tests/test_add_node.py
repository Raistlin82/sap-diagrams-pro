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
