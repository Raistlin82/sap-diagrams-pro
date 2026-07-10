# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""tests/test_scaffold_extend_integration.py — the hybrid *scaffold-and-extend*
path, end to end, through the real CLIs (Step 5.5).

The single big test exercises the whole surgical-edit workflow on a *real* SAP
reference template and then gates the result with the SAME authoritative dual
gate the procedural path uses:

  scaffold BPA-L2 by id  →  remove one out-of-scope node (legacy "SAP ECC")
  →  relabel one node ("SAP Cloud Solutions" → "SAP SuccessFactors")
  →  add a genuinely-missing component ("SAP Cloud ALM", icon resolved) via
     add-node.py --mode slot  →  wire it with add-edge.py
  →  validate-drawio --strict (0 CRITICAL) + check-composition (0 FAIL)
     + score-diagram --sap-like (≥ 85) + score-diagram --corpus (≥ 82).

Everything runs through subprocess (not in-process ``main()``) precisely because
this is the *integration* guard: it pins the real argv/exit-code contract of the
five edit/gate CLIs stitched together, the way the skill drives them.

The real cell ids/labels below were read out of the freshly-scaffolded file (see
the ``sanity`` block, which fails loudly if the template ever drifts) — they are
NOT assumed.

Also carries the demos byte-identical regression: the four shipped demos, when
regenerated, must reproduce their committed .drawio verbatim (modulo the volatile
``modified=`` timestamp) — proof the scaffold-and-extend edit tools never touched
the procedural engine core.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
TEMPLATES = ROOT / "assets" / "templates"

# Real cells found by reading the scaffolded SAP_Build_Process_Automation_L2
# template (INSPECTED, not assumed — the sanity block guards against drift).
ECC_ID = "ZNLiSohyDAu_GED2eyi8-3"                 # "SAP ECC" — legacy, out of scope
RELABEL_ID = "-Aj5rOMPWS9pz5DeyN8X-28"            # "SAP Cloud Solutions"
MAIN_FRAME_ID = "-Aj5rOMPWS9pz5DeyN8X-1"          # BTP mega-frame (add-node group)
TRANSPORT_ID = "-Aj5rOMPWS9pz5DeyN8X-76"          # "Cloud Transport Management"


def _run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        capture_output=True, text=True,
    )


def _cells(path: Path) -> list[ET.Element]:
    return list(ET.parse(path).getroot().iter("mxCell"))


def _by_id(cells: list[ET.Element], cid: str) -> ET.Element | None:
    return next((c for c in cells if c.get("id") == cid), None)


def _label(cell: ET.Element | None) -> str:
    if cell is None:
        return ""
    v = re.sub("<[^>]+>", " ", cell.get("value") or "")
    return re.sub(r"\s+", " ", html.unescape(v)).strip()


