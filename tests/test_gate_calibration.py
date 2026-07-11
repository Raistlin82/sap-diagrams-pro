# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""tests/test_gate_calibration.py — codifies finding #2: the generate path's
authoritative gate is --sap-like (>=85); --corpus is scaffold-only (procedural
diagrams fingerprint ~55 against the corpus by design)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCORE = ROOT / "scripts" / "score-diagram.py"
DEMOS = [ROOT / "demo" / "nova" / "nova-L1.drawio",
         ROOT / "demo" / "nova" / "nova-L2.drawio",
         ROOT / "demo" / "interactive" / "cap-bwz-bpa-L1.drawio",
         ROOT / "demo" / "replicas" / "task-center-L1.drawio"]


def _sap_like(path: Path) -> float:
    out = subprocess.run([sys.executable, str(SCORE), "--sap-like", str(path), "--json"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)["score"]


@pytest.mark.parametrize("demo", DEMOS, ids=lambda p: p.name)
def test_generate_path_demos_clear_sap_like_85(demo):
    assert demo.exists(), demo
    assert _sap_like(demo) >= 85.0, f"{demo.name} sap-like < 85 (generate-path gate)"
