# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""tests/test_ir_v2.py — Task 4 (IR v2 parsing + validate-ir.py).

Two families:
  * Parsing — generate-drawio.py's dataclasses/parse_json gained new, all-
    optional v2 fields (Group.kind/badges, Node.type/capabilities,
    Edge.pill/flowFamily, Diagram.layoutHints/branding/badges). v1 IRs must
    still parse byte-for-byte the same way, with every new field defaulting
    to None; the v2 fixture must parse with the new fields populated.
  * validate-ir.py — a standalone CLI (imported in-process via `load_script`,
    the same dashed-module technique tests/conftest.py uses for
    generate-drawio.py) that re-checks enums/references parse_json itself
    leaves unchecked for the new fields, exiting 0 + "OK" when valid and 2
    with an actionable "ERROR <where>: <what>. Allowed: <values>" message per
    problem when not.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import load_script

ROOT = Path(__file__).resolve().parent.parent
V2_FIXTURE = ROOT / "tests" / "fixtures" / "ir-v2-sample.json"
V1_DEMOS = [
    ROOT / "demo" / "nova" / "nova-L0.json",
    ROOT / "demo" / "nova" / "nova-L1.json",
    ROOT / "demo" / "nova" / "nova-L2.json",
]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────────────
# Module loading — both scripts import cleanly via the dashed-module
# technique (Task 4 "Before You Begin" check).
# ─────────────────────────────────────────────────────────────────────────
def test_scripts_load_via_dashed_module_technique():
    gen = load_script("generate-drawio")
    vir = load_script("validate-ir")
    assert hasattr(gen, "parse_json") and hasattr(gen, "Diagram")
    assert hasattr(vir, "main") and hasattr(vir, "validate_diagram")
    # validate-ir.py must reuse generate-drawio's own parser/dataclasses,
    # not a re-implementation — assert object identity, not just equality.
    assert vir.parse_json is gen.parse_json
    assert vir.Diagram is gen.Diagram


# ─────────────────────────────────────────────────────────────────────────
# v1 compatibility — every new field defaults to None.
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", V1_DEMOS, ids=lambda p: p.stem)
def test_v1_demo_parses_with_v2_fields_defaulting_to_none(path):
    gen = load_script("generate-drawio")
    diagram = gen.parse_json(_load(path))
    assert isinstance(diagram, gen.Diagram)
    assert diagram.layoutHints is None
    assert diagram.branding is None
    assert diagram.badges is None
    assert diagram.groups and diagram.nodes and diagram.edges  # sanity: not empty
    for g in diagram.groups:
        assert g.kind is None
        assert g.badges is None
    for n in diagram.nodes:
        assert n.type is None
        assert n.capabilities is None
    for e in diagram.edges:
        assert e.pill is None
        assert e.flowFamily is None


# ─────────────────────────────────────────────────────────────────────────
# v2 fixture — parses with the new fields populated as designed.
# ─────────────────────────────────────────────────────────────────────────
def test_v2_fixture_parses_with_capabilities_list():
    gen = load_script("generate-drawio")
    diagram = gen.parse_json(_load(V2_FIXTURE))

    products = [n for n in diagram.nodes if n.type == "product"]
    assert {n.id for n in products} == {"cloud-alm", "bpa"}
    for n in products:
        assert isinstance(n.capabilities, list)
        assert len(n.capabilities) == 4
        for cap in n.capabilities:
            assert isinstance(cap["label"], str) and cap["label"]
            # icon is optional: present on some, absent on others in the fixture
            assert "icon" not in cap or isinstance(cap["icon"], str)


def test_v2_fixture_nested_subaccounts():
    gen = load_script("generate-drawio")
    diagram = gen.parse_json(_load(V2_FIXTURE))
    by_id = {g.id: g for g in diagram.groups}

    test_acc = by_id["subaccount-test"]
    prod_acc = by_id["subaccount-production"]
    assert test_acc.type == "subaccount" and prod_acc.type == "subaccount"
    assert test_acc.parent == "btp"
    assert prod_acc.parent == "subaccount-test"


def test_v2_fixture_governance_cloud_tier_and_top_level_identity():
    gen = load_script("generate-drawio")
    diagram = gen.parse_json(_load(V2_FIXTURE))
    by_id = {g.id: g for g in diagram.groups}

    assert by_id["governance"].type == "governance"
    assert by_id["governance"].parent is None

    tier = by_id["cloud-tier-right"]
    assert tier.type == "cloud-tier"
    assert tier.kind == "private"
    assert tier.badges == {"hyperscalers": ["azure"], "runtimes": ["cloud-foundry"]}

    # "identity group top-level": not parented to btp (or anything else).
    assert by_id["identity"].parent is None
    identity_services = {n.service for n in diagram.nodes if n.group == "identity"}
    assert identity_services == {"Identity Authentication", "Authorization & Trust Mgmt"}


def test_v2_fixture_edges_flow_family_and_pill_mix():
    gen = load_script("generate-drawio")
    diagram = gen.parse_json(_load(V2_FIXTURE))
    assert len(diagram.edges) == 5

    by_id = {e.id: e for e in diagram.edges}
    assert {e.flowFamily for e in diagram.edges if e.flowFamily is not None} == {
        "identity", "provisioning", "master-data", "transport",
    }
    # e5 omits both flowFamily and pill entirely — proves v1-style bare
    # edges keep working inside a v2 IR.
    assert by_id["e5"].flowFamily is None
    assert by_id["e5"].pill is None
    # A mix of edges carry a protocol pill; at least one (e3) doesn't.
    assert by_id["e1"].pill == "SAML2/OIDC"
    assert by_id["e3"].pill is None