def test_scaffold_extend_dual_gate(tmp_path):
    base = tmp_path / "base.drawio"

    # ── scaffold the real BPA-L2 template by id ──────────────────────────────
    r = _run("scaffold-diagram.py", "--template",
             "sap-build-process-automation-l2", "--out", str(base), "--force")
    assert r.returncode == 0, r.stdout + r.stderr
    assert base.exists()
    pristine = base.read_bytes()                      # snapshot of the scaffold

    # sanity: the cells the delta targets are really there (guards template drift)
    cells = _cells(base)
    assert _label(_by_id(cells, ECC_ID)) == "SAP ECC"
    assert _label(_by_id(cells, RELABEL_ID)) == "SAP Cloud Solutions"
    assert _by_id(cells, MAIN_FRAME_ID) is not None
    assert _label(_by_id(cells, TRANSPORT_ID)) == "Cloud Transport Management"

    # ── delta 1: remove one out-of-scope node (legacy ECC) ───────────────────
    r = _run("remove-cell.py", str(base), "--id", ECC_ID, "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["removed"] == [ECC_ID]

    # ── delta 2: relabel one node to a concrete SaaS ─────────────────────────
    # --set=<id>=<label>: the '=' form is required because the id starts with
    # '-' (argparse would else read it as a flag); relabel splits on the 1st '='.
    r = _run("relabel.py", str(base),
             f"--set={RELABEL_ID}=SAP SuccessFactors", "--no-backup")
    assert r.returncode == 0, r.stdout + r.stderr

    # ── delta 3: add a genuinely-missing component + wire it ─────────────────
    r = _run("add-node.py", str(base), f"--group={MAIN_FRAME_ID}",
             "--label", "SAP Cloud ALM", "--service", "SAP Cloud ALM",
             "--mode", "slot", f"--near={TRANSPORT_ID}", "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    node_id = json.loads(r.stdout)["id"]

    r = _run("add-edge.py", str(base), f"--source={node_id}",
             f"--target={TRANSPORT_ID}", "--flowFamily", "transport",
             "--pill", "REST / OData", "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    edge_id = json.loads(r.stdout)["edge"]

    # ── the delta really happened (not a no-op that trivially passes gates) ──
    cells = _cells(base)
    labels = {_label(c) for c in cells}
    assert _by_id(cells, ECC_ID) is None and "SAP ECC" not in labels
    assert "SAP Cloud Solutions" not in labels and "SAP SuccessFactors" in labels
    alm = _by_id(cells, node_id)
    assert alm is not None and _label(alm) == "SAP Cloud ALM"
    assert alm.get("parent") == MAIN_FRAME_ID
    assert "image=" in (alm.get("style") or ""), "add-node did not resolve an icon"
    edge = _by_id(cells, edge_id)
    assert edge is not None and edge.get("edge") == "1"
    assert edge.get("source") == node_id and edge.get("target") == TRANSPORT_ID
    assert base.read_bytes() != pristine, "the extended file must differ from the scaffold"

    # ── authoritative dual gate on the scaffolded + extended artifact ────────
    assert _run("validate-drawio.py", str(base), "--strict").returncode == 0, \
        "validate-drawio --strict must exit 0 (no CRITICAL)"
    assert _run("check-composition.py", str(base)).returncode == 0, \
        "check-composition must exit 0 (0 FAIL)"

    sap = json.loads(_run("score-diagram.py", "--sap-like", str(base),
                          "--json").stdout)["score"]
    corpus_proc = _run("score-diagram.py", "--corpus", str(TEMPLATES),
                       str(base), "--min-score", "82", "--json")
    corpus = json.loads(corpus_proc.stdout)["score"]

    # Hard gate: corpus similarity + a clean validator + 0 FAIL composition.
    assert corpus_proc.returncode == 0, "corpus score fell below --min-score 82"
    assert corpus >= 82.0, f"corpus similarity {corpus} < 82"

    # MEASURED (2026-07-10, deterministic): this real BPA-L2 scaffold+extend
    # artifact scores sap-like = 90.5 and corpus = 99.0. The plan's "real
    # templates may dip below 85 on the reference-free --sap-like scorer"
    # concern did NOT materialise here — 90.5 clears 85 comfortably — so we
    # assert the plan's target floor (85) rather than the measured value.
    assert sap >= 85.0, f"sap-like {sap} < 85 (measured 90.5 at authoring time)"


# ── demos byte-identical regression: engine core untouched ──────────────────
DEMOS = [
    "demo/nova/nova-L0.json",
    "demo/nova/nova-L1.json",
    "demo/nova/nova-L2.json",
    "demo/replicas/task-center-L1.json",
]

_MODIFIED_RE = re.compile(r' modified="[^"]*"')


def _strip_volatile(xml: str) -> str:
    """Drop the only run-to-run volatile field: the mxfile ``modified=`` stamp."""
    return _MODIFIED_RE.sub("", xml)


def test_demos_byte_identical(tmp_path):
    """Regenerate each shipped demo and assert it reproduces the committed
    .drawio at HEAD verbatim (modulo ``modified=``). The scaffold-and-extend
    edit tools live entirely outside generate-drawio.py's code path, so this is
    the guard that they didn't perturb the deterministic engine core."""
    for json_rel in DEMOS:
        drawio_rel = json_rel[:-len(".json")] + ".drawio"
        committed = subprocess.run(
            ["git", "show", f"HEAD:{drawio_rel}"],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert committed.returncode == 0, f"git show HEAD:{drawio_rel}: {committed.stderr}"

        regen = _run("generate-drawio.py", str(ROOT / json_rel), "--out", "-")
        assert regen.returncode == 0, regen.stderr

        assert _strip_volatile(regen.stdout) == _strip_volatile(committed.stdout), \
            f"{drawio_rel} is no longer byte-identical to HEAD (engine core drifted)"
