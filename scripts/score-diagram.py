#!/usr/bin/env python3
"""Corpus-similarity + SAP-likeness scorer for sap-diagrams-pro.

Every generated ``.drawio`` diagram can be scored two ways:

  * **reference-free** — ``sap_likeness(fp)`` gives a 0..100 "how SAP-like is
    this" signal from a weighted blend of the conventions the engine is meant
    to honour (white canvas, Horizon palette, Helvetica, canonical flow pills,
    composition zones, bundled icons, grid snap, absolute arcs, edge label
    backgrounds, few external images).
  * **reference-based** — ``compare(ref, cand)`` gives a 0..100 similarity
    between a candidate and one real SAP reference across per-dimension
    closeness; ``score_corpus(cand, dir)`` takes the best ``compare`` over a
    whole corpus of references.

The score is a *fingerprint* similarity: it captures structure and style, not
literal content, so two diagrams of different scenarios that both follow the
SAP conventions still fingerprint alike.

Repo conventions this scorer keys on (see ``scripts/validate-drawio.py`` for the
Horizon palette, ``assets/canonical-pills.json`` for the pill vocabulary):

  * zones     — area rects: rounded=1, absoluteArcSize=1, strokeWidth=1.5,
                verticalAlign=top (BTP/non-SAP palette fills), *not* pills.
  * pills      — rounded=1;arcSize=50 cells; labels matched against the
                 canonical pill vocabulary to split canonical vs novelty.
  * icons      — shape=image with an inline ``data:`` URI (svg or base64 png);
                 external ``http(s)`` images are counted separately (a smell).
  * grid snap  — fraction of geometry coords on the 10px grid (gridSize=10).

Usage:
  score-diagram.py --sap-like  cand.drawio            # reference-free 0..100
  score-diagram.py --compare    ref.drawio cand.drawio # similarity 0..100
  score-diagram.py --corpus     dir/ cand.drawio [--top N] [--min-score S]
  add --json to any of the above for machine-readable output.

Exit codes: 0 on success; with ``--corpus --min-score S`` exits 2 if the best
corpus match scores below S (so it can gate a CI loop).

Standard library only.
"""
from __future__ import annotations

import argparse
import html
import importlib.util
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_ASSETS = _REPO / "assets"


# --- Palette + vocabulary loading ----------------------------------------------
#
# The single source of truth for the Horizon palette is scripts/validate-drawio.py
# (owned by another module). We import it dynamically — its filename has a hyphen
# so it cannot be a normal ``import`` — and fall back to a hardcoded snapshot when
# it is unavailable, exactly like the validator's own layout.

_PALETTE_FALLBACK = {
    # borders
    "#0070F2", "#0070F3", "#475E75", "#475F75", "#188918", "#C35500",
    "#D20A0A", "#07838F", "#5D36FF", "#470BED", "#4628EC", "#CC00DC",
    # fills
    "#EBF8FF", "#F5F6F7", "#FFFFFF", "#F5FAE5", "#FFF8D6", "#FFEAF4",
    "#DAFDF5", "#F1ECFF", "#F1EDFF", "#FFF0FA", "#5B738B", "#E0B400",
    # text
    "#1D2D3E", "#1D2D3D", "#556B82", "#266F3A", "#FFFFFF",
}


def _load_horizon_palette() -> set[str]:
    """Union of Horizon border/fill/text colours from the validator, upper-cased."""
    validator = _HERE / "validate-drawio.py"
    try:
        spec = importlib.util.spec_from_file_location("_sdp_validate", validator)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod  # dataclass introspection needs this
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            palette: set[str] = set()
            for name in ("HORIZON_BORDERS", "HORIZON_FILLS", "HORIZON_TEXT",
                         "STRUCTURAL_STROKES"):
                for c in getattr(mod, name, set()):
                    if isinstance(c, str) and c.startswith("#"):
                        palette.add(c.upper())
            if palette:
                return palette
    except Exception:  # pragma: no cover - best-effort import
        pass
    return {c.upper() for c in _PALETTE_FALLBACK}


def _load_canonical_pills() -> set[str]:
    """Canonical pill labels (lower-cased) from assets/canonical-pills.json."""
    path = _ASSETS / "canonical-pills.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k).strip().lower() for k in data}
    except Exception:  # pragma: no cover - degrade gracefully
        return set()


