# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""tests/test_select_template.py — the hybrid scaffold-path selector.

select-template.py ranks the entries in assets/template-index.json against a
free-text request. These tests assert that obvious requests route to the right
family/template, that an explicit level biases ranking, and that a nonsense
request never clears the confidence threshold (so the caller falls back to the
procedural engine).
"""
from __future__ import annotations

import json
from pathlib import Path

from conftest import load_script

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "assets" / "template-index.json"

sel = load_script("select-template")


def _rank(query: str, top: int = 5, level: str | None = None):
    index = sel.load_index(INDEX)
    return sel.rank(index, query, top, level)


def test_index_present():
    assert INDEX.exists(), "template index must be built (build-template-index.py)"
    assert sel.load_index(INDEX).get("templates"), "index has templates"


def test_joule_mcp_request_routes_to_agentic_ai_family():
    ranked = _rank("Joule agent calls S/4HANA via MCP and XSUAA", top=3)
    top = ranked[0]
    assert top.recommended
    assert top.family == "ai"
    assert top.id.startswith("ra0029")
    # both curated agentic aliases fire
    assert "Joule" in top.aliasHits and "MCP" in top.aliasHits


def test_task_center_request_selects_a_task_center_template():
    ranked = _rank("SAP Task Center central inbox L1", top=5)
    top = ranked[0]
    assert top.recommended
    assert "task-center" in top.id or "task_center" in top.id.lower()
    assert "Task Center" in top.aliasHits


def test_explicit_level_biases_ranking():
    # Same request, different requested level → different best template.
    l1 = _rank("SAP Task Center central inbox", top=6, level="L1")[0]
    l2 = _rank("SAP Task Center central inbox", top=6, level="L2")[0]
    assert l1.level == "L1"
    assert l2.level == "L2"
    assert l1.id != l2.id


def test_scenario_alias_is_the_strongest_signal():
    # A single curated alias hit (10) alone must NOT clear the 14.0 bar, so a
    # bare one-word scenario mention doesn't spuriously trigger the scaffold.
    assert sel.RECOMMEND_THRESHOLD > sel.W_ALIAS
    # …but alias + family/service overlap does clear it.
    ranked = _rank("event mesh eventing between S/4HANA systems", top=3)
    assert ranked[0].recommended
    assert ranked[0].score >= sel.RECOMMEND_THRESHOLD


def test_nonsense_request_signals_procedural_fallback():
    ranked = _rank("a picnic in the park with sandwiches", top=3)
    assert not ranked[0].recommended
    assert ranked[0].score < sel.RECOMMEND_THRESHOLD


def test_word_boundary_matching_avoids_substring_false_positives():
    # "storage" must not be treated as containing "rag"; the RAG scenario alias
    # must not fire on it.
    index = sel.load_index(INDEX)
    q_aliases = set(sel.detect_scenarios("object storage bucket lifecycle"))
    assert "RAG" not in q_aliases


def test_cli_json_shape(capsys):
    rc = sel.main(["Joule agent with MCP", "--top", "3", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["threshold"] == sel.RECOMMEND_THRESHOLD
    assert payload["recommended"]  # non-null id
    assert len(payload["candidates"]) == 3
    assert {"id", "score", "reasons", "recommended"} <= set(payload["candidates"][0])


def test_missing_index_exits_2(tmp_path, capsys):
    rc = sel.main(["anything", "--index", str(tmp_path / "nope.json")])
    assert rc == 2
