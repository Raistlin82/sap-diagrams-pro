# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""tests/test_select_template_coverage.py — the --components coverage report and
the 3-way scaffold / scaffold-extend / generate decision.

select-template.py already ranks the template corpus against a free-text
request. THIS surface adds, on top of the ranked winner, a component-coverage
report (which requested components are PRESENT / MISSING in the candidate, and
which template components are EXTRA) plus a routing decision:

  * scaffold        — the winner covers everything, nothing to strip: relabel-only.
  * scaffold-extend — close enough to copy, but needs a bounded set of surgical
                      edits (a delta plan of remove / relabel / add).
  * generate        — too far off (low coverage or a heavy structural extra):
                      fall back to the procedural engine.

The pre-existing ranking behaviour (no --components) is asserted unchanged in
tests/test_select_template.py; these tests only exercise the new coverage path.
"""
from __future__ import annotations

import json
from pathlib import Path

from conftest import load_script

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "assets" / "template-index.json"

sel = load_script("select-template")

# A real template that genuinely lacks Cloud ALM (see template-index.json).
BPA_ID = "sap-build-process-automation-l2"


def _entry(entry_id: str) -> dict:
    index = sel.load_index(INDEX)
    for e in index["templates"]:
        if e["id"] == entry_id:
            return e
    raise AssertionError(f"template {entry_id!r} not in index")


def _write_drawio(path: Path, cells: list[tuple[str, str | None, str]]) -> None:
    """Write a minimal .drawio. ``cells`` are ``(id, value|None, parent)`` — a
    cell is a container iff some other cell names it as ``parent``."""
    body = ['<mxfile><diagram name="t"><mxGraphModel><root>',
            '<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
    for cid, value, parent in cells:
        val = f' value="{value}"' if value is not None else ""
        body.append(
            f'<mxCell id="{cid}"{val} parent="{parent}" vertex="1">'
            f'<mxGeometry x="0" y="0" width="80" height="40" as="geometry"/></mxCell>')
    body.append("</root></mxGraphModel></diagram></mxfile>")
    path.write_text("".join(body), encoding="utf-8")


# --------------------------------------------------------------------------- #
# enumeration
# --------------------------------------------------------------------------- #
def test_enumeration_ignores_labelTokens():
    # A stray "…px" fragment in labelTokens must NOT surface as a component;
    # enumeration reads serviceTokens + scenarioAliases only.
    entry = {
        "serviceTokens": ["SAP Integration Suite"],
        "scenarioAliases": ["Task Center"],
        "labelTokens": ["16px", "px", "noise"],
    }
    comps = sel.enumerate_components(entry)
    assert comps == ["SAP Integration Suite", "Task Center"]
    assert "px" not in comps and "16px" not in comps


# --------------------------------------------------------------------------- #
# coverage report: present / missing
# --------------------------------------------------------------------------- #
def test_coverage_present_missing():
    entry = _entry(BPA_ID)
    rep = sel.coverage_report(
        entry, ["Build Process Automation", "Integration Suite", "Cloud ALM"])
    assert "Build Process Automation" in rep["present"]
    assert "Integration Suite" in rep["present"]
    assert "Cloud ALM" in rep["missing"]
    assert "Cloud ALM" not in rep["present"]


# --------------------------------------------------------------------------- #
# light vs heavy extras
# --------------------------------------------------------------------------- #
def test_extra_light_vs_heavy(tmp_path):
    _write_drawio(tmp_path / "syn.drawio", [
        ("bigzone", "BigZone", "1"),
        ("kid", "inner", "bigzone"),      # makes BigZone a container -> heavy
        ("leaf", "SmallSvc", "1"),        # no children -> light
    ])
    entry = {
        "id": "syn", "file": "syn.drawio", "zoneCount": 6,
        "serviceTokens": ["BigZone", "SmallSvc"], "scenarioAliases": [],
    }
    rep = sel.coverage_report(entry, [], templates_dir=tmp_path)
    weights = {e["label"]: e["weight"] for e in rep["extra"]}
    assert weights["BigZone"] == "heavy"
    assert weights["SmallSvc"] == "light"
    assert rep["heavyCount"] == 1


# --------------------------------------------------------------------------- #
# decisions
# --------------------------------------------------------------------------- #
def test_decision_pure_relabel():
    # Fully covered, nothing extra -> pure relabel scaffold.
    entry = _entry(BPA_ID)
    requested = sel.enumerate_components(entry)
    result = sel.decide(entry, requested, recommended=True)
    assert result["decision"] == "scaffold"
    assert result["missing"] == []
    assert result["delta"]["remove"] == []
    assert result["delta"]["add"] == []
    assert result["delta"]["relabel"]  # relabel-only plan is non-empty


def test_decision_scaffold_extend(tmp_path):
    _write_drawio(tmp_path / "ext.drawio", [
        ("a", "Alpha", "1"), ("b", "Beta", "1"), ("g", "Gamma", "1"),
    ])
    entry = {
        "id": "ext", "file": "ext.drawio", "zoneCount": 6,
        "serviceTokens": ["Alpha", "Beta", "Gamma"], "scenarioAliases": [],
    }
    # Alpha+Beta present, Delta missing, Gamma extra (leaf -> light).
    result = sel.decide(entry, ["Alpha", "Beta", "Delta"], recommended=True,
                        templates_dir=tmp_path)
    assert result["decision"] == "scaffold-extend"
    assert result["coverage"] >= sel.COVERAGE_MIN
    assert "Delta" in result["delta"]["add"]
    assert "Gamma" in result["delta"]["remove"]
    assert result["delta"]["relabel"]


def test_decision_generate_when_low_coverage():
    entry = _entry(BPA_ID)
    # None of these are in the BPA template -> coverage 0.0 < COVERAGE_MIN.
    requested = ["Cloud ALM", "SAP Signavio", "SAP Ariba",
                 "SAP Fieldglass", "SAP Concur"]
    result = sel.decide(entry, requested, recommended=True)
    assert result["coverage"] < sel.COVERAGE_MIN
    assert result["decision"] == "generate"


# --------------------------------------------------------------------------- #
# heavy guard
# --------------------------------------------------------------------------- #
def test_heavy_guard_zone_ratio(tmp_path):
    cells = [("a", "Alpha", "1"),
             ("c", "Container", "1"), ("kid", "inner", "c")]
    _write_drawio(tmp_path / "hga.drawio", cells)
    _write_drawio(tmp_path / "hgb.drawio", cells)

    base = {"serviceTokens": ["Alpha", "Container"], "scenarioAliases": []}
    # Many zones: one heavy extra is tolerated (1 <= zoneCount/3).
    many = {**base, "id": "hga", "file": "hga.drawio", "zoneCount": 13}
    # Few zones (<=3): the same heavy extra is blocked by the zoneCount/3 clause.
    few = {**base, "id": "hgb", "file": "hgb.drawio", "zoneCount": 2}

    r_many = sel.decide(many, ["Alpha"], recommended=True, templates_dir=tmp_path)
    r_few = sel.decide(few, ["Alpha"], recommended=True, templates_dir=tmp_path)

    assert r_many["heavyGuardOk"] is True
    assert r_many["decision"] == "scaffold-extend"
    assert r_few["heavyGuardOk"] is False
    assert r_few["decision"] == "generate"


# --------------------------------------------------------------------------- #
# CLI wiring
# --------------------------------------------------------------------------- #
def test_cli_components_json(capsys):
    rc = sel.main([
        "SAP Build Process Automation L2 with Task Center",
        "--components", "Build Process Automation,Integration Suite,Cloud ALM",
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] in {"scaffold", "scaffold-extend", "generate"}
    assert isinstance(payload["coverage"], float)
    assert set(payload["delta"]) == {"remove", "relabel", "add"}
