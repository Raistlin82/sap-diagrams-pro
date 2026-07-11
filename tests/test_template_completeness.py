# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""tests/test_template_completeness.py — template-informed completeness:
suggest_extras() triage + the --suggest CLI surface. See
docs/superpowers/specs/2026-07-11-template-informed-completeness-design.md."""
from __future__ import annotations

import json
from pathlib import Path

from conftest import load_script

sel = load_script("select-template")


def _entry(eid, service_tokens, aliases=None):
    return {"id": eid, "file": f"{eid}.drawio", "zoneCount": 6,
            "serviceTokens": service_tokens, "scenarioAliases": aliases or []}


def test_consensus_two_of_candidates_suggested():
    cands = [
        _entry("c1", ["Work Zone", "Destination Service", "Joule"]),
        _entry("c2", ["Work Zone", "Destination Service"]),
        _entry("c3", ["Work Zone", "Cloud Connector"]),
    ]
    out = sel.suggest_extras(cands, requested=["Work Zone"], best_practice=[])
    labels = {s["label"] for s in out}
    assert "Destination Service" in labels
    assert "Joule" not in labels
    dest = next(s for s in out if s["label"] == "Destination Service")
    assert dest["consensus"] == 2 and dest["candidates"] == 3
    assert dest["bestPractice"] is False


def test_single_template_extra_promoted_by_best_practice():
    cands = [
        _entry("c1", ["Work Zone", "Audit Log"]),
        _entry("c2", ["Work Zone"]),
    ]
    out = sel.suggest_extras(cands, requested=["Work Zone"],
                             best_practice=["Audit Log Service"])
    audit = next((s for s in out if s["label"] == "Audit Log"), None)
    assert audit is not None
    assert audit["bestPractice"] is True
    assert "best-practice" in audit["reason"]


def test_sap_prefixed_spellings_merge():
    cands = [
        _entry("c1", ["Work Zone", "SAP Destination Service"]),
        _entry("c2", ["Work Zone", "Destination Service"]),
    ]
    out = sel.suggest_extras(cands, requested=["Work Zone"], best_practice=[])
    dests = [s for s in out if "Destination" in s["label"]]
    assert len(dests) == 1
    assert dests[0]["consensus"] == 2


def test_cap_respected():
    names = [f"Svc{i}" for i in range(8)]
    cands = [_entry("c1", ["Work Zone", *names]),
             _entry("c2", ["Work Zone", *names])]
    out = sel.suggest_extras(cands, requested=["Work Zone"], best_practice=[], cap=3)
    assert len(out) == 3


def test_already_requested_never_suggested():
    cands = [_entry("c1", ["Work Zone", "Integration Suite"]),
             _entry("c2", ["Work Zone", "Integration Suite"])]
    out = sel.suggest_extras(cands, requested=["Work Zone", "Integration Suite"],
                             best_practice=[])
    assert all(s["label"] != "Integration Suite" for s in out)


def test_html_label_normalised():
    cands = [_entry("c1", ["Work Zone", "<b>Destination Service</b>"]),
             _entry("c2", ["Work Zone", "Destination Service"])]
    out = sel.suggest_extras(cands, requested=["Work Zone"], best_practice=[])
    dests = [s for s in out if "Destination" in s["label"]]
    assert len(dests) == 1 and "<b>" not in dests[0]["label"]


def test_sap_prefixed_request_not_resuggested():
    # Requested spelling carries the SAP prefix; template tokens are bare.
    # The bare token must NOT come back as a suggestion.
    cands = [_entry("c1", ["Work Zone", "Destination Service"]),
             _entry("c2", ["Work Zone", "Destination Service"])]
    out = sel.suggest_extras(cands, requested=["SAP Work Zone"], best_practice=[])
    assert all(s["label"] != "Work Zone" for s in out)
    # (Destination Service is still legitimately suggested.)
    assert any("Destination" in s["label"] for s in out)


# --------------------------------------------------------------------------- #
# CLI: --suggest
# --------------------------------------------------------------------------- #
def test_cli_suggest_emits_suggestions(capsys):
    rc = sel.main([
        "sap build work zone with s4hana pce and build process automation",
        "--components", "Build Work Zone,S/4HANA,Build Process Automation",
        "--suggest", "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "suggestions" in payload
    assert isinstance(payload["suggestions"], list)
    for s in payload["suggestions"]:
        assert set(s) >= {"label", "consensus", "candidates", "bestPractice", "reason"}


def test_cli_suggest_requires_components(capsys):
    rc = sel.main(["some request", "--suggest", "--json"])
    assert rc == 2  # --suggest without --components is an error


def test_cli_no_suggest_is_backward_compatible(capsys):
    rc = sel.main([
        "SAP Build Process Automation L2 with Task Center",
        "--components", "Build Process Automation,Integration Suite,Cloud ALM",
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "suggestions" not in payload
    assert set(payload["delta"]) == {"remove", "relabel", "add"}