SAP_PALETTE = _load_horizon_palette()
CANONICAL_PILL_VOCAB = _load_canonical_pills()

# Fonts the engine uses; anything else is a non-SAP smell.
SAP_FONTS = {"helvetica", "arial", "helvetica,arial,sans-serif", "72", "72,helvetica,arial,sans-serif"}
# Stroke widths that appear in the SAP libraries.
SAP_STROKES = {1.0, 1.5, 2.0, 3.0, 4.0}
# Coords are measured against a 10px grid (gridSize=10). SAP's own references are
# only ~20-25% snapped (sizes/offsets are content-driven), so full grid credit is
# earned at this empirically-observed baseline, not at 100%.
GRID_STEP = 10
GRID_TARGET = 0.20


# --- Regexes -------------------------------------------------------------------

HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}\b")
DATA_URI_RE = re.compile(r"data:image/[^&\";]+")
INLINE_ICON_RE = re.compile(r"shape=image[^\"]*image=data:image/(?:svg\+xml|png|jpeg|gif)")
STENCIL_ICON_RE = re.compile(r"shape=mxgraph\.")
EXTERNAL_IMAGE_RE = re.compile(r"image=https?://")
ARC50_RE = re.compile(r"arcSize=50\b")
ABS_ARC_RE = re.compile(r"absoluteArcSize=1\b")
# Edge labels punch through connectors via a background fill; the engine uses
# white (#FFFFFF), draw.io's "default" also counts.
LABEL_BG_RE = re.compile(r"labelBackgroundColor=(?:default|#[Ff]{6}|#[Ff]{3})\b")
FONT_RE = re.compile(r"fontFamily=([^;\"]+)")
STROKE_RE = re.compile(r"strokeWidth=([0-9.]+)")
PAGE_BG_RE = re.compile(r'(?:background|pageBackgroundColor)="([^"]+)"')
STOPWORDS = {
    "a", "an", "and", "app", "apps", "architecture", "as", "at", "be", "by",
    "cloud", "diagram", "for", "from", "in", "into", "is", "l0", "l1", "l2",
    "l3", "of", "on", "or", "page", "sap", "solution", "the", "to", "via",
    "with", "level",
}


# --- Fingerprint ---------------------------------------------------------------


@dataclass
class Fingerprint:
    path: str
    canvas_w: int = 0
    canvas_h: int = 0
    cells_total: int = 0
    vertices: int = 0
    edges: int = 0
    zones: int = 0
    zone_depth: int = 0            # max nested zone depth observed
    icons: int = 0                 # inline + stencil icon assets
    icons_inline: int = 0          # bundled inline data: URI icons (preferred)
    icons_stencil: int = 0         # mxgraph.* stencil icons
    external_images: int = 0       # image=http(s)://  — smell
    pills: int = 0
    canonical_pill_count: int = 0
    novelty_pill_count: int = 0
    grid_snap_rate: float = 0.0
    has_absolute_arc: bool = False
    has_label_bg: bool = False     # labelBackgroundColor=default on edge labels
    page_background: str = ""
    sap_logo_count: int = 0
    palette: set[str] = field(default_factory=set)
    edge_palette: set[str] = field(default_factory=set)
    fonts: set[str] = field(default_factory=set)
    stroke_widths: set[float] = field(default_factory=set)
    pill_vocab: set[str] = field(default_factory=set)
    label_tokens: set[str] = field(default_factory=set)
    label_count: int = 0


