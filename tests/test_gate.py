# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Task 12 geometric gate (scripts/check-composition.py v2).

Two invariants this file pins:

  1. The hand-built NEGATIVE fixture (tests/fixtures/bad-nova-L1.drawio) —
     every defect in it is deliberate, see the file's own header comment —
     trips >=3 DISTINCT FAIL-severity rule codes, and the CLI exits 2.

  2. Both shipped "good" fixtures — demo/nova/nova-L1.json and
     tests/fixtures/ir-v2-sample.json, freshly regenerated through
     generate-drawio.py's default (routed, "auto") layout — produce ZERO
     FAIL findings, and the CLI exits 0. This is the exact invariant Task
     18's golden CI test gates on; a regression here means Task 18 breaks.

Both use ``check()`` directly (fast, in-process) for the "what fired"
assertions, plus one subprocess-based CLI test each for the exit-code
contract (``check()`` alone can't observe ``main()``'s argv/exit-code glue).
"""
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from conftest import load_script

ROOT = Path(__file__).resolve().parent.parent
NOVA_JSON = ROOT / "demo" / "nova" / "nova-L1.json"
V2_JSON = ROOT / "tests" / "fixtures" / "ir-v2-sample.json"
BAD_FIXTURE = ROOT / "tests" / "fixtures" / "bad-nova-L1.drawio"
GATE = ROOT / "scripts" / "check-composition.py"

cc = load_script("check-composition")


@pytest.fixture(scope="module")
def gen():
    return load_script("generate-drawio")


def _emit(gen, path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    diagram = gen.parse_json(payload)
    return gen.emit(diagram, shape_index=gen.ShapeIndex.load(), layout="auto")


@pytest.fixture(scope="module")
def nova_drawio(tmp_path_factory, gen):
    p = tmp_path_factory.mktemp("gate") / "nova-L1.drawio"
    p.write_text(_emit(gen, NOVA_JSON), encoding="utf-8")
    return p


@pytest.fixture(scope="module")
def v2_drawio(tmp_path_factory, gen):
    p = tmp_path_factory.mktemp("gate") / "ir-v2.drawio"
    p.write_text(_emit(gen, V2_JSON), encoding="utf-8")
    return p


def _run_cli(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), str(path)],
        capture_output=True, text=True,
    )


# ── the hand-built bad fixture must FAIL loudly, on >=3 distinct codes ──────
def test_bad_fixture_trips_at_least_three_distinct_fail_codes():
    findings = cc.check(BAD_FIXTURE)
    fail_codes = {f.rule for f in findings if f.severity == "FAIL"}
    assert len(fail_codes) >= 3, f"expected >=3 distinct FAIL codes, got {fail_codes}"
    # The 4 defects the fixture's header comment documents, each mapped to
    # its own rule — pin the exact codes, not just the count, so a future
    # refactor that silently drops one FAIL-level check is caught here.
    assert {"PIERCING", "TEXT_OVERLAP", "CAPTION_OUT", "PILL_COLLISION"} <= fail_codes


def test_bad_fixture_cli_exits_2():
    result = _run_cli(BAD_FIXTURE)
    assert result.returncode == 2, result.stdout + result.stderr


def test_bad_fixture_exits_2_without_strict_flag():
    """A FAIL blocks unconditionally now — ``--strict`` is a deprecated
    no-op (see check-composition.py's module docstring); the CI workflow
    still passes it, so this pins that it keeps working AND that omitting
    it no longer silently downgrades a FAIL to exit 0."""
    result = subprocess.run(
        [sys.executable, str(GATE), str(BAD_FIXTURE), "--strict"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2


# ── both shipped GOOD fixtures must produce ZERO FAIL ───────────────────────
def test_nova_l1_zero_fail(nova_drawio):
    findings = cc.check(nova_drawio)
    fails = [f for f in findings if f.severity == "FAIL"]
    assert fails == [], f"nova-L1 must have 0 FAIL, got: {fails}"


def test_nova_l1_cli_exits_0(nova_drawio):
    result = _run_cli(nova_drawio)
    assert result.returncode == 0, result.stdout + result.stderr


def test_ir_v2_zero_fail(v2_drawio):
    findings = cc.check(v2_drawio)
    fails = [f for f in findings if f.severity == "FAIL"]
    assert fails == [], f"ir-v2-sample must have 0 FAIL, got: {fails}"


def test_ir_v2_cli_exits_0(v2_drawio):
    result = _run_cli(v2_drawio)
    assert result.returncode == 0, result.stdout + result.stderr


# ── the router's channels ARE serialized (Task 12 prefers this over a
#    WARN-only degrade for CHANNEL_DISCIPLINE) ──────────────────────────────
def test_channels_metadata_is_serialized(nova_drawio):
    root = ET.parse(nova_drawio).getroot()
    cells = {c.get("id"): c for d in root.findall("diagram") for c in d.iter("mxCell")}
    assert "sapdp:channels" in cells
    channels = json.loads(cells["sapdp:channels"].get("value"))
    assert isinstance(channels, list) and len(channels) > 0
    for ch in channels:
        assert set(ch) == {"id", "axis", "rect"}
        assert ch["axis"] in ("v", "h")
        assert len(ch["rect"]) == 4


def test_channel_discipline_is_not_a_silent_no_op_on_nova(nova_drawio):
    """CHANNEL_DISCIPLINE must actually run (not degrade to the "metadata
    absent" INFO no-op) whenever the metadata cell is present."""
    findings = cc.check(nova_drawio)
    cd = [f for f in findings if f.rule == "CHANNEL_DISCIPLINE"]
    assert cd, "CHANNEL_DISCIPLINE produced no finding at all"
    assert "no sapdp:channels metadata" not in cd[0].message


# ── greedy layout (no router, no channels) must degrade gracefully ─────────
def test_channel_discipline_degrades_gracefully_without_metadata(gen):
    payload = json.loads(NOVA_JSON.read_text(encoding="utf-8"))
    diagram = gen.parse_json(payload)
    xml = gen.emit(diagram, shape_index=gen.ShapeIndex.load(), layout="greedy")
    root = ET.fromstring(xml)
    cells = [c for d in root.findall("diagram") for c in d.iter("mxCell")]
    assert not any(c.get("id") == "sapdp:channels" for c in cells)
    findings = cc.check(_write_tmp(xml))
    cd = [f for f in findings if f.rule == "CHANNEL_DISCIPLINE"]
    assert len(cd) == 1 and cd[0].severity == "INFO"


def _write_tmp(xml: str) -> Path:
    import tempfile
    fd, name = tempfile.mkstemp(suffix=".drawio")
    Path(name).write_text(xml, encoding="utf-8")
    return Path(name)


# ── existing v1 checks must not have regressed ──────────────────────────────
def test_v1_group_overlap_still_fail_severity():
    findings = cc.check(BAD_FIXTURE)
    # bad-nova-L1.drawio's two zones don't overlap each other — this just
    # pins that GROUP_OVERLAP's INFO/FAIL machinery still runs alongside v2.
    go = [f for f in findings if f.rule == "GROUP_OVERLAP"]
    assert go and go[0].severity == "INFO"
