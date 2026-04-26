#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""
build-shape-index.py — parse SAP draw.io shape libraries and emit shape-index.json.

Reads the XML library files under
    $SAP_DIAGRAMS_CACHE/btp-solution-diagrams/assets/shape-libraries-and-editable-presets/draw.io/
and produces a JSON catalog conformant to assets/shape-index.schema.json.

Usage:
    python3 build-shape-index.py
    python3 build-shape-index.py --cache /path/to/cache --out shape-index.json

The output is consumed at runtime by sap-icons-resolve (look up service name →
draw.io style) and by sap-diagram-generate (resolve node.service → icon).

Each shape library file is a draw.io ``mxlibrary`` containing a JSON-encoded
array of shape entries with fields: ``xml`` (mxCell snippet), ``w``, ``h``,
``aspect``, ``title``. Sizes are inferred from the directory naming:
    ...-size-S.xml → small (24px)
    ...-size-M.xml → medium (48px)
    ...-size-L.xml → large (96px)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CACHE = Path.home() / ".cache" / "sap-diagrams-pro"
LIB_SUBPATH = (
    "btp-solution-diagrams/assets/shape-libraries-and-editable-presets/draw.io"
)

SET_ID_BY_DIR = {
    "20-02-00-sap-btp-service-icons-foundational-set": "foundational",
    "20-02-01-sap-btp-service-icons-integration-suite-set": "integration-suite",
    "20-02-02-sap-btp-service-icons-app-dev-automation-set": "app-dev-automation",
    "20-02-04-sap-btp-service-icons-data-analytics-set": "data-analytics",
    "20-02-05-sap-btp-service-icons-ai-set": "ai",
    "20-02-06-sap-btp-service-icons-btp-saas-set": "btp-saas",
    "20-02-99-sap-btp-service-icons-all": "all",  # aggregate, skipped
    "20-03-generic-icons": "generic",
}

SIZE_FROM_FILENAME = re.compile(r"-size-(?P<size>[SML])\.xml$", re.IGNORECASE)


def _git_short_sha(repo_dir: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo_dir), "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _parse_library(path: Path) -> list[dict]:
    """Parse one mxlibrary XML file and return its shape entries.

    The mxlibrary content is a JSON array stored as text inside <mxlibrary>.
    """
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        print(f"WARN: skip {path}: parse error {exc}", file=sys.stderr)
        return []

    root = tree.getroot()
    if root.tag != "mxlibrary":
        return []
    text = (root.text or "").strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"WARN: skip {path}: json decode error {exc}", file=sys.stderr)
        return []


def _normalize_service(title: str) -> str:
    """Trim/clean a shape title into a canonical service name."""
    if not title:
        return ""
    return re.sub(r"\s+", " ", title).strip()


def _extract_style(xml_snippet: str) -> str:
    """Pull the style="..." attribute out of an mxCell snippet."""
    if not xml_snippet:
        return ""
    m = re.search(r'style="([^"]*)"', xml_snippet)
    return m.group(1) if m else ""


def _aliases_for(name: str) -> list[str]:
    """Heuristic alias generation: acronym + common short forms."""
    if not name:
        return []
    aliases = set()
    # Acronym from leading capitals.
    acro = "".join(w[0] for w in name.split() if w and w[0].isupper())
    if 2 <= len(acro) <= 6:
        aliases.add(acro)
    # Drop "SAP " prefix.
    if name.startswith("SAP "):
        aliases.add(name[len("SAP "):])
    return sorted(aliases)


def build_index(cache: Path) -> dict:
    libs_dir = cache / LIB_SUBPATH
    if not libs_dir.exists():
        raise SystemExit(
            f"ERROR: shape libraries not found at {libs_dir}\n"
            "  Run scripts/bootstrap-cache.sh first."
        )

    sets: list[dict] = []
    services: list[dict] = []

    for set_dir in sorted(libs_dir.iterdir()):
        if not set_dir.is_dir():
            continue
        set_id = SET_ID_BY_DIR.get(set_dir.name)
        if not set_id or set_id == "all":
            continue  # skip the aggregate "all" set to avoid duplicates

        set_count = 0
        for xml_file in sorted(set_dir.glob("*.xml")):
            m = SIZE_FROM_FILENAME.search(xml_file.name)
            if not m:
                continue
            size = m.group("size").upper()

            entries = _parse_library(xml_file)
            for entry in entries:
                title = _normalize_service(entry.get("title") or "")
                if not title:
                    continue
                style = _extract_style(entry.get("xml") or "")
                services.append(
                    {
                        "name": title,
                        "aliases": _aliases_for(title),
                        "set": set_id,
                        "size": size,
                        "drawioStyle": style,
                    }
                )
                set_count += 1

        sets.append(
            {
                "id": set_id,
                "name": set_dir.name,
                "fileBasename": set_dir.name,
                "serviceCount": set_count,
            }
        )

    repo_dir = cache / "btp-solution-diagrams"
    return {
        "meta": {
            "sourceRepo": "https://github.com/SAP/btp-solution-diagrams",
            "sourceCommit": _git_short_sha(repo_dir) or "unknown",
            "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "totalServices": len(services),
            "schemaVersion": "1.0.0",
        },
        "sets": sets,
        "services": services,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Parse SAP draw.io shape libraries into a unified shape-index.json."
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path(os.environ.get("SAP_DIAGRAMS_CACHE", DEFAULT_CACHE)),
        help=f"Cache root (default: $SAP_DIAGRAMS_CACHE or {DEFAULT_CACHE}).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "assets" / "shape-index.json",
        help="Output path for shape-index.json (default: assets/shape-index.json).",
    )
    args = parser.parse_args(argv)

    index = build_index(args.cache)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"✅ Wrote {args.out} — {index['meta']['totalServices']} services across "
        f"{len(index['sets'])} sets (source SHA: {index['meta']['sourceCommit']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
