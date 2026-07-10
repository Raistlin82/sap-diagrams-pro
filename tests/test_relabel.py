# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""tests/test_relabel.py — the hybrid scaffold-path surgical relabel step.

relabel.py changes the visible text of cells in a scaffolded .drawio while
preserving every cell's geometry, style, id and the overall structure. These
tests copy a real template, relabel it, and assert that the labels changed but
nothing structural did.
"""
from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from conftest import load_script

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "assets" / "templates" / "SAP_Task_Center_L1.drawio"

relabel = load_script("relabel")


def _model(path: Path):
    """Return (cell_ids, geometries, styles) snapshots for structural diffing."""
    root = ET.parse(path).getroot()
    ids, geoms, styles = [], [], []
    for e in root.iter():
        if e.tag in ("mxCell", "object"):
            ids.append(e.get("id"))
            styles.append(e.get("style"))
        if e.tag == "mxGeometry":
            geoms.append((e.get("x"), e.get("y"), e.get("width"), e.get("height")))
    return ids, geoms, styles


def _first_labelled_cell(path: Path):
    root = ET.parse(path).getroot()
    for e in root.iter():
        v = e.get("value")
        if v and relabel.clean_label(v):
            return e.get("id"), relabel.clean_label(v)
    raise AssertionError("no labelled cell found")


def test_set_by_id_changes_label_preserves_structure(tmp_path):
    work = tmp_path / "d.drawio"
    shutil.copyfile(TEMPLATE, work)
    before_ids, before_geoms, before_styles = _model(work)
    cell_id, _old = _first_labelled_cell(work)

    rc = relabel.main([str(work), "--set", f"{cell_id}=Central Inbox Hub"])
    assert rc == 0

    after_ids, after_geoms, after_styles = _model(work)
    assert after_ids == before_ids           # no cells added/removed/reordered
    assert after_geoms == before_geoms        # geometry untouched
    assert after_styles == before_styles      # style untouched

    # The targeted cell's visible label changed.
    root = ET.parse(work).getroot()
    target = next(e for e in root.iter() if e.get("id") == cell_id)
    assert relabel.clean_label(target.get("value")) == "Central Inbox Hub"


def test_replace_by_visible_label(tmp_path):
    work = tmp_path / "d.drawio"
    shutil.copyfile(TEMPLATE, work)
    _cid, visible = _first_labelled_cell(work)

    rc = relabel.main([str(work), "--replace", f"{visible}=Renamed Service"])
    assert rc == 0
    labels = {relabel.clean_label(e.get("value"))
              for e in ET.parse(work).getroot().iter() if e.get("value")}
    assert "Renamed Service" in labels
    assert visible not in labels


def test_inplace_writes_backup(tmp_path):
    work = tmp_path / "d.drawio"
    shutil.copyfile(TEMPLATE, work)
    original = work.read_bytes()
    cid, _ = _first_labelled_cell(work)
    relabel.main([str(work), "--set", f"{cid}=X"])
    bak = work.with_suffix(".drawio.bak")
    assert bak.exists()
    assert bak.read_bytes() == original  # .bak is the pristine original


def test_out_leaves_source_intact_no_backup(tmp_path):
    work = tmp_path / "d.drawio"
    out = tmp_path / "adapted.drawio"
    shutil.copyfile(TEMPLATE, work)
    original = work.read_bytes()
    cid, _ = _first_labelled_cell(work)
    rc = relabel.main([str(work), "--set", f"{cid}=X", "--out", str(out)])
    assert rc == 0
    assert work.read_bytes() == original          # source untouched
    assert out.exists()
    assert not work.with_suffix(".drawio.bak").exists()  # no bak with --out


def test_preserves_simple_inline_wrapper(tmp_path):
    work = tmp_path / "d.drawio"
    shutil.copyfile(TEMPLATE, work)
    # Find a cell whose value is wrapped in a <font ...> tag.
    root = ET.parse(work).getroot()
    wrapped = next(
        (e for e in root.iter()
         if e.get("value") and e.get("value").lower().startswith("<font")),
        None,
    )
    assert wrapped is not None, "fixture should contain a <font>-wrapped label"
    cid = wrapped.get("id")
    relabel.main([str(work), "--set", f"{cid}=Wrapped Label"])
    new_val = next(e.get("value") for e in ET.parse(work).getroot().iter()
                   if e.get("id") == cid)
    assert "<font" in new_val.lower() and "Wrapped Label" in new_val


def test_no_edits_flags_exit_2(tmp_path):
    work = tmp_path / "d.drawio"
    shutil.copyfile(TEMPLATE, work)
    assert relabel.main([str(work)]) == 2


def test_bad_set_syntax_exit_2(tmp_path):
    work = tmp_path / "d.drawio"
    shutil.copyfile(TEMPLATE, work)
    assert relabel.main([str(work), "--set", "no-equals-here"]) == 2


def test_missing_file_exit_2(tmp_path):
    assert relabel.main([str(tmp_path / "nope.drawio"), "--set", "a=b"]) == 2
