#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""build-templates-pack.py — pack a CURATED subset of the template corpus into a
single JSON file (``assets/templates-pack.json``) with each template's draw.io XML
embedded, so the scaffold path works on Claude Desktop / claude.ai where the loose
``assets/templates/`` corpus (156 files) can't be bundled under the 200-file Skills
upload cap.

Curation rule (deterministic): every SAP/btp-solution-diagrams editable example
(the canonical L0/L1/L2 gold set) PLUS, for each SAP/architecture-center family,
the single best representative — most scenarioAliases, then smallest file (a leaner
diagram is a cleaner scaffold base). Yields ~18-20 templates covering every family.

``scaffold-diagram.py`` reads a template's XML from the loose file when present,
else from this pack; ``select-template.py`` restricts its ranking to the packed
subset when the loose corpus is absent (Desktop).

    python3 scripts/build-templates-pack.py            # → assets/templates-pack.json
"""
from __future__ import annotations

import json
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
INDEX = _REPO / "assets" / "template-index.json"
TEMPLATES_DIR = _REPO / "assets" / "templates"
OUT = _REPO / "assets" / "templates-pack.json"


def curate(templates: list[dict]) -> list[dict]:
    """All btp editable examples + best-per-AC-family representative."""
    chosen: list[dict] = []
    seen: set[str] = set()
    # 1. every btp-solution-diagrams editable example (the gold L0/L1/L2 set)
    for e in templates:
        if str(e.get("source", "")).endswith("btp-solution-diagrams"):
            chosen.append(e)
            seen.add(e["id"])
    # 2. best architecture-center representative per family
    by_family: dict[str, list[dict]] = {}
    for e in templates:
        if str(e.get("source", "")).endswith("architecture-center"):
            by_family.setdefault(e.get("family", "generic"), []).append(e)

    def rank_key(e: dict) -> tuple:
        path = TEMPLATES_DIR / e["file"]
        size = path.stat().st_size if path.exists() else 1 << 30
        # most scenario aliases first, then smallest file, then id for determinism
        return (-len(e.get("scenarioAliases", [])), size, e["id"])

    for family in sorted(by_family):
        best = sorted(by_family[family], key=rank_key)[0]
        if best["id"] not in seen:
            chosen.append(best)
            seen.add(best["id"])
    return chosen


def main() -> int:
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    picked = curate(index["templates"])
    packed = []
    total_xml = 0
    for e in picked:
        src = TEMPLATES_DIR / e["file"]
        xml = src.read_text(encoding="utf-8")
        total_xml += len(xml)
        packed.append({**e, "drawioXml": xml})
    out = {
        "meta": {
            "schemaVersion": "1.0.0",
            "description": "Curated subset of the template corpus with embedded "
            "draw.io XML, for the Desktop/claude.ai scaffold path (200-file cap).",
            "source": index.get("meta", {}).get("sources", []),
            "templateCount": len(packed),
        },
        "templates": packed,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    fams = sorted({e.get("family") for e in picked})
    print(f"✅ Wrote {OUT} — {len(packed)} templates, {total_xml // 1024} KB XML embedded")
    print(f"   families: {', '.join(fams)}")
    for e in picked:
        print(f"   - {e['id']}  [{e.get('family')}/{e.get('level')}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
