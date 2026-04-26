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
import csv
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
DEFAULT_OVERRIDES_CSV = (
    Path(__file__).resolve().parent.parent / "assets" / "service-name-overrides.csv"
)

# Acronyms that must stay uppercase when normalizing tech IDs.
UPPERCASE_TERMS = {
    "sap", "btp", "ai", "api", "edi", "iot", "mdg", "sac", "cap",
    "cf", "cdn", "crm", "cdm", "odata", "rfc", "rest", "sql", "url",
    "uri", "http", "https", "oauth", "jwt", "saml", "sdk", "ui",
    "ui5", "ux", "vpn", "alm", "ans", "bpa", "bp", "cpi", "dms",
    "dox", "dsp", "dwc", "ec", "fi", "ga", "gcp", "gpu", "hr",
    "i18n", "ias", "ips", "is", "it", "kpi", "lca", "ml", "mlops",
    "ocr", "po", "pre", "qa", "rfq", "rpa", "saas", "scn", "sd",
    "slm", "sla", "sm", "soa", "sox", "sse", "tls", "tms", "vat",
    "wms", "xml", "xsuaa",
}

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
    """Trim/clean a raw shape title (whitespace only)."""
    if not title:
        return ""
    return re.sub(r"\s+", " ", title).strip()


def _normalize_tech_id(tech_id: str) -> str:
    """Convert a SAP shape tech ID into a human-friendly service name.

    Pattern: ``<digits>-<words-with-hyphens-and-underscores>[_sd]`` →
    ``Title Case With SAP Acronym Preservation``.

    Examples:
        10002-cloud-integration-automation_sd → Cloud Integration Automation
        10014-sap-audit-log-service_sd → SAP Audit Log Service
        10017-sap-btp_cloud-foundry-runtime_sd → SAP BTP Cloud Foundry Runtime
    """
    if not tech_id:
        return ""
    no_prefix = re.sub(r"^\d+-", "", tech_id)
    no_suffix = re.sub(r"_sd$", "", no_prefix)
    spaced = no_suffix.replace("_", " ").replace("-", " ").strip()
    parts = [p for p in spaced.split() if p]
    out: list[str] = []
    for word in parts:
        lower = word.lower()
        if lower in UPPERCASE_TERMS:
            out.append(lower.upper())
        elif lower.startswith("s4") and len(lower) <= 3:
            out.append("S/4" + lower[2:].upper())  # s4 → S/4
        elif lower == "s4hana":
            out.append("S/4HANA")
        else:
            out.append(word.capitalize())
    name = " ".join(out)
    # Collapse duplicate "SAP " prefixes when present (e.g. SAP ... SAP ... → SAP ... ...).
    name = re.sub(r"\bSAP (\w+) SAP \b", r"SAP \1 ", name)
    return name


def _load_overrides(path: Path) -> dict[str, dict[str, object]]:
    """Read assets/service-name-overrides.csv into a tech_id-keyed dict."""
    if not path.exists():
        return {}
    overrides: dict[str, dict[str, object]] = {}
    with path.open(encoding="utf-8") as fh:
        # Strip comments and blank lines before passing to DictReader.
        lines = [ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        return overrides
    reader = csv.DictReader(lines)
    for row in reader:
        tech_id = (row.get("tech_id") or "").strip()
        if not tech_id:
            continue
        canonical = (row.get("canonical_name") or "").strip()
        aliases_raw = (row.get("aliases") or "").strip()
        aliases = [a.strip() for a in aliases_raw.split("|") if a.strip()]
        overrides[tech_id] = {"canonical_name": canonical, "aliases": aliases}
    return overrides


def _extract_style(xml_snippet: str) -> str:
    """Pull the style="..." attribute of the *icon* mxCell (id=2 by SAP convention)."""
    if not xml_snippet:
        return ""
    # SAP shape XML wraps the icon in an mxGraphModel/root with cells id=0,1,2.
    # Cell id=2 is the actual icon — that's the one we want.
    m = re.search(r'<mxCell id="2"[^>]*style="([^"]*)"', xml_snippet)
    if m:
        return m.group(1)
    # Fallback: first style="" attribute encountered.
    m = re.search(r'style="([^"]*)"', xml_snippet)
    return m.group(1) if m else ""


def _extract_display_name(xml_snippet: str) -> str:
    """Pull the value="..." attribute of the icon cell — the user-friendly SAP name."""
    if not xml_snippet:
        return ""
    m = re.search(r'<mxCell id="2"[^>]*value="([^"]*)"', xml_snippet)
    if not m:
        return ""
    raw = m.group(1)
    # SAP shapes encode line breaks as `&#10;` (XML entity) — flatten to space.
    cleaned = raw.replace("&#10;", " ").replace("&#xA;", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", cleaned).strip()


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


def build_index(cache: Path, overrides_path: Path = DEFAULT_OVERRIDES_CSV) -> dict:
    libs_dir = cache / LIB_SUBPATH
    if not libs_dir.exists():
        raise SystemExit(
            f"ERROR: shape libraries not found at {libs_dir}\n"
            "  Run scripts/bootstrap-cache.sh first."
        )

    overrides = _load_overrides(overrides_path)
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
                tech_id = _normalize_service(entry.get("title") or "")
                if not tech_id:
                    continue
                xml_snippet = entry.get("xml") or ""
                style = _extract_style(xml_snippet)
                # 3-tier name resolution: CSV override → SAP `value` → auto-normalize.
                override = overrides.get(tech_id, {})
                display = (
                    override.get("canonical_name")
                    or _extract_display_name(xml_snippet)
                    or _normalize_tech_id(tech_id)
                    or tech_id
                )
                aliases: set[str] = set(_aliases_for(display))
                aliases.update(override.get("aliases", []) or [])
                aliases.add(tech_id)  # tech ID stays as alias for backwards compat

                services.append(
                    {
                        "name": display,
                        "techId": tech_id,
                        "aliases": sorted(aliases),
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
            "schemaVersion": "1.1.0",
            "overridesApplied": len(overrides),
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
