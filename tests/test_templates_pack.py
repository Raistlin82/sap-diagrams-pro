# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""The curated templates pack (Desktop scaffold path): build determinism, embedded
XML validity, and scaffold-diagram's loose-file → pack fallback."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACK = ROOT / "assets" / "templates-pack.json"


def _load(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pack_exists_and_is_valid():
    assert PACK.exists(), "run scripts/build-templates-pack.py"
    pack = json.loads(PACK.read_text(encoding="utf-8"))
    tmpls = pack["templates"]
    assert 10 <= len(tmpls) <= 40, f"curated set should be ~21, got {len(tmpls)}"
    for e in tmpls:
        assert e.get("id") and e.get("file")
        xml = e.get("drawioXml")
        assert xml and "<mxGraphModel" in xml or "<mxfile" in xml or "<diagram" in xml, e["id"]
    # every family represented at least once
    fams = {e.get("family") for e in tmpls}
    assert {"identity", "event-driven", "ml", "security"} <= fams


def test_pack_build_is_deterministic():
    bp = _load("build-templates-pack")
    index = json.loads((ROOT / "assets" / "template-index.json").read_text())
    a = [e["id"] for e in bp.curate(index["templates"])]
    b = [e["id"] for e in bp.curate(index["templates"])]
    assert a == b and len(a) == len(set(a))


def test_scaffold_falls_back_to_pack_when_loose_absent(tmp_path):
    """Simulates Desktop: no loose corpus dir, but the pack is present. scaffold's
    template_content must return the embedded XML so the scaffold path still works."""
    sd = _load("scaffold-diagram")
    pack = json.loads(PACK.read_text(encoding="utf-8"))
    entry = pack["templates"][0]
    empty_dir = tmp_path / "no-templates"  # does not exist → forces pack fallback
    content = sd.template_content(entry, empty_dir)
    assert content is not None and content == entry["drawioXml"]


def test_scaffold_prefers_loose_file_when_present(tmp_path):
    sd = _load("scaffold-diagram")
    loose = tmp_path / "templates"
    loose.mkdir()
    entry = {"id": "x", "file": "x.drawio"}
    (loose / "x.drawio").write_text("<mxfile>loose</mxfile>", encoding="utf-8")
    assert sd.template_content(entry, loose) == "<mxfile>loose</mxfile>"
