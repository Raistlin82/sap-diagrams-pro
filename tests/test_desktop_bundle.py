# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""tests/test_desktop_bundle.py — the Claude Desktop / claude.ai Agent Skill
bundle must ship the scaffold-and-extend edit tools AND stay under claude.ai's
200-file Skills upload cap.

``packaging/claude-desktop-skill/build.sh`` assembles the bundle from the single
source-of-truth engine. This test runs it for real and pins:
  * exit 0,
  * the four scaffold-and-extend scripts (_drawio_edit / remove-cell / add-node /
    add-edge) land in the staged bundle AND in the .zip, and
  * the staged file count stays <= 200 (the whole reason the icon atlas is
    base64-packed into one index.json instead of shipping ~360 loose PNGs).
"""
from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "packaging" / "claude-desktop-skill" / "build.sh"
SKILL_NAME = "sap-diagram-generate"
STAGE = ROOT / "dist" / "claude-desktop-skill" / SKILL_NAME
ZIP = ROOT / "dist" / "claude-desktop-skill" / f"{SKILL_NAME}.zip"

FILE_CAP = 200
EDIT_TOOLS = ["_drawio_edit.py", "remove-cell.py", "add-node.py", "add-edge.py"]


@pytest.fixture(scope="module")
def built_bundle():
    if shutil.which("bash") is None:
        pytest.skip("bash unavailable")
    result = subprocess.run(
        ["bash", str(BUILD)], cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_bundle_ships_the_edit_tools(built_bundle):
    for name in EDIT_TOOLS:
        assert (STAGE / "scripts" / name).is_file(), \
            f"{name} missing from the staged bundle"


def test_bundle_zip_contains_the_edit_tools(built_bundle):
    if not ZIP.exists():
        pytest.skip("zip CLI unavailable — build.sh staged the folder but made no .zip")
    names = set(zipfile.ZipFile(ZIP).namelist())
    for name in EDIT_TOOLS:
        assert f"{SKILL_NAME}/scripts/{name}" in names, \
            f"{name} missing from the uploadable .zip"


def test_bundle_stays_under_the_200_file_cap(built_bundle):
    count = sum(1 for p in STAGE.rglob("*") if p.is_file())
    assert count <= FILE_CAP, \
        f"bundle has {count} files, over claude.ai's {FILE_CAP}-file Skills cap"
