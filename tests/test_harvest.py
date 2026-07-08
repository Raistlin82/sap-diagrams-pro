# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
import json, shutil, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
LIB_SUBPATH = "assets/shape-libraries-and-editable-presets/draw.io"

def run_harvest(tmp_path, sources, manifest=None, official_repo=None):
    cmd = [sys.executable, ROOT/"scripts/harvest-brand-assets.py",
           "--manifest", manifest or ROOT/"assets/brand-pack.manifest.json",
           "--out-public", tmp_path/"pub", "--out-local", tmp_path/"loc"]
    if official_repo is not None:
        cmd += ["--official-repo", official_repo]
    cmd += list(sources)
    out = subprocess.run(list(map(str, cmd)), capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out

def test_exemplar_assets_go_local(tmp_path):
    run_harvest(tmp_path, [ROOT/"tests/fixtures/mini-exemplar.drawio"])
    loc = json.loads((tmp_path/"loc/index.json").read_text())
    assert {"aws-badge", "azure-badge", "lutech-logo"} <= set(loc)   # matched by value_regex
    assert loc["aws-badge"]["dataUri"].startswith("data:image/")
    # azure-badge's image cell is <object label="Azure">-wrapped: the label
    # lives on the wrapper element, not on the mxCell itself.
    assert loc["azure-badge"]["dataUri"].startswith("data:image/")

def test_public_assets_come_from_official_repo_only(tmp_path):
    run_harvest(tmp_path, [ROOT/"tests/fixtures/mini-exemplar.drawio"])
    pub = json.loads((tmp_path/"pub/index.json").read_text()) if (tmp_path/"pub/index.json").exists() else {}
    assert all(v["source"] == "official" for v in pub.values())

def test_official_rich_text_entry_harvests_into_public_index(tmp_path):
    # Regression for c271d27: a library entry whose value holds escaped rich
    # text AND an embedded image must survive the mxlibrary parse chain (a
    # double html.unescape() used to corrupt exactly this shape of entry).
    repo = tmp_path/"official"
    lib_dir = repo/LIB_SUBPATH
    lib_dir.mkdir(parents=True)
    shutil.copy(ROOT/"tests/fixtures/mini-library.xml", lib_dir/"mini-library.xml")
    manifest = tmp_path/"manifest.json"
    manifest.write_text(json.dumps({"assets": [
        {"key": "rich-chip", "public": True, "source": "official",
         "official_ref": "mini-library.xml:Rich Text Chip"},
        {"key": "text-chip", "public": True, "source": "official",
         "official_ref": "mini-library.xml:Text Only Chip"},
    ]}))
    out = run_harvest(tmp_path, [], manifest=manifest, official_repo=repo)
    pub = json.loads((tmp_path/"pub/index.json").read_text())
    assert pub["rich-chip"]["source"] == "official"
    assert pub["rich-chip"]["dataUri"].startswith("data:image/")
    # Text-only entry: title matches but no embedded image -> WARNING + skip.
    assert "text-chip" not in pub
    assert "WARNING" in out.stderr and "text-chip" in out.stderr

def test_invalid_value_regex_warns_and_skips_without_aborting(tmp_path):
    manifest = tmp_path/"manifest.json"
    manifest.write_text(json.dumps({"assets": [
        {"key": "bad-regex", "public": False, "source": "exemplar",
         "match": {"value_regex": "(?i)[unclosed"}},
        {"key": "lutech-logo", "public": False, "source": "exemplar",
         "match": {"value_regex": "(?i)lutech"}},
    ]}))
    out = run_harvest(tmp_path, [ROOT/"tests/fixtures/mini-exemplar.drawio"],
                      manifest=manifest)   # run_harvest asserts exit code 0
    loc = json.loads((tmp_path/"loc/index.json").read_text())
    assert "lutech-logo" in loc and "bad-regex" not in loc
    assert "WARNING" in out.stderr
