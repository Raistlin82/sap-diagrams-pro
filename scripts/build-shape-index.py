#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""
build-shape-index.py — harvest SAP service icons + utility libraries into
shape-index.json.

SERVICE ICONS are harvested from the authoritative per-service SVG library
    <repo>/assets/shape-libraries-and-editable-presets/svg/*.svg
(one clean-``techId`` filename per service, e.g.
``32133-sap-integration-suite_api-management_sd.svg``). Each SVG is embedded
as a ``data:image/svg+xml,<base64>`` data URI inside a synthesized draw.io
``shape=image;...`` style, matching the format the runtime (generate-drawio.py
``_extract_image_uri`` / build-icon-atlas.py) expects.

UTILITY LIBRARIES (connectors, annotations_and_interfaces, area_shapes,
default_shapes, numbers, sap_brand_names, essentials, text_elements) and the
generic-icons set are still parsed from the draw.io/ ``mxlibrary`` XML files
exactly as before — those feed pills, brand chips, essentials presets and
generic pictograms.

BACKWARD COMPATIBILITY: every canonical name + alias that resolved against the
previous shape-index (mined from ``--legacy-index``, default: the existing
--out file) and every curated entry of service-name-overrides.csv is matched to
its new SVG-based entry (by techId number, then slug) and re-attached as an
alias, so no previously-resolvable name becomes unresolvable.

The output is consumed at runtime by sap-icons-resolve (look up service name →
draw.io style) and by sap-diagram-generate (resolve node.service → icon).

Usage:
    python3 build-shape-index.py --cache /path/to/cache-root
    python3 build-shape-index.py --cache /path --out shape-index.json
"""
from __future__ import annotations

import argparse
import base64
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
# The authoritative per-service SVG icon library sits next to draw.io/.
SVG_SUBPATH = (
    "btp-solution-diagrams/assets/shape-libraries-and-editable-presets/svg"
)
DEFAULT_OVERRIDES_CSV = (
    Path(__file__).resolve().parent.parent / "assets" / "service-name-overrides.csv"
)
DEFAULT_LEGACY_INDEX = (
    Path(__file__).resolve().parent.parent / "assets" / "shape-index.json"
)
# Curated generic cloud icons (mingrammer/diagrams, MIT) — see assets/GENERIC-ICONS-NOTICE.md.
# Non-SAP components (databases, brokers, K8s, hyperscaler compute/storage, on-prem)
# resolve to real vendor glyphs instead of neutral boxes.
DEFAULT_GENERIC_ICONS = (
    Path(__file__).resolve().parent.parent / "assets" / "generic-icons.json"
)

# Standalone library files (top-level of draw.io/) → category bucket they
# populate. These were missing from the original parser which only walked
# the 20-02-* and 20-03-* sub-directories.
STANDALONE_LIBRARIES = {
    "connectors.xml": "connectors",
    "annotations_and_interfaces.xml": "annotations",
    "area_shapes.xml": "area_shapes",
    "default_shapes.xml": "default_shapes",
    "numbers.xml": "numbers",
    "sap_brand_names.xml": "brand_names",
    "essentials.xml": "essentials",
    "text_elements.xml": "text_elements",
}

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

# Friendly labels for the six service-icon set buckets (the taxonomy is kept
# stable across the SVG re-harvest). ``generic`` is reserved for genericIcons.
SET_LABELS = {
    "foundational": "Foundational Services",
    "integration-suite": "Integration Suite",
    "app-dev-automation": "App Dev & Automation",
    "data-analytics": "Data & Analytics",
    "ai": "AI",
    "btp-saas": "BTP SaaS",
    "generic-cloud": "Generic Cloud (non-SAP)",
    "generic": "Generic Icons",
}

# Canonical draw.io style for a synthesized SAP service icon. The
# ``image=data:image/svg+xml,<base64>`` value is the ONLY load-bearing part
# for the runtime (generate-drawio.py ``_extract_image_uri`` reads it back with
# ``re.search(r"image=([^;]+)")``, so the value must be ';'-delimited and must
# NOT contain a literal ``;base64,`` marker — we emit the comma form). The
# leading ``shape=image;...`` flags and the trailing connection ``points``
# mirror the format the previous per-service mxlibrary entries used.
_STYLE_PREFIX = (
    "shape=image;verticalLabelPosition=bottom;verticalAlign=top;"
    "imageAspect=0;aspect=fixed;image="
)
_STYLE_SUFFIX = (
    ";points=[[0,0,0,0,0],[0,0.25,0,0,0],[0,0.5,0,0,0],[0,0.75,0,0,0],"
    "[0.25,0,0,0,0],[0.5,0,0,0,0],[0.75,0,0,0,0],[1,0,0,0,0],[1,0.25,0,0,0],"
    "[1,0.5,0,0,0],[1,0.75,0,0,0]];fontStyle=0;fontSize=10;fontColor=#556B82"
)

# Tokens dropped when computing a slug fingerprint for old↔new matching, so
# "SAP Connectivity Service" ~ "connectivity-service" and the ``_sd`` /
# "for SAP BTP" decorations don't defeat the match.
_SLUG_NOISE = {
    "sap", "for", "btp", "service", "services", "sd", "edition",
    "the", "on", "of", "and",
}


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


def _leading_number(tech_id: str) -> str | None:
    """The leading numeric id of a techId (e.g. ``32133-...`` → ``"32133"``)."""
    m = re.match(r"(\d+)", tech_id or "")
    return m.group(1) if m else None


def _slug_tokens(tech_id: str) -> frozenset[str]:
    """A noise-stripped token fingerprint of a techId's descriptive part.

    ``32133-sap-integration-suite_api-management_sd`` →
    ``{integration, suite, api, management}``. Used to match legacy/CSV
    entries to their new SVG entry across the space/underscore/number drift.
    """
    t = re.sub(r"^\d+-", "", tech_id or "")
    t = re.sub(r"\.svg$", "", t)
    t = re.sub(r"[_.\s]sd$", "", t)
    t = t.replace("_", " ").replace("-", " ")
    return frozenset(
        w for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in _SLUG_NOISE
    )


def _heuristic_set(tech_id: str, tokens: frozenset[str]) -> str:
    """Bucket a brand-new service (one with no legacy set) into the existing
    set taxonomy from keywords in its techId. Conservative — defaults to
    ``foundational``."""
    s = (tech_id or "").lower()
    if "integration-suite" in s or (
        "integration" in tokens and ("suite" in tokens or "for-data-services" in s)
    ):
        return "integration-suite"
    if tokens & {
        "ai", "joule", "classification", "enrichment", "grounding",
        "recommendation", "intelligence", "genai", "generative",
    }:
        return "ai"
    if "integration" not in tokens and tokens & {
        "analytics", "datasphere", "quality",
    }:
        return "data-analytics"
    if tokens & {
        "build", "process", "forms", "visibility", "automation",
        "workflow", "code",
    }:
        return "app-dev-automation"
    return "foundational"


def _svg_style(svg_path: Path) -> str:
    """Read an SVG file and wrap it in a draw.io ``shape=image`` style with the
    icon embedded as a base64 ``data:image/svg+xml,`` URI (comma form — see
    ``_STYLE_PREFIX`` docstring)."""
    raw = svg_path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"{_STYLE_PREFIX}data:image/svg+xml,{b64}{_STYLE_SUFFIX}"


class _NewIndex:
    """Fast old→new matcher over the harvested SVG techIds."""

    def __init__(self, tech_ids: list[str]):
        self._by_num: dict[str, list[str]] = {}
        self._tokens: dict[str, frozenset[str]] = {}
        for t in tech_ids:
            n = _leading_number(t)
            if n:
                self._by_num.setdefault(n, []).append(t)
            self._tokens[t] = _slug_tokens(t)

    def _best_slug(self, tokens: frozenset[str]) -> str | None:
        if not tokens:
            return None
        cands: list[tuple[int, int, str]] = []
        for t, tk in self._tokens.items():
            if tk == tokens:
                cands.append((0, len(tk), t))            # exact fingerprint
            elif tokens <= tk or tk <= tokens:
                cands.append((1, len(tk ^ tokens), t))   # subset either way
        if not cands:
            return None
        cands.sort()  # (tier, symmetric-diff, techId) — fully deterministic
        return cands[0][2]

    def match(self, tech_id: str, *, trust_number: bool) -> str | None:
        """Best new techId for a legacy/CSV entry.

        ``trust_number=True`` (legacy-index entries, whose numbers share the
        current SAP numbering) tries an exact number match, then a 48xxx→32xxx
        collapse, then slug. ``trust_number=False`` (CSV rows, keyed by a
        stale/older numbering that SAP has since re-assigned) skips the number
        entirely and matches on slug only.
        """
        if trust_number:
            n = _leading_number(tech_id)
            if n and n in self._by_num:
                return sorted(self._by_num[n])[0]
            if n and n.startswith("48") and ("32" + n[2:]) in self._by_num:
                return sorted(self._by_num["32" + n[2:]])[0]
        return self._best_slug(_slug_tokens(tech_id))


def _mine_legacy(legacy_path: Path, new_index: "_NewIndex") -> dict[str, dict]:
    """Group the previous shape-index's service names+aliases by the new
    techId they now map to, so they can be re-attached as aliases."""
    out: dict[str, dict] = {}
    if not legacy_path or not legacy_path.exists():
        return out
    try:
        data = json.loads(legacy_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return out
    for svc in data.get("services", []):
        tech = svc.get("techId", "")
        target = new_index.match(tech, trust_number=True)
        if not target:
            continue
        bucket = out.setdefault(
            target, {"names": [], "aliases": set(), "sets": [], "numbers": set()}
        )
        name = svc.get("name")
        if name:
            bucket["names"].append(name)
        bucket["aliases"].update(svc.get("aliases", []) or [])
        if svc.get("set"):
            bucket["sets"].append(svc["set"])
        n = _leading_number(tech)
        if n:
            bucket["numbers"].add(n)
    return out


def _mine_overrides(overrides: dict[str, dict], new_index: "_NewIndex") -> dict[str, dict]:
    """Group service-name-overrides.csv canonical names + aliases by the new
    techId they map to (slug match — CSV numbers are stale)."""
    out: dict[str, dict] = {}
    for tech_id, row in overrides.items():
        target = new_index.match(tech_id, trust_number=False)
        if not target:
            continue
        bucket = out.setdefault(target, {"names": [], "aliases": set()})
        canonical = (row.get("canonical_name") or "").strip()
        if canonical:
            bucket["names"].append(canonical)
        bucket["aliases"].update(row.get("aliases", []) or [])
    return out


def _harvest_svg_services(
    svg_dir: Path,
    overrides: dict[str, dict],
    legacy_path: Path | None,
) -> list[dict]:
    """Build the ``services`` list from every ``svg/*.svg`` file.

    techId = filename stem; name = mined legacy canonical name (backward-compat)
    → CSV canonical name → auto-normalized techId; set = mined legacy set →
    keyword heuristic; aliases = union of the auto acronym/short forms, all
    legacy names+aliases, all CSV names+aliases, and the techId itself.
    """
    if not svg_dir.exists():
        raise SystemExit(
            f"ERROR: SVG icon library not found at {svg_dir}\n"
            "  Run scripts/bootstrap-cache.sh first (or pass --cache)."
        )

    stems = sorted(p.stem for p in svg_dir.glob("*.svg"))
    new_index = _NewIndex(stems)
    legacy = _mine_legacy(legacy_path, new_index) if legacy_path else {}
    csv_map = _mine_overrides(overrides, new_index)

    # Curated (CSV) short-forms must win over the accidental initial-letter
    # acronyms _aliases_for() synthesizes — e.g. "SAP AI Core" auto-generates
    # "SAC", which would otherwise shadow SAP Analytics Cloud's curated "SAC".
    curated_alias_owner: dict[str, str] = {}
    for target, ovr in csv_map.items():
        for alias in ovr.get("aliases", set()):
            curated_alias_owner.setdefault(alias.strip().lower(), target)

    services: list[dict] = []
    for stem in stems:
        svg_path = svg_dir / f"{stem}.svg"
        number = _leading_number(stem)
        tokens = _slug_tokens(stem)
        leg = legacy.get(stem, {})
        ovr = csv_map.get(stem, {})

        # ── canonical name ──────────────────────────────────────────────
        # Prefer the legacy canonical name of the entry that shared THIS
        # techId number (keeps every previously-resolvable canonical name
        # canonical); then any legacy name; then the CSV canonical; then the
        # auto-normalized techId.
        name = ""
        for cand in leg.get("names", []):
            name = cand
            if number and number in leg.get("numbers", set()):
                break
        if not name and ovr.get("names"):
            name = ovr["names"][0]
        if not name:
            name = _normalize_tech_id(stem) or stem

        # ── set bucket ──────────────────────────────────────────────────
        set_id = leg["sets"][0] if leg.get("sets") else _heuristic_set(stem, tokens)

        # ── aliases (union, minus the canonical name) ───────────────────
        # Curated CSV short-forms win over accidental initial-letter acronyms:
        # drop any auto- or legacy-sourced alias that a DIFFERENT entry owns as
        # a curated CSV alias (e.g. "SAP AI Core" must not keep "SAC", which
        # belongs to SAP Analytics Cloud). This entry's own CSV names/aliases
        # and every legacy full name are always kept.
        def _not_owned_elsewhere(a: str) -> bool:
            return curated_alias_owner.get(a.strip().lower(), stem) == stem

        aliases: set[str] = {a for a in _aliases_for(name) if _not_owned_elsewhere(a)}
        aliases.update(leg.get("names", []))
        aliases.update(a for a in leg.get("aliases", set()) if _not_owned_elsewhere(a))
        aliases.update(ovr.get("names", []))
        aliases.update(ovr.get("aliases", set()))
        aliases.add(stem)  # techId is always a resolvable alias
        aliases = {a for a in aliases if a and a != name}

        services.append(
            {
                "name": name,
                "techId": stem,
                "aliases": sorted(aliases),
                "set": set_id,
                "size": "M",  # one canonical icon per service in the SVG library
                "drawioStyle": _svg_style(svg_path),
            }
        )
    return services


def _load_generic_cloud_icons(path: Path = DEFAULT_GENERIC_ICONS) -> list[dict]:
    """Curated generic cloud icons (mingrammer/diagrams, MIT) as service rows in
    the ``generic-cloud`` set, so the resolver matches non-SAP components (redis,
    kafka, kubernetes, aws-lambda, …) by alias and the atlas rasterizes them.
    Each manifest icon carries a full ``data:image/png;base64,…`` URI, wrapped in
    the same ``shape=image`` style as the SAP icons. Absent manifest → no rows."""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict] = []
    for icon in data.get("icons", []):
        key = icon["key"]
        aliases = {a for a in icon.get("aliases", []) if a and a != icon["name"]}
        aliases.add(key)
        out.append(
            {
                "name": icon["name"],
                "techId": key,
                "aliases": sorted(aliases),
                "set": "generic-cloud",
                "size": "M",
                "drawioStyle": f"{_STYLE_PREFIX}{icon['pngDataUri']}{_STYLE_SUFFIX}",
            }
        )
    return out


def _parse_generic_icons(libs_dir: Path) -> list[dict]:
    """Parse the 20-03-generic-icons libraries (User, Mobile, Desktop,
    Cloud Connector, On-Premise, Third Party, Adapter, Admin, AI, ...).

    These use a DIFFERENT JSON schema from service icons + standalone libs:
    each entry is `{data: <SVG-base64-URI>, w, h, title, aspect}` rather
    than the mxCell-wrapped XML used elsewhere. We synthesise the
    drawioStyle the way drawio would when the shape is dragged from the
    sidebar: `shape=image;...image=<data>` with `aspect=fixed`.
    """
    out: list[dict] = []
    set_dir = libs_dir / "20-03-generic-icons"
    if not set_dir.exists():
        return out
    # Loosened size regex — generic-icons files use `-size-M-200302.xml`
    # (extra numeric suffix) rather than the `-size-M.xml` pattern of
    # service-icon libraries.
    size_pat = re.compile(r"-size-(?P<size>[SML])\b", re.IGNORECASE)
    for xml_file in sorted(set_dir.glob("*.xml")):
        m = size_pat.search(xml_file.name)
        if not m:
            continue
        size = m.group("size").upper()
        # The generic-icons libraries embed JSON inline (not wrapped in
        # <mxlibrary>...</mxlibrary> with mxCell entries — the outer XML
        # is `<mxlibrary>[{data:..., title:..., w:..., h:..., aspect:...}]
        # </mxlibrary>`). Use raw split to retrieve the JSON array
        # because ElementTree's text decoding is lossy on long base64.
        try:
            raw = xml_file.read_text(encoding="utf-8")
        except OSError:
            continue
        if "<mxlibrary>" not in raw or "</mxlibrary>" not in raw:
            continue
        body = raw.split("<mxlibrary>", 1)[1].split("</mxlibrary>", 1)[0]
        try:
            entries = json.loads(body)
        except json.JSONDecodeError:
            continue
        for entry in entries:
            title = (entry.get("title") or "").strip()
            data = entry.get("data") or ""
            w = entry.get("w") or 24
            h = entry.get("h") or 24
            aspect = entry.get("aspect") or "fixed"
            if not data:
                continue
            # drawio synthesises this style for image shapes:
            style = (
                "shape=image;verticalLabelPosition=bottom;verticalAlign=top;"
                f"imageAspect=0;aspect={aspect};image={data};"
                "fontStyle=0;fontSize=12;fontColor=#1D2D3E;"
            )
            # Normalize the title into a base concept + variant flag:
            # "User Non-SAP Size M" → base="User", variant="non-sap"
            base = re.sub(r"\s*(Highlight|SAP|Non-SAP)[\s-]+Size\s*[SML]$", "", title).strip()
            variant = "highlight"
            if "Non-SAP" in title:
                variant = "non-sap"
            elif " SAP " in title or "-SAP-" in title or title.endswith(" SAP"):
                variant = "sap"
            out.append(
                {
                    "name": title,
                    "base": base,
                    "variant": variant,
                    "size": size,
                    "width": w,
                    "height": h,
                    "drawioStyle": style,
                }
            )
    return out


def _parse_standalone_libraries(libs_dir: Path) -> dict[str, list[dict]]:
    """Parse the 8 top-level XML files in draw.io/ (connectors, annotations,
    area_shapes, default_shapes, numbers, brand_names, essentials,
    text_elements) into category-keyed lists.

    For ``essentials`` (multi-cell pre-composed organisms like "User and
    client", "Legend", "Cloud Connector"), we preserve the COMPLETE XML
    body so the engine can later embed the raw cells into output diagrams
    as a single SAP-canonical block.
    """
    catalog: dict[str, list[dict]] = {cat: [] for cat in STANDALONE_LIBRARIES.values()}
    for filename, category in STANDALONE_LIBRARIES.items():
        path = libs_dir / filename
        if not path.exists():
            continue
        entries = _parse_library(path)
        for entry in entries:
            title = (entry.get("title") or "").strip()
            xml_snippet = entry.get("xml") or ""
            # Pull the FIRST style we find (these libraries use single-cell or
            # group-prefixed shapes; always-take-first works for both).
            m = re.search(r'style="([^"]*)"', xml_snippet)
            style = m.group(1) if m else ""
            display = title or _extract_display_name(xml_snippet) or "(unnamed)"
            # For essentials we keep the full XML body. For other categories
            # we keep the first style + raw XML for reference.
            shape = {
                "name": display,
                "title": title,
                "drawioStyle": style,
                "rawXml": xml_snippet,
                "width": entry.get("w"),
                "height": entry.get("h"),
            }
            if category == "essentials":
                # Generate a slug usable as preset key: "User and client"
                # → "user-and-client", "3rd party IdP and protocols" →
                # "3rd-party-idp-and-protocols".
                slug = (
                    re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
                )
                shape["slug"] = slug
                # Count mxCell entries inside (for telemetry).
                shape["cellCount"] = len(re.findall(r"<mxCell", xml_snippet))
            catalog[category].append(shape)
    return catalog


def build_index(
    cache: Path,
    overrides_path: Path = DEFAULT_OVERRIDES_CSV,
    legacy_index_path: Path | None = DEFAULT_LEGACY_INDEX,
) -> dict:
    libs_dir = cache / LIB_SUBPATH
    svg_dir = cache / SVG_SUBPATH
    if not libs_dir.exists():
        raise SystemExit(
            f"ERROR: shape libraries not found at {libs_dir}\n"
            "  Run scripts/bootstrap-cache.sh first."
        )

    overrides = _load_overrides(overrides_path)

    # ── Service icons: harvested from the authoritative svg/ library ─────
    services = _harvest_svg_services(svg_dir, overrides, legacy_index_path)

    # ── Generic cloud icons (mingrammer/diagrams, MIT) — appended as the
    # ``generic-cloud`` set so non-SAP components resolve to real vendor glyphs.
    services.extend(_load_generic_cloud_icons())

    # Set buckets: one entry per set-id actually used, with its service count.
    counts: dict[str, int] = {}
    for svc in services:
        counts[svc["set"]] = counts.get(svc["set"], 0) + 1
    set_order = [
        "foundational", "integration-suite", "app-dev-automation",
        "data-analytics", "ai", "btp-saas", "generic-cloud",
    ]
    sets: list[dict] = [
        {
            "id": sid,
            "name": SET_LABELS.get(sid, sid),
            "fileBasename": sid,
            "serviceCount": counts[sid],
        }
        for sid in set_order
        if sid in counts
    ]

    # Standalone catalogs (the 8 draw.io/ utility libraries) — unchanged.
    standalone = _parse_standalone_libraries(libs_dir)
    standalone_total = sum(len(v) for v in standalone.values())

    # Generic-icons set (User, Mobile, Desktop, Cloud Connector, …) — uses
    # a different JSON schema, parsed separately.
    generic_icons = _parse_generic_icons(libs_dir)

    repo_dir = cache / "btp-solution-diagrams"
    return {
        "meta": {
            "sourceRepo": "https://github.com/SAP/btp-solution-diagrams",
            "sourceCommit": _git_short_sha(repo_dir) or "unknown",
            "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "totalServices": len(services),
            "totalStandalone": standalone_total,
            "totalGenericIcons": len(generic_icons),
            "schemaVersion": "1.3.0",
            "overridesApplied": len(overrides),
        },
        "sets": sets,
        "services": services,
        "genericIcons": generic_icons,
        "connectors": standalone["connectors"],
        "annotations": standalone["annotations"],
        "areaShapes": standalone["area_shapes"],
        "defaultShapes": standalone["default_shapes"],
        "numbers": standalone["numbers"],
        "brandNames": standalone["brand_names"],
        "essentials": standalone["essentials"],
        "textElements": standalone["text_elements"],
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
    parser.add_argument(
        "--legacy-index",
        type=Path,
        default=None,
        help="Previous shape-index.json to mine names/aliases from for "
             "backward-compat (default: the --out file, read before overwrite).",
    )
    args = parser.parse_args(argv)

    legacy_index_path = args.legacy_index if args.legacy_index is not None else args.out
    index = build_index(args.cache, legacy_index_path=legacy_index_path)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"✅ Wrote {args.out} — {index['meta']['totalServices']} services across "
        f"{len(index['sets'])} sets (source SHA: {index['meta']['sourceCommit']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