def test_v2_fixture_metadata_branding_badges_and_layout_hints():
    gen = load_script("generate-drawio")
    diagram = gen.parse_json(_load(V2_FIXTURE))
    assert diagram.branding == {"customerLogo": "acme", "partnerWatermark": "lutech"}
    assert diagram.badges == {"hyperscalers": ["azure"], "runtimes": ["cloud-foundry"]}
    assert diagram.layoutHints == [{"op": "toggle_separator", "value": True}]


# ─────────────────────────────────────────────────────────────────────────
# validate-ir.py — happy path (in-process via load_script).
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", V1_DEMOS + [V2_FIXTURE], ids=lambda p: p.stem)
def test_validate_ir_ok_on_valid_irs(path, capsys):
    vir = load_script("validate-ir")
    rc = vir.main([str(path)])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.strip() == "OK"


# ─────────────────────────────────────────────────────────────────────────
# validate-ir.py — the exact Task 4 regression case: a typo'd flowFamily
# must exit 2 with a message that names the correct value among the
# allowed ones.
# ─────────────────────────────────────────────────────────────────────────
def test_validate_ir_rejects_bad_flow_family(tmp_path, capsys):
    payload = _load(V2_FIXTURE)
    payload["edges"][0]["flowFamily"] = "identiy"  # typo, on purpose
    bad_path = tmp_path / "bad-flow-family.json"
    bad_path.write_text(json.dumps(payload), encoding="utf-8")

    vir = load_script("validate-ir")
    rc = vir.main([str(bad_path)])
    captured = capsys.readouterr()

    assert rc == 2
    assert "identity" in captured.out
    assert captured.out.strip().startswith("ERROR")
    assert "Allowed:" in captured.out


@pytest.mark.parametrize(
    "mutate, where_fragment",
    [
        (lambda p: p["groups"].__setitem__(0, {**p["groups"][0], "type": "bogus-type"}), "group 'governance'"),
        (lambda p: [g.update(kind="bogus-kind") for g in p["groups"] if g["id"] == "cloud-tier-right"], "group 'cloud-tier-right'"),
        (lambda p: [n.update(type="bogus-node-type") for n in p["nodes"] if n["id"] == "pce"], "node 'pce'"),
    ],
)
def test_validate_ir_rejects_bad_enums(tmp_path, capsys, mutate, where_fragment):
    payload = _load(V2_FIXTURE)
    mutate(payload)
    bad_path = tmp_path / "bad-enum.json"
    bad_path.write_text(json.dumps(payload), encoding="utf-8")

    vir = load_script("validate-ir")
    rc = vir.main([str(bad_path)])
    captured = capsys.readouterr()

    assert rc == 2
    assert where_fragment in captured.out
    assert "Allowed:" in captured.out


def test_validate_ir_rejects_bad_capability_shape(tmp_path, capsys):
    payload = _load(V2_FIXTURE)
    payload["nodes"][0]["capabilities"][0] = {"icon": "no-label"}  # missing label
    bad_path = tmp_path / "bad-capability.json"
    bad_path.write_text(json.dumps(payload), encoding="utf-8")

    vir = load_script("validate-ir")
    rc = vir.main([str(bad_path)])
    captured = capsys.readouterr()

    assert rc == 2
    assert "capabilities[0]" in captured.out
    assert "label" in captured.out


def test_validate_ir_rejects_dangling_parent_ref(tmp_path, capsys):
    payload = _load(V2_FIXTURE)
    for g in payload["groups"]:
        if g["id"] == "subaccount-test":
            g["parent"] = "no-such-group"
    bad_path = tmp_path / "dangling-parent.json"
    bad_path.write_text(json.dumps(payload), encoding="utf-8")

    vir = load_script("validate-ir")
    rc = vir.main([str(bad_path)])
    captured = capsys.readouterr()

    assert rc == 2
    assert "no-such-group" in captured.out
    assert "does not exist" in captured.out


def test_validate_ir_rejects_parent_cycle(tmp_path, capsys):
    payload = _load(V2_FIXTURE)
    for g in payload["groups"]:
        if g["id"] == "btp":
            g["parent"] = "subaccount-production"  # btp -> ... -> btp
    bad_path = tmp_path / "parent-cycle.json"
    bad_path.write_text(json.dumps(payload), encoding="utf-8")

    vir = load_script("validate-ir")
    rc = vir.main([str(bad_path)])
    captured = capsys.readouterr()

    assert rc == 2
    assert "cycle" in captured.out


def test_validate_ir_reports_missing_file(tmp_path, capsys):
    vir = load_script("validate-ir")
    rc = vir.main([str(tmp_path / "does-not-exist.json")])
    captured = capsys.readouterr()
    assert rc == 2
    assert "ERROR" in captured.err


def test_validate_ir_reports_invalid_json(tmp_path, capsys):
    bad_path = tmp_path / "not-json.json"
    bad_path.write_text("{not valid json", encoding="utf-8")
    vir = load_script("validate-ir")
    rc = vir.main([str(bad_path)])
    captured = capsys.readouterr()
    assert rc == 2
    assert "ERROR" in captured.err


# ─────────────────────────────────────────────────────────────────────────
# End-to-end smoke test: the real CLI entry point, as a subprocess, the way
# the SKILL instructs authors to run it.
# ─────────────────────────────────────────────────────────────────────────
def test_validate_ir_cli_subprocess_smoke():
    ok = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate-ir.py"), str(V2_FIXTURE)],
        capture_output=True, text=True,
    )
    assert ok.returncode == 0
    assert ok.stdout.strip() == "OK"

    bad = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate-ir.py"), str(ROOT / "tests" / "fixtures" / "mini-library.xml")],
        capture_output=True, text=True,
    )
    assert bad.returncode == 2
