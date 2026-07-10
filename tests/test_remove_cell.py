# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""tests/test_remove_cell.py — the hybrid scaffold-path surgical remove step.

remove-cell.py deletes a cell from a scaffolded .drawio together with its whole
subtree (any cell whose parent chain reaches the target) and every edge left
dangling by the removal (source/target pointing at a removed cell). It writes a
.bak of the prior on-disk state via the shared _drawio_edit.save helper.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from conftest import load_script

remove_cell = load_script("remove-cell")
edit = load_script("_drawio_edit")

MX = ('<mxfile><diagram><mxGraphModel><root>'
      '<mxCell id="0"/><mxCell id="1" parent="0"/>'
      '<mxCell id="g" value="Group" parent="1" vertex="1" style="rounded=1;">'
      '<mxGeometry x="10" y="20" width="100" height="80" as="geometry"/></mxCell>'
      '<mxCell id="n" value="Node" parent="g" vertex="1"><mxGeometry x="0" y="0" width="40" height="40" as="geometry"/></mxCell>'
      '<mxCell id="e" edge="1" source="n" target="g" parent="1"><mxGeometry as="geometry"/></mxCell>'
      '</root></mxGraphModel></diagram></mxfile>')


def _write(tmp_path: Path) -> Path:
    f = tmp_path / "d.drawio"
    f.write_text(MX, encoding="utf-8")
    return f


def _ids(path: Path) -> set[str]:
    return {c.get("id") for c in ET.parse(path).getroot().iter("mxCell")}


def test_remove_leaf_drops_incident_edges(tmp_path):
    f = _write(tmp_path)
    rc = remove_cell.main([str(f), "--id", "n"])
    assert rc == 0
    ids = _ids(f)
    assert "n" not in ids            # target gone
    assert "e" not in ids            # edge with source=n dropped (no dangling)
    assert "g" in ids                # sibling kept
    assert Path(str(f) + ".bak").exists()


def test_remove_container_drops_subtree(tmp_path):
    f = _write(tmp_path)
    rc = remove_cell.main([str(f), "--id", "g"])
    assert rc == 0
    ids = _ids(f)
    assert "g" not in ids            # container gone
    assert "n" not in ids            # child of g gone
    assert "e" not in ids            # edge incident to g and n gone


def test_remove_by_label(tmp_path):
    f = _write(tmp_path)
    rc = remove_cell.main([str(f), "--match", "Node"])
    assert rc == 0
    ids = _ids(f)
    assert "n" not in ids
    assert "e" not in ids            # edge with source=n dropped
    assert "g" in ids


def test_unknown_id_errors(tmp_path):
    f = _write(tmp_path)
    original = f.read_bytes()
    try:
        rc = remove_cell.main([str(f), "--id", "zzz"])
    except SystemExit as exc:
        rc = exc.code
    assert rc not in (0, None)                       # nonzero exit
    assert not Path(str(f) + ".bak").exists()        # nothing written
    assert f.read_bytes() == original                # file untouched


def test_json_reports_removed(tmp_path, capsys):
    f = _write(tmp_path)
    rc = remove_cell.main([str(f), "--id", "n", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload == {"removed": ["e", "n"]}          # sorted
