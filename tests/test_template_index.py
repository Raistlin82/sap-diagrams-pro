# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""tests/test_template_index.py — contract tests for the SAP reference template
corpus and its index (assets/template-index.json + assets/templates/*.drawio).

These are artifact/contract tests: they read the COMMITTED index directly and
need no source repos or rebuild. They guard the schema a later scaffold selector
depends on.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "assets" / "template-index.json"
TEMPLATES_DIR = ROOT / "assets" / "templates"

REQUIRED_ENTRY_KEYS = {
    "id", "file", "level", "family", "title", "canvas",
    "serviceTokens", "labelTokens", "scenarioAliases", "zoneCount", "iconCount",
    "source", "sourcePath",
}
VALID_LEVELS = {"L0", "L1", "L2", "unknown"}


def _load():
    return json.loads(INDEX.read_text(encoding="utf-8"))


def test_index_is_valid_json_and_nonempty():
    data = _load()
    assert isinstance(data, dict)
    assert "templates" in data and isinstance(data["templates"], list)
    assert len(data["templates"]) > 0, "template index must not be empty"


def test_meta_block_present_and_deterministic():
    meta = _load()["meta"]
    assert meta["templateCount"] == len(_load()["templates"])
    assert isinstance(meta["sources"], list) and len(meta["sources"]) >= 1
    for src in meta["sources"]:
        assert src["repo"] and src["license"]
        # commit SHA recorded (40-char hex) so the corpus is reproducible.
        assert src["commit"] is None or len(src["commit"]) == 40
    # generatedAt must be a static string (derived from source commits), never
    # a wall-clock value — presence is enough to assert here.
    assert "generatedAt" in meta


def test_every_entry_has_required_keys():
    for e in _load()["templates"]:
        missing = REQUIRED_ENTRY_KEYS - set(e)
        assert not missing, f"{e.get('file')} missing keys: {missing}"


def test_levels_are_in_allowed_set():
    for e in _load()["templates"]:
        assert e["level"] in VALID_LEVELS, f"{e['file']} has bad level {e['level']!r}"


def test_ids_are_unique():
    ids = [e["id"] for e in _load()["templates"]]
    assert len(ids) == len(set(ids)), "template ids must be unique"


def test_field_types():
    for e in _load()["templates"]:
        assert isinstance(e["id"], str) and e["id"]
        assert isinstance(e["file"], str) and e["file"].endswith(".drawio")
        assert isinstance(e["title"], str) and e["title"]
        assert isinstance(e["family"], str) and e["family"]
        for lst in ("serviceTokens", "labelTokens", "scenarioAliases"):
            assert isinstance(e[lst], list)
        assert isinstance(e["canvas"], dict)
        assert set(e["canvas"]) == {"w", "h"}
        assert isinstance(e["canvas"]["w"], int) and isinstance(e["canvas"]["h"], int)
        assert isinstance(e["zoneCount"], int) and e["zoneCount"] >= 0
        assert isinstance(e["iconCount"], int) and e["iconCount"] >= 0


def test_every_committed_file_exists_and_is_referenced_once():
    entries = _load()["templates"]
    referenced = [e["file"] for e in entries]
    # every referenced file exists on disk
    for f in referenced:
        assert (TEMPLATES_DIR / f).is_file(), f"missing template file: {f}"
    # no dangling entry references the same file twice
    assert len(referenced) == len(set(referenced))
    # every committed .drawio is indexed (index and corpus are in sync)
    on_disk = {p.name for p in TEMPLATES_DIR.glob("*.drawio")}
    assert on_disk == set(referenced), (
        f"index/corpus mismatch: only-on-disk={on_disk - set(referenced)}, "
        f"only-in-index={set(referenced) - on_disk}"
    )


def test_templates_are_wellformed_drawio():
    # Spot-check that committed templates are real draw.io files.
    for e in _load()["templates"]:
        head = (TEMPLATES_DIR / e["file"]).read_text(encoding="utf-8", errors="replace")[:4000]
        assert "mxGraphModel" in head or "<mxfile" in head, f"{e['file']} not draw.io"
