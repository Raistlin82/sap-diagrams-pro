# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""tests/test_scaffold.py — the hybrid scaffold-path copy step.

scaffold-diagram.py copies the closest real SAP template for a request and
prints a relabel checklist, or signals the procedural fallback (exit 3) when no
template clears the selector's confidence threshold.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from conftest import load_script

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "assets" / "templates"

scaffold = load_script("scaffold-diagram")


def _cell_count(path: Path) -> int:
    root = ET.parse(path).getroot()
    return sum(1 for e in root.iter() if e.tag in ("mxCell", "object"))


def test_scaffold_copies_a_real_task_center_template(tmp_path):
    out = tmp_path / "tc.drawio"
    rc = scaffold.main(["SAP Task Center central inbox L1", "--out", str(out)])
    assert rc == 0
    assert out.exists()
    # It is a byte-for-byte copy of a real SAP template.
    src = TEMPLATES / "SAP_Task_Center_L1.drawio"
    assert out.read_bytes() == src.read_bytes()
    # …and it is valid, non-trivial draw.io XML.
    assert _cell_count(out) > 10


def test_scaffold_reports_relabels_and_alternates(tmp_path, capsys):
    out = tmp_path / "tc.drawio"
    rc = scaffold.main(["SAP Task Center central inbox L1", "--out", str(out), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "task-center" in payload["template"]
    assert payload["reasons"]
    assert payload["alternates"]  # other candidates surfaced
    # The request words absent from the template become an add/relabel checklist.
    assert "inbox" in payload["relabel_add"] or "central" in payload["relabel_add"]
    # Template services not in the request are flagged for relabel/removal.
    assert any("S/4HANA" in s for s in payload["relabel_swap"])


def test_explicit_template_overrides_ranking(tmp_path):
    out = tmp_path / "x.drawio"
    rc = scaffold.main(["anything at all", "--template", "sap-task-center-l2",
                        "--out", str(out)])
    assert rc == 0
    assert out.read_bytes() == (TEMPLATES / "SAP_Task_Center_L2.drawio").read_bytes()


def test_no_close_template_signals_procedural_fallback(tmp_path, capsys):
    out = tmp_path / "none.drawio"
    rc = scaffold.main(["a picnic in the park with sandwiches", "--out", str(out)])
    assert rc == scaffold.EXIT_NO_TEMPLATE  # distinct, nonzero
    assert not out.exists()  # nothing copied
    assert "generate-drawio.py" in capsys.readouterr().out


def test_dry_run_copies_nothing(tmp_path, capsys):
    out = tmp_path / "dry.drawio"
    rc = scaffold.main(["SAP Task Center inbox", "--out", str(out), "--dry-run", "--json"])
    assert rc == 0
    assert not out.exists()
    payload = json.loads(capsys.readouterr().out)
    assert payload["chosen"]["id"]


def test_unknown_explicit_template_errors(tmp_path, capsys):
    rc = scaffold.main(["x", "--template", "does-not-exist", "--out", str(tmp_path / "o.drawio")])
    assert rc == 1


def test_refuses_to_overwrite_without_force(tmp_path):
    out = tmp_path / "tc.drawio"
    out.write_text("existing", encoding="utf-8")
    rc = scaffold.main(["SAP Task Center inbox", "--out", str(out)])
    assert rc == 1
    assert out.read_text(encoding="utf-8") == "existing"
    # …but --force replaces it.
    rc = scaffold.main(["SAP Task Center inbox", "--out", str(out), "--force"])
    assert rc == 0
    assert out.read_text(encoding="utf-8") != "existing"