def _style_dict(style: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in style.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _clean_label(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _tokens(text: str) -> set[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    words = re.findall(r"[A-Za-z0-9]+", text.replace("_", " ").replace("-", " ").replace("/", " "))
    return {w.lower() for w in words if len(w) >= 2 and w.lower() not in STOPWORDS}


# BTP / non-SAP / semantic AREA fills (foundation.md). A rounded rect carrying one
# of these fills IS a composition zone regardless of its arcSize — this is what
# lets the scorer recognise BOTH our engine's zones (arcSize 24/32) AND the SAP
# reference templates' canonical arcSize=16 zones (else a scaffolded real SAP
# diagram would score LOWER than our procedural output, which is backwards).
_ZONE_AREA_FILLS = {
    "#EBF8FF",  # SAP/BTP area
    "#F5F6F7",  # non-SAP area
    "#F5FAE5",  # positive
    "#F1ECFF",  # accent indigo (authorization)
    "#FFF0FA",  # accent pink (trust)
    "#DAFDF5",  # accent teal
}
_FILL_RE = re.compile(r"fillColor=(#[0-9A-Fa-f]{6})")


def _is_zone_style(style: str) -> bool:
    """SAP composition zone: a rounded area rect (never an arcSize=50 pill, never
    a shape/image cell). Detected by an area fill in the SAP palette OR — for the
    engine's no-fill nested zones — a bold top label with a thin structural stroke.
    """
    if "rounded=1" not in style:
        return False
    if ARC50_RE.search(style) or "shape=" in style or "image=" in style:
        return False
    m = _FILL_RE.search(style)
    if m and m.group(1).upper() in _ZONE_AREA_FILLS:
        return True
    # engine's no-fill nested zone idiom
    return (
        "verticalAlign=top" in style
        and "absoluteArcSize=1" in style
        and ("strokeWidth=1.5" in style or "strokeWidth=1" in style)
    )


def fingerprint(path: str | Path) -> Fingerprint:
    path = Path(path)
    fp = Fingerprint(path=str(path))
    text = path.read_text(encoding="utf-8")

    # Style-level signals scanned over the whole doc (robust even if XML is odd).
    # Strip data: URIs first so embedded SVG/PNG payloads don't pollute palette.
    style_text = DATA_URI_RE.sub("", text)
    fp.palette = {h.upper() for h in HEX_RE.findall(style_text)}
    fp.fonts = {f.strip().lower() for f in FONT_RE.findall(style_text)}
    fp.stroke_widths = {float(s) for s in STROKE_RE.findall(style_text)}
    fp.has_absolute_arc = bool(ABS_ARC_RE.search(text))
    fp.has_label_bg = bool(LABEL_BG_RE.search(text))
    bg = PAGE_BG_RE.search(style_text)
    if bg:
        fp.page_background = bg.group(1).strip().lower()

    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return fp  # style-level signals already captured; structure stays zero

    graph = root.find(".//mxGraphModel")
    scope = graph if graph is not None else root
    if graph is not None:
        fp.canvas_w = int(graph.get("pageWidth") or graph.get("dx") or 0)
        fp.canvas_h = int(graph.get("pageHeight") or graph.get("dy") or 0)
        if not fp.page_background:
            b = (graph.get("background") or graph.get("pageBackgroundColor") or "").strip().lower()
            fp.page_background = b

    cells = scope.findall(".//mxCell")
    fp.cells_total = len(cells)
    cells_by_id: dict[str, ET.Element] = {c.get("id"): c for c in cells if c.get("id")}
    parent_by_child = {id(child): parent for parent in scope.iter() for child in list(parent)}

    # Label tokens across every value/label/name attribute in the tree.
    labels: set[str] = set()
    for elem in scope.iter():
        for attr in ("value", "label", "name"):
            raw = elem.get(attr)
            if raw:
                lbl = _clean_label(raw)
                if lbl:
                    labels.add(lbl)
    fp.label_count = len(labels)
    for lbl in labels:
        fp.label_tokens |= _tokens(lbl)

    coords: list[float] = []
    zone_ids: set[str] = set()

    for c in cells:
        style = c.get("style") or ""
        if c.get("vertex") == "1":
            fp.vertices += 1
            sd = _style_dict(style)
            if INLINE_ICON_RE.search(style):
                fp.icons += 1
                fp.icons_inline += 1
            elif STENCIL_ICON_RE.search(style):
                fp.icons += 1
                fp.icons_stencil += 1
            if EXTERNAL_IMAGE_RE.search(style):
                fp.external_images += 1
            image = sd.get("image", "").lower()
            if "sap_logo" in image or "sap-logo" in image:
                fp.sap_logo_count += 1

            if ARC50_RE.search(style):
                fp.pills += 1
                raw = c.get("value") or ""
                if not raw:
                    parent = parent_by_child.get(id(c))
                    if parent is not None and parent.tag in ("UserObject", "object"):
                        raw = parent.get("value") or parent.get("label") or ""
                label = _clean_label(raw).strip().lower()
                if label:
                    fp.pill_vocab.add(label)
                    if label in CANONICAL_PILL_VOCAB:
                        fp.canonical_pill_count += 1
                    else:
                        fp.novelty_pill_count += 1
            elif _is_zone_style(style):
                fp.zones += 1
                if c.get("id"):
                    zone_ids.add(c.get("id"))

            geo = c.find("mxGeometry")
            if geo is not None:
                for attr in ("x", "y", "width", "height"):
                    v = geo.get(attr)
                    if v is not None:
                        try:
                            coords.append(float(v))
                        except ValueError:
                            pass
        elif c.get("edge") == "1":
            fp.edges += 1
            stroke = _style_dict(style).get("strokeColor", "").upper()
            if stroke.startswith("#"):
                fp.edge_palette.add(stroke)

    # Zone nesting depth: how many zone cells appear on a cell's parent chain.
    if zone_ids:
        def depth_of(cell: ET.Element) -> int:
            depth, seen, pid = 0, set(), cell.get("parent")
            while pid and pid not in seen:
                seen.add(pid)
                parent = cells_by_id.get(pid)
                if parent is None:
                    break
                if parent.get("id") in zone_ids:
                    depth += 1
                pid = parent.get("parent")
            return depth
        fp.zone_depth = max((depth_of(c) for c in cells if c.get("vertex") == "1"), default=0)

    if coords:
        snapped = sum(1 for v in coords if abs(v - round(v)) < 1e-6 and int(round(v)) % GRID_STEP == 0)
        fp.grid_snap_rate = snapped / len(coords)

    return fp


# --- Scoring helpers -----------------------------------------------------------


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / max(1, len(a | b))


def _ratio(a: float, b: float) -> float:
    if a == 0 and b == 0:
        return 1.0
    if a == 0 or b == 0:
        return 0.0
    return min(a, b) / max(a, b)


@dataclass
class SapLikenessResult:
    score: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)


@dataclass
class CompareResult:
    score: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)
    diffs: list[str] = field(default_factory=list)


def sap_likeness(fp: Fingerprint, *, validator_errors: int = 0) -> SapLikenessResult:
    """Reference-free 0..100 "how SAP-like" score.

    Each part is a 0..1 sub-score; the score is their weighted mean. Weights
    encode what most makes a diagram read as an SAP Architecture-Center diagram:
    a clean white canvas and a clean validator run are table-stakes (weight 2),
    palette/pill-vocabulary/zones/icons are the strong stylistic tells
    (weight 1.25-1.5), and micro-conventions (abs-arc, label-bg) are minor
    (weight 0.5).
    """
    r = SapLikenessResult()
    p: dict[str, float] = {}
    accepted_bg = {"", "none", "default", "#ffffff", "#fff"}

    p["page_bg"] = 1.0 if fp.page_background.lower() in accepted_bg else 0.0
    if not p["page_bg"]:
        r.issues.append(f"non-white page background {fp.page_background!r}")

    p["validator"] = 1.0 if validator_errors == 0 else 0.0
    if validator_errors:
        r.issues.append(f"{validator_errors} validator error(s)")

    # Zones: at least one composition zone; credit saturates at 2 zones.
    p["zones"] = min(1.0, fp.zones / 2.0)
    if fp.zones == 0:
        r.issues.append("no SAP-style composition zones detected")

    # Icons: expect bundled icons once there's real content on the canvas.
    p["icons"] = min(1.0, fp.icons / 3.0) if fp.vertices >= 4 else 1.0
    if fp.vertices >= 4 and fp.icons == 0:
        r.issues.append("no bundled service icons detected")

    # Pills: only expected once there are flows to annotate.
    p["pills"] = min(1.0, fp.pills / 3.0) if fp.edges >= 2 else 1.0
    if fp.edges >= 2 and fp.pills == 0:
        r.issues.append("no SAP-style flow pills detected")

    # Pill vocabulary: reward canonical labels, penalise novelty.
    if fp.pills:
        p["pill_vocab"] = max(0.0, 1.0 - fp.novelty_pill_count / fp.pills)
        if fp.novelty_pill_count:
            r.issues.append(f"{fp.novelty_pill_count} non-canonical pill label(s)")
    else:
        p["pill_vocab"] = 0.8  # neutral: no pills to judge

    visible = {c.upper() for c in fp.palette}
    p["palette"] = (len(visible & SAP_PALETTE) / len(visible)) if visible else 1.0
    if p["palette"] < 1.0:
        r.issues.append(f"off-palette colors: {sorted(visible - SAP_PALETTE)[:6]}")

    p["edge_palette"] = (len(fp.edge_palette & SAP_PALETTE) / len(fp.edge_palette)) if fp.edge_palette else 1.0

    fonts = fp.fonts
    p["fonts"] = 1.0 if not fonts or fonts <= SAP_FONTS else 0.0
    if not p["fonts"]:
        r.issues.append(f"non-SAP font families: {sorted(fonts - SAP_FONTS)}")

    p["strokes"] = (len(fp.stroke_widths & SAP_STROKES) / len(fp.stroke_widths)) if fp.stroke_widths else 1.0

    p["grid_snap"] = min(1.0, fp.grid_snap_rate / GRID_TARGET)
    if fp.grid_snap_rate < GRID_TARGET:
        r.issues.append(f"grid-snap rate {fp.grid_snap_rate * 100:.1f}% (target {GRID_TARGET * 100:.0f}%)")

    p["abs_arc"] = 1.0 if fp.has_absolute_arc else 0.6
    p["label_bg"] = 1.0 if (fp.has_label_bg or fp.edges == 0) else 0.8
    p["external_images"] = max(0.0, 1.0 - min(fp.external_images, 5) * 0.2)
    if fp.external_images:
        r.issues.append(f"{fp.external_images} external image(s)")

    weights = {
        "page_bg": 2.0,          # dark/branded canvas is the loudest non-SAP tell
        "validator": 2.0,        # a diagram that fails validation isn't SAP-clean
        "zones": 1.5,            # zone composition is core to the SAP layout
        "palette": 1.5,          # Horizon palette adherence
        "pill_vocab": 1.25,      # canonical flow verbs vs invented ones
        "icons": 1.0,            # bundled SAP/generic icons over bare boxes
        "fonts": 1.0,            # Helvetica/Arial family
        "grid_snap": 1.0,        # tidy geometry on the 10px grid
        "external_images": 1.0,  # remote images are fragile & off-brand
        "pills": 0.75,           # presence of flow pills (weaker than their vocab)
        "edge_palette": 0.75,    # connector colours on-palette
        "strokes": 0.75,         # standard stroke widths
        "abs_arc": 0.5,          # absoluteArcSize convention
        "label_bg": 0.5,         # edge label background = default
    }
    total = sum(weights[k] for k in p)
    r.score = round(sum(p[k] * weights[k] for k in p) / total * 100, 1)
    r.breakdown = p
    return r


def compare(ref: Fingerprint, cand: Fingerprint) -> CompareResult:
    """0..100 fingerprint similarity between a candidate and a reference.

    ``compare(x, x)`` is exactly 100. Each dimension is a 0..1 closeness; the
    score is their weighted mean. Content-bearing dimensions (label tokens,
    palette, zones, icons, pill vocabulary) carry the most weight.
    """
    r = CompareResult()
    p: dict[str, float] = {}

    p["canvas"] = 1.0 if (ref.canvas_w == cand.canvas_w and ref.canvas_h == cand.canvas_h) else 0.0
    if not p["canvas"]:
        r.diffs.append(f"canvas {cand.canvas_w}x{cand.canvas_h} vs ref {ref.canvas_w}x{ref.canvas_h}")

    if ref.zones or cand.zones:
        p["zones"] = _ratio(ref.zones, cand.zones)
    if ref.zone_depth or cand.zone_depth:
        p["zone_depth"] = _ratio(ref.zone_depth, cand.zone_depth)
        if ref.zone_depth != cand.zone_depth:
            r.diffs.append(f"zone depth {cand.zone_depth} vs ref {ref.zone_depth}")

    p["icons"] = _ratio(ref.icons, cand.icons)
    p["edges"] = _ratio(ref.edges, cand.edges)
    p["vertices"] = _ratio(ref.vertices, cand.vertices)
    p["pills"] = _ratio(ref.pills, cand.pills) if (ref.pills or cand.pills) else 1.0

    if ref.pills or cand.pills:
        ref_rate = ref.canonical_pill_count / max(1, ref.pills)
        cand_rate = cand.canonical_pill_count / max(1, cand.pills)
        if ref.pills == 0:
            p["pill_vocab"] = 1.0 if cand_rate >= 0.6 else cand_rate
        else:
            p["pill_vocab"] = 1.0 if cand_rate >= ref_rate else cand_rate / max(0.01, ref_rate)
        if cand.novelty_pill_count > ref.novelty_pill_count:
            r.diffs.append(f"novelty pills {cand.novelty_pill_count} vs ref {ref.novelty_pill_count}")

    p["palette"] = _jaccard(ref.palette, cand.palette)
    extra = cand.palette - ref.palette
    if extra:
        r.diffs.append(f"palette colours not in ref: {sorted(extra)[:8]}")
    if ref.edge_palette or cand.edge_palette:
        p["edge_palette"] = _jaccard(ref.edge_palette, cand.edge_palette)

    # Fonts: candidate ⊆ ref is full credit (SAP files mix Arial+Helvetica).
    p["fonts"] = 1.0 if (cand.fonts and ref.fonts and cand.fonts <= ref.fonts) else _jaccard(ref.fonts, cand.fonts)
    p["strokes"] = _jaccard(ref.stroke_widths, cand.stroke_widths)
    p["label_count"] = _ratio(ref.label_count, cand.label_count)
    p["label_tokens"] = _jaccard(ref.label_tokens, cand.label_tokens)
    if p["label_tokens"] < 0.8:
        missing = sorted(ref.label_tokens - cand.label_tokens)[:8]
        r.diffs.append(f"label token drift — missing={missing}")

    p["external_images"] = 1.0 if cand.external_images <= ref.external_images else _ratio(ref.external_images, cand.external_images)
    p["abs_arc"] = 1.0 if ref.has_absolute_arc == cand.has_absolute_arc else 0.5
    p["label_bg"] = 1.0 if ref.has_label_bg == cand.has_label_bg else 0.5

    if ref.grid_snap_rate >= GRID_TARGET:
        p["grid_snap"] = 1.0 if cand.grid_snap_rate >= ref.grid_snap_rate * 0.95 else cand.grid_snap_rate
    else:
        p["grid_snap"] = 1.0 if cand.grid_snap_rate >= ref.grid_snap_rate else cand.grid_snap_rate / max(0.01, ref.grid_snap_rate)

    weights = {
        "canvas": 1.0,
        "zones": 1.5,
        "zone_depth": 1.0,
        "icons": 1.5,
        "edges": 1.0,
        "vertices": 0.5,
        "pills": 0.5,
        "pill_vocab": 1.5,
        "palette": 1.5,
        "edge_palette": 1.0,
        "fonts": 1.0,
        "strokes": 0.5,
        "label_count": 0.5,
        "label_tokens": 2.0,
        "external_images": 0.5,
        "abs_arc": 0.5,
        "label_bg": 0.5,
        "grid_snap": 1.0,
    }
    total = sum(weights[k] for k in p)
    r.score = round(sum(p[k] * weights[k] for k in p) / total * 100, 1)
    r.breakdown = p
    return r


@dataclass
class CorpusResult:
    score: float = 0.0                       # best compare score across the corpus
    best_match: str = ""
    matches: list[tuple[str, float]] = field(default_factory=list)  # (path, score) sorted desc
    corpus_size: int = 0


def score_corpus(cand: str | Path, corpus_dir: str | Path, *, top: int = 3) -> CorpusResult:
    """Best ``compare`` score of ``cand`` against every ``.drawio`` in a dir.

    Degrades gracefully to an empty result if the corpus directory is missing
    or contains no ``.drawio`` files.
    """
    result = CorpusResult()
    corpus_dir = Path(corpus_dir)
    if not corpus_dir.is_dir():
        return result
    refs = sorted(p for p in corpus_dir.glob("*.drawio") if p.is_file())
    result.corpus_size = len(refs)
    if not refs:
        return result

    cand_fp = fingerprint(cand)
    scored: list[tuple[str, float]] = []
    for ref in refs:
        try:
            scored.append((str(ref), compare(fingerprint(ref), cand_fp).score))
        except Exception:
            continue
    scored.sort(key=lambda x: x[1], reverse=True)
    result.matches = scored[:max(1, top)]
    if scored:
        result.best_match, result.score = scored[0]
    return result


# --- CLI -----------------------------------------------------------------------


def _fp_to_jsonable(fp: Fingerprint) -> dict:
    d = asdict(fp)
    for k, v in d.items():
        if isinstance(v, set):
            d[k] = sorted(v)
    return d


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SAP-likeness / corpus-similarity scorer for .drawio diagrams.")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--sap-like", metavar="CAND", type=Path, help="reference-free SAP-likeness score")
    mode.add_argument("--compare", nargs=2, metavar=("REF", "CAND"), type=Path, help="similarity vs one reference")
    mode.add_argument("--corpus", nargs=2, metavar=("DIR", "CAND"), type=Path, help="best similarity across a corpus dir")
    ap.add_argument("--top", type=int, default=3, help="corpus: number of top matches to report")
    ap.add_argument("--min-score", type=float, default=None, help="corpus: exit nonzero if best < this")
    ap.add_argument("--validator-errors", type=int, default=0, help="sap-like: validator error count to fold in")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    if args.sap_like:
        fp = fingerprint(args.sap_like)
        res = sap_likeness(fp, validator_errors=args.validator_errors)
        if args.json:
            print(json.dumps({"mode": "sap-like", "score": res.score,
                              "breakdown": res.breakdown, "issues": res.issues,
                              "fingerprint": _fp_to_jsonable(fp)}, indent=2))
        else:
            print(f"file      : {args.sap_like}")
            print(f"sap-like  : {res.score:.1f}/100")
            for k, v in sorted(res.breakdown.items(), key=lambda x: x[1]):
                print(f"   {k:16s} {v * 100:5.1f}%  {'█' * int(v * 20)}")
            for issue in res.issues:
                print(f"  - {issue}")
        return 0

    if args.compare:
        ref_p, cand_p = args.compare
        ref, cand = fingerprint(ref_p), fingerprint(cand_p)
        res = compare(ref, cand)
        like = sap_likeness(cand)
        if args.json:
            print(json.dumps({"mode": "compare", "score": res.score,
                              "sap_likeness": like.score, "breakdown": res.breakdown,
                              "diffs": res.diffs, "reference": _fp_to_jsonable(ref),
                              "candidate": _fp_to_jsonable(cand)}, indent=2))
        else:
            print(f"reference : {ref_p}")
            print(f"candidate : {cand_p}")
            print(f"score     : {res.score:.1f}/100")
            print(f"sap-like  : {like.score:.1f}/100")
            for k, v in sorted(res.breakdown.items(), key=lambda x: x[1]):
                print(f"   {k:16s} {v * 100:5.1f}%  {'█' * int(v * 20)}")
            for d in res.diffs:
                print(f"  - {d}")
        return 0

    # --corpus
    corpus_dir, cand_p = args.corpus
    res = score_corpus(cand_p, corpus_dir, top=args.top)
    if args.json:
        print(json.dumps({"mode": "corpus", "score": res.score,
                          "best_match": res.best_match, "corpus_size": res.corpus_size,
                          "matches": [{"path": p, "score": s} for p, s in res.matches]}, indent=2))
    else:
        print(f"candidate : {cand_p}")
        print(f"corpus    : {corpus_dir} ({res.corpus_size} reference(s))")
        if res.corpus_size == 0:
            print("no references found — corpus scoring skipped")
        else:
            print(f"best      : {res.score:.1f}/100  ({res.best_match})")
            for path, score in res.matches:
                print(f"   {score:5.1f}  {path}")
    if args.min_score is not None and res.corpus_size > 0 and res.score < args.min_score:
        print(f"FAIL: best corpus score {res.score:.1f} < min {args.min_score:.1f}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
