# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
import json, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

def run_harvest(tmp_path, sources):
    out = subprocess.run([sys.executable, ROOT/"scripts/harvest-brand-assets.py",
                          "--manifest", ROOT/"assets/brand-pack.manifest.json",
                          "--out-public", tmp_path/"pub", "--out-local", tmp_path/"loc",
                          *map(str, sources)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return tmp_path

def test_exemplar_assets_go_local(tmp_path):
    run_harvest(tmp_path, [ROOT/"tests/fixtures/mini-exemplar.drawio"])
    loc = json.loads((tmp_path/"loc/index.json").read_text())
    assert {"aws-badge", "lutech-logo"} <= set(loc)      # matched by value_regex
    assert loc["aws-badge"]["dataUri"].startswith("data:image/")

def test_public_assets_come_from_official_repo_only(tmp_path):
    run_harvest(tmp_path, [ROOT/"tests/fixtures/mini-exemplar.drawio"])
    pub = json.loads((tmp_path/"pub/index.json").read_text()) if (tmp_path/"pub/index.json").exists() else {}
    assert all(v["source"] == "official" for v in pub.values())
