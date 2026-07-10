#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""Build assets/template-index.json from the SAP reference template corpus.

The corpus (assets/templates/*.drawio) is a set of real, editable SAP reference
architecture diagrams harvested from two Apache-2.0 SAP open-source repos:

  * SAP/architecture-center      (docs/ref-arch/RAxxxx/**/drawio/*.drawio)
  * SAP/btp-solution-diagrams    (assets/editable-diagram-examples/*.drawio)

This script parses every template and emits a metadata index that a later
"scaffold" selector ranks against a natural-language prompt to pick the closest
real diagram to copy + surgically edit (instead of computing a layout).

Design constraints:
  * stdlib only, zero third-party deps.
  * Deterministic: entries sorted by id; ``generatedAt`` is derived from the
    source repos' commit dates (never from wall-clock time), so re-running on
    the same inputs yields byte-identical output.

Usage:
    python3 scripts/build-template-index.py
    python3 scripts/build-template-index.py \
        --templates-dir assets/templates \
        --out assets/template-index.json \
        --architecture-center /tmp/architecture-center \
        --btp-solution-diagrams ~/tools/btp-solution-diagrams

Provenance (source repo + original path + commit SHA + commit date) is
recovered by matching each committed template's content hash against the source
repos when they are available at build time. The emitted JSON is self-contained
and needs no source repos to be read/tested afterwards.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

# --------------------------------------------------------------------------- #
# Classification vocabularies
# --------------------------------------------------------------------------- #

# family -> ordered keyword list (first match wins; order = priority).
FAMILY_KEYWORDS: list[tuple[str, list[str]]] = [
    ("event-driven", ["event mesh", "event-driven", "event driven", "advanced event mesh",
                       "kafka", "pub/sub", "message queue", "eventing"]),
    ("ml", ["federated ml", "federated machine", "fedml", "machine learning", "hana ml",
            "predictive", "data science", "jupyter"]),
    ("ai", ["joule", "generative ai", "gen ai", "genai", "llm", "rag",
            "retrieval augmented", "retrieval-augmented", "semantic search",
            "ai core", "ai launchpad", "orchestration", "embedding", "copilot",
            "mcp", "agent"]),
    ("identity", ["identity", "authentication", "authorization", "sso", "saml", "oidc",
                  "scim", "iam", "cloud identity services", "ias", "ips",
                  "identity provider", "identity lifecycle", "decentralized identity",
                  "verifiable credential"]),
    ("connectivity", ["private link", "cloud connector", "connectivity", "vpc",
                      "vnet", "peering", "transit gateway", "privatelink",
                      "load balancer", "loadbalancer", "network"]),
    ("resiliency", ["resiliency", "resilience", "multi-region", "multi region",
                    "high availability", "disaster recovery", "failover",
                    "availability zone"]),
    ("data", ["datasphere", "business data cloud", "bdc", "hana cloud", "data lake",
              "data warehouse", "data intelligence", "data product", "analytics",
              "hyperscaler data"]),
    ("integration", ["integration suite", "cloud integration", "iflow", "ifow",
                     "api management", "apim", "edge integration cell", "eic",
                     "b2b", "business-to-business", "application-to-application",
                     "a2a", "master data integration", "mdi", "process integration",
                     "successfactors", "s/4hana integration"]),
    ("extension", ["build process automation", "process automation", "workflow",
                   "build work zone", "work zone", "task center", "build apps",
                   "low-code", "no-code", "extension suite", "cap", "kyma",
                   "start", "build code"]),
    ("iot", ["iot", "internet of things", "sitewise", "edge"]),
    ("security", ["siem", "soar", "threat detection", "etd", "security"]),
]

# Curated strong scenario keywords -> canonical alias emitted in scenarioAliases.
# Matched case-insensitively against labels + title + filename.
SCENARIO_ALIASES: dict[str, list[str]] = {
    "Joule": ["joule"],
    "MCP": ["mcp", "model context protocol"],
    "Private Link": ["private link", "privatelink"],
    "Edge Integration Cell": ["edge integration cell", "eic"],
    "SuccessFactors": ["successfactors", "success factors"],
    "DevOps": ["devops", "ci/cd", "cicd", "pipeline"],
    "Federated ML": ["federated ml", "fedml", "federated machine learning"],
    "RAG": ["rag", "retrieval augmented", "retrieval-augmented"],
    "Semantic Search": ["semantic search"],
    "Generative AI": ["generative ai", "gen ai", "genai"],
    "Multi-Region Resiliency": ["multi-region", "multi region", "resiliency", "resilience"],
    "Task Center": ["task center"],
    "Work Zone": ["work zone"],
    "Build Process Automation": ["build process automation", "process automation"],
    "Identity Lifecycle": ["identity lifecycle"],
    "Cloud Identity Services": ["cloud identity services"],
    "Decentralized Identity": ["decentralized identity", "verifiable credential"],
    "Master Data Integration": ["master data integration"],
    "Event Mesh": ["event mesh"],
    "API Management": ["api management", "apim"],
    "Cloud Connector": ["cloud connector"],
    "Private Cloud Edition": ["private cloud edition", "pce", "rise with sap"],
    "Kyma": ["kyma"],
    "Datasphere": ["datasphere"],
    "Business Data Cloud": ["business data cloud"],
    "HANA Cloud": ["hana cloud"],
    "Document AI": ["document ai", "document information extraction", "dox"],
    "B2B Integration": ["business-to-business", "b2b integration"],
    "Hyperscaler": ["aws", "azure", "gcp", "google cloud", "hyperscaler"],
    "SIEM/SOAR": ["siem", "soar", "threat detection"],
}

# Service-label heuristic vocabulary (labels containing any of these, or starting
# with "SAP ", are treated as serviceTokens rather than generic label tokens).
SERVICE_VOCAB = [
    "sap", "hana", "kyma", "cloud foundry", "s/4hana", "successfactors", "ariba",
    "concur", "fieldglass", "datasphere", "integration suite", "event mesh",
    "build", "work zone", "task center", "joule", "ai core", "ai launchpad",
    "document", "identity", "connectivity", "destination", "xsuaa",
    "aws", "azure", "gcp", "google cloud", "amazon", "microsoft",
    "kafka", "postgres", "redis", "openai", "bedrock", "vertex",
]

# Labels to exclude from serviceTokens (connector protocols / legend / chrome).
NON_SERVICE_EXACT = {
    "legend", "optional", "access", "provisioning", "authentication",
    "mutual trust", "note", "notes", "title", "description",
}
PROTOCOL_RE = re.compile(
    r"^(rest|https?|odata|saml2?|oidc|oauth2?|scim|jdbc|amqp|mqtt|soap|rfc|"
    r"tcp|udp|ssh|sftp|ftp|grpc|ws|wss|spi|token|json|xml|api)\b",
    re.IGNORECASE,
)

TAG_RE = re.compile(r"<[^>]+>")
WORD_RE = re.compile(r"[a-z0-9][a-z0-9+/&.\-]{1,}", re.IGNORECASE)
LEVEL_FILE_RE = re.compile(r"[-_.\s]l([0-3])\b", re.IGNORECASE)
LEVEL_TEXT_RE = re.compile(r"level[:\s]+l([0-3])", re.IGNORECASE)

STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "via", "per",
    "check", "list", "supported", "solutions", "manual", "diagram", "level",
    "depicts", "within", "customer", "your", "you", "are", "not", "any", "all",
    "sap", "btp",  # too common to discriminate; kept out of labelTokens noise
}


def clean_label(raw: str) -> str:
    """Unescape entities, strip HTML tags, collapse whitespace."""
    if not raw:
        return ""
    txt = html.unescape(raw)
    txt = txt.replace("<br>", " ").replace("<br/>", " ").replace("<br />", " ")
    txt = TAG_RE.sub(" ", txt)
    txt = html.unescape(txt)
    txt = re.sub(r"[\r\n\t]+", " ", txt)
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()


def is_service_label(label: str) -> bool:
    low = label.lower().strip()
    if not low or len(label) > 70:
        return False
    if low in NON_SERVICE_EXACT:
        return False
    if PROTOCOL_RE.match(low):
        return False
    if low.isdigit():
        return False
    if low.startswith("sap "):
        return True
    return any(v in low for v in SERVICE_VOCAB)


def infer_level(filename: str, texts: list[str]) -> str:
    m = LEVEL_FILE_RE.search(filename)
    if m:
        return f"L{m.group(1)}" if m.group(1) in "012" else "unknown"
    for t in texts:
        m = LEVEL_TEXT_RE.search(t)
        if m:
            return f"L{m.group(1)}" if m.group(1) in "012" else "unknown"
    return "unknown"


_KW_CACHE: dict[str, re.Pattern] = {}


def kw_hit(haystack_low: str, kw: str) -> bool:
    """Word-boundary keyword match (so 'rag' never matches 'storage',
    'aws' never matches 'flaws', 'mcp'/'eic'/'pce' stay whole-word)."""
    pat = _KW_CACHE.get(kw)
    if pat is None:
        pat = re.compile(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])")
        _KW_CACHE[kw] = pat
    return pat.search(haystack_low) is not None


def infer_family(haystack: str) -> str:
    low = haystack.lower()
    for fam, kws in FAMILY_KEYWORDS:
        if any(kw_hit(low, kw) for kw in kws):
            return fam
    return "generic"


def detect_scenarios(haystack: str) -> list[str]:
    low = haystack.lower()
    out = []
    for alias, kws in SCENARIO_ALIASES.items():
        if any(kw_hit(low, kw) for kw in kws):
            out.append(alias)
    return sorted(out)


def humanize(filename: str) -> str:
    stem = re.sub(r"\.drawio$", "", filename)
    stem = re.sub(r"^RA\d{4}_", "", stem)
    stem = stem.replace("_", " ").replace("-", " ")
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem


def parse_template(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")

    labels: list[str] = []
    icon_count = 0
    zone_count = 0
    canvas = {"w": 0, "h": 0}
    diagram_name = ""
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")

    def consume_cell(value: str, style: str, geom: dict):
        nonlocal icon_count, zone_count, min_x, min_y, max_x, max_y
        style_l = style.lower()
        is_image = "shape=image" in style_l or "image=" in style_l
        if is_image:
            icon_count += 1
        w = geom.get("w", 0.0)
        h = geom.get("h", 0.0)
        x = geom.get("x")
        y = geom.get("y")
        if x is not None and y is not None and w and h:
            min_x, min_y = min(min_x, x), min(min_y, y)
            max_x, max_y = max(max_x, x + w), max(max_y, y + h)
        # zone heuristic: large background rectangle / group container, not an icon.
        is_group = "group" in style_l or "container=1" in style_l or "swimlane" in style_l
        big_box = (w >= 220 and h >= 160 and not is_image)
        if is_group or big_box:
            zone_count += 1
        lbl = clean_label(value)
        if lbl:
            labels.append(lbl)

    parsed = False
    try:
        root = ET.fromstring(text)
        parsed = True
        for diag in root.iter("diagram"):
            if not diagram_name and diag.get("name"):
                diagram_name = diag.get("name") or ""
        for gm in root.iter("mxGraphModel"):
            pw = gm.get("pageWidth")
            ph = gm.get("pageHeight")
            if pw and ph and canvas["w"] == 0:
                try:
                    canvas = {"w": int(float(pw)), "h": int(float(ph))}
                except ValueError:
                    pass
        for cell in root.iter("mxCell"):
            geom = {}
            for g in cell:
                if g.tag == "mxGeometry":
                    for k, dst in (("x", "x"), ("y", "y"), ("width", "w"), ("height", "h")):
                        v = g.get(k)
                        if v is not None:
                            try:
                                geom[dst] = float(v)
                            except ValueError:
                                pass
            consume_cell(cell.get("value", "") or "", cell.get("style", "") or "", geom)
        # draw.io also uses <object label="..."><mxCell .../></object> and UserObject
        for obj in root.iter():
            if obj.tag in ("object", "UserObject"):
                lbl = clean_label(obj.get("label", "") or obj.get("value", "") or "")
                if lbl:
                    labels.append(lbl)
    except ET.ParseError:
        parsed = False

    if not parsed:
        # Regex fallback (should be rare; corpus is well-formed XML).
        for m in re.finditer(r'value="([^"]*)"', text):
            lbl = clean_label(m.group(1))
            if lbl:
                labels.append(lbl)
        icon_count = len(re.findall(r"shape=image|image=data:", text))
        mm = re.search(r'pageWidth="([0-9.]+)"\s+pageHeight="([0-9.]+)"', text)
        if mm:
            canvas = {"w": int(float(mm.group(1))), "h": int(float(mm.group(2)))}
        dn = re.search(r'<diagram[^>]*\bname="([^"]*)"', text)
        if dn:
            diagram_name = dn.group(1)

    if canvas["w"] == 0 and max_x > min_x:
        canvas = {"w": int(max_x - min_x), "h": int(max_y - min_y)}

    # De-dupe labels preserving order.
    seen = set()
    uniq_labels = []
    for l in labels:
        if l not in seen:
            seen.add(l)
            uniq_labels.append(l)

    service_tokens = []
    seen_s = set()
    for l in uniq_labels:
        if is_service_label(l) and l.lower() not in seen_s:
            seen_s.add(l.lower())
            service_tokens.append(l)

    # labelTokens: cleaned word bag for full-text ranking.
    label_tokens = set()
    for l in uniq_labels:
        for w in WORD_RE.findall(l.lower()):
            if len(w) >= 3 and w not in STOPWORDS:
                label_tokens.add(w)

    title = diagram_name.strip() or humanize(path.name)
    haystack = " ".join([title, path.name] + uniq_labels)

    return {
        "title": title,
        "canvas": canvas,
        "serviceTokens": service_tokens,
        "labelTokens": sorted(label_tokens),
        "scenarioAliases": detect_scenarios(haystack),
        "zoneCount": zone_count,
        "iconCount": icon_count,
        "_level_texts": uniq_labels,  # consumed by caller, dropped from output
    }


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def git_sha(repo: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except Exception:
        return None


def git_commit_date(repo: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), "show", "-s", "--format=%cI", "HEAD"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except Exception:
        return None


def build_provenance_map(repo: Path, glob: str) -> dict[str, str]:
    """md5 -> relative path, for .drawio files under repo matching glob."""
    out = {}
    if not repo or not repo.exists():
        return out
    for p in repo.glob(glob):
        if p.is_file():
            out.setdefault(md5(p), str(p.relative_to(repo)))
    return out


def slugify_id(filename: str) -> str:
    stem = re.sub(r"\.drawio$", "", filename)
    return re.sub(r"[^a-zA-Z0-9]+", "-", stem).strip("-").lower()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--templates-dir", type=Path, default=Path("assets/templates"))
    ap.add_argument("--out", type=Path, default=Path("assets/template-index.json"))
    ap.add_argument("--architecture-center", type=Path,
                    default=Path("/tmp/architecture-center"))
    ap.add_argument("--btp-solution-diagrams", type=Path,
                    default=Path.home() / "tools" / "btp-solution-diagrams")
    args = ap.parse_args()

    tdir = args.templates_dir
    if not tdir.is_dir():
        raise SystemExit(f"templates dir not found: {tdir}")

    ac_map = build_provenance_map(args.architecture_center, "docs/ref-arch/**/*.drawio")
    btp_map = build_provenance_map(args.btp_solution_diagrams,
                                   "assets/editable-diagram-examples/*.drawio")

    entries = []
    for path in sorted(tdir.glob("*.drawio")):
        parsed = parse_template(path)
        level_texts = parsed.pop("_level_texts")
        h = md5(path)
        if h in ac_map:
            source, source_path = "SAP/architecture-center", ac_map[h]
        elif h in btp_map:
            source, source_path = "SAP/btp-solution-diagrams", btp_map[h]
        else:
            source, source_path = "unknown", None

        entry = {
            "id": slugify_id(path.name),
            "file": path.name,
            "level": infer_level(path.name, [parsed["title"]] + level_texts),
            # family keys off the strong signals (title + filename + detected
            # scenario aliases), not every connector/legend label, which would
            # otherwise over-classify (e.g. any "agent" label -> ai).
            "family": infer_family(" ".join(
                [parsed["title"], path.name] + parsed["scenarioAliases"])),
            "title": parsed["title"],
            "canvas": parsed["canvas"],
            "serviceTokens": parsed["serviceTokens"],
            "labelTokens": parsed["labelTokens"],
            "scenarioAliases": parsed["scenarioAliases"],
            "zoneCount": parsed["zoneCount"],
            "iconCount": parsed["iconCount"],
            "source": source,
            "sourcePath": source_path,
        }
        entries.append(entry)

    entries.sort(key=lambda e: e["id"])

    ac_sha = git_sha(args.architecture_center)
    btp_sha = git_sha(args.btp_solution_diagrams)
    dates = [d for d in (git_commit_date(args.architecture_center),
                         git_commit_date(args.btp_solution_diagrams)) if d]
    generated_at = max(dates) if dates else None  # deterministic: from source commits

    index = {
        "meta": {
            "schemaVersion": 1,
            "description": (
                "SAP reference template corpus: real editable .drawio diagrams "
                "for the scaffold path (copy closest diagram + surgical edits)."
            ),
            "generatedAt": generated_at,
            "generatedBy": "scripts/build-template-index.py",
            "templateCount": len(entries),
            "sources": [
                {
                    "repo": "SAP/architecture-center",
                    "url": "https://github.com/SAP/architecture-center",
                    "license": "Apache-2.0",
                    "commit": ac_sha,
                    "path": "docs/ref-arch/RAxxxx/**/drawio/*.drawio",
                },
                {
                    "repo": "SAP/btp-solution-diagrams",
                    "url": "https://github.com/SAP/btp-solution-diagrams",
                    "license": "Apache-2.0",
                    "commit": btp_sha,
                    "path": "assets/editable-diagram-examples/*.drawio",
                },
            ],
        },
        "templates": entries,
    }

    args.out.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"wrote {args.out} with {len(entries)} templates")
    fams: dict[str, int] = {}
    lvls: dict[str, int] = {}
    for e in entries:
        fams[e["family"]] = fams.get(e["family"], 0) + 1
        lvls[e["level"]] = lvls.get(e["level"], 0) + 1
    print("by family:", dict(sorted(fams.items())))
    print("by level :", dict(sorted(lvls.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
