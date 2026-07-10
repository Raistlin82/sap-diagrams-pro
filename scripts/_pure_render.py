#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""_pure_render.py — pure-Python PNG renderer for OUR emitted .drawio vocabulary (Task 10).

Renders a ``.drawio`` file to a PNG without draw.io / Electron: parse the XML
with :mod:`scripts._drawio_io`, resolve each ``mxCell``'s geometry, and paint
rounded rects, pills, ellipses, cylinders, text, images and edges with
Pillow. This is a *preview* renderer, not a draw.io clone — the goal is
geometric fidelity (right shapes, right colors, right positions) for the
specific, closed vocabulary ``generate-drawio.py`` emits (see
``assets/style-contract.json``), not pixel-parity with draw.io's own
renderer. Only that vocabulary is supported; anything else falls back to the
closest primitive (typically a plain rectangle, with a WARNING) rather than
raising. A cell's ``image=`` icon is composited whenever present regardless
of its ``shape=`` (a bare ``shape=image`` cell, or any other shape that
embeds one, e.g. a backend box's service icon or a capability chip's icon
grid entry) — never just for the one exact ``shape=image`` spelling.

────────────────────────────────────────────────────────────────────────────
SHA1 CONTRACT — read scripts/build-icon-atlas.py's module docstring first
────────────────────────────────────────────────────────────────────────────
An emitted ``.drawio`` embeds real ``image=data:...`` data-URIs (no more
``@{placeholder}`` tokens — those only appear in assets/style-contract.json's
documentation). To find the pre-rasterized PNG for one:

    1. normalize the cell's style:  ``style.replace(";base64,", ",")``
    2. extract the URI:             ``re.search(r"image=([^;]+)", style)``
    3. sha1 the URI string, look it up in assets/icon-atlas/index.json's
       ``by_sha1`` map -> PNG filename under assets/icon-atlas/.

``normalize_style``/``extract_image_value``/``sha1_of`` below are a
byte-for-byte copy of generate-drawio.py's ``_safe_img``/``_extract_image_uri``
(build-icon-atlas.py carries its own identical copy too — this is the THIRD
independent copy of the same four lines). tests/test_pure_render.py's
``test_sha1_normalization_matches_the_real_emitter`` cross-checks this copy
against the real generate-drawio.py functions (mirroring
tests/test_icon_atlas.py's identical guard for build-icon-atlas.py) so this
copy can't silently drift from the other two. If a lookup misses (bad/absent
sha1 AND no ``by_name`` fallback hit), we draw a grey placeholder circle and
print a WARNING — we never fail the whole render over one missing icon.

────────────────────────────────────────────────────────────────────────────
Geometry model
────────────────────────────────────────────────────────────────────────────
``decode_diagram_pages()`` (scripts/_drawio_io.py) returns the ``<diagram>``
element itself for inline pages but the ``<mxGraphModel>`` for compressed
ones — ``_model_of()`` below normalizes that before walking ``<root>``.

mxCell/`<object>`-wrapped-mxCell geometry is parent-relative: a vertex's
absolute position is its own (x, y) plus its parent's absolute position,
recursively, bottoming out at the implicit root layers ("0"/"1"). Edges are
always top-level in our vocabulary, so an edge's own waypoints
(``<Array as="points">``) are already absolute canvas coordinates. A vertex
parented to an *edge* (a protocol/interface pill riding an edge label) is a
different case again — mxGraph positions it via ``relative="1"`` fractional
placement along the edge's path plus a pixel ``<mxPoint as="offset">``; see
``resolve_edge_child_rects()``.

Determinism: no timestamps, no randomness; PNG bytes depend only on pixel
content + Pillow's (deterministic) PNG encoder, exactly like
build-icon-atlas.py's own PNGs. Text is set in the bundled Arimo family
(``assets/fonts/`` — Apache-2.0-repo-friendly metric-compatible Helvetica
substitute, SIL OFL-1.1 licensed, see REUSE.toml) resolved by absolute
path, so this holds ACROSS machines too, not just across repeated runs on
one: rendering no longer depends on whichever TrueType fonts (if any)
happen to be installed system-wide (previously ``DejaVuSans.ttf``, which
Pillow does not bundle and macOS does not ship — silently degrading every
render to ``ImageFont.load_default()``'s tiny bitmap font, tofu for
non-ASCII glyphs like the em dash used in every diagram title, and no
bold/italic distinction).

Usage:
    python3 scripts/_pure_render.py diagram.drawio --out preview.png --scale 2
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import io
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print(
        "ERROR: pillow is required to render PNGs.\n"
        "  pip install pillow",
        file=sys.stderr,
    )
    raise SystemExit(3)

from _drawio_io import decode_diagram_pages  # noqa: E402 (after the Pillow guard, by design)

ROOT = Path(__file__).resolve().parent.parent
ATLAS_DIR = ROOT / "assets" / "icon-atlas"
ATLAS_INDEX_PATH = ATLAS_DIR / "index.json"
FONTS_DIR = ROOT / "assets" / "fonts"

DEFAULT_PAGE_W = 850.0
DEFAULT_PAGE_H = 1100.0
BACKGROUND_RGB = (255, 255, 255)
PLACEHOLDER_RGB = (190, 190, 190)

_IMAGE_RE = re.compile(r"image=([^;]+)")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


class RenderError(Exception):
    """Raised for .drawio input the renderer cannot make sense of at all
    (no pages, no <mxGraphModel>/<root>) — anything narrower is handled
    per-cell with a WARNING instead, so one bad cell never sinks the render.
    """


# ─────────────────────────────────────────────────────────────────────────
# sha1 contract (see module docstring) — byte-for-byte copy of
# generate-drawio.py's _safe_img/_extract_image_uri (== build-icon-atlas.py's
# normalize_style/extract_image_value). tests/test_pure_render.py's
# test_sha1_normalization_matches_the_real_emitter guards against drift.
# ─────────────────────────────────────────────────────────────────────────
def normalize_style(style: str | None) -> str | None:
    """Byte-for-byte copy of generate-drawio.py's ``_safe_img``."""
    return style.replace(";base64,", ",") if style else style


def extract_image_value(style: str | None) -> str | None:
    """Byte-for-byte copy of generate-drawio.py's ``_extract_image_uri``."""
    if not style:
        return None
    m = _IMAGE_RE.search(normalize_style(style))
    return m.group(1) if m else None


def sha1_of(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────
# small generic helpers
# ─────────────────────────────────────────────────────────────────────────
def _safe_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def parse_style(style: str | None) -> dict[str, str]:
    """Split a drawio ``;``-delimited style string into a dict.

    Bare tokens with no ``=`` (``ellipse``, ``text``, ``rounded`` used
    without an explicit ``=1``) are stored as ``"1"`` so a single
    ``style.get(key) == "1"`` check works for both spellings our vocabulary
    uses.
    """
    out: dict[str, str] = {}
    for part in (style or "").split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, _, v = part.partition("=")
            out[k] = v
        else:
            out[part] = "1"
    return out


def parse_color(value: str | None) -> tuple[int, int, int] | None:
    """``#RRGGBB`` (or short ``#RGB``) -> an (r, g, b) tuple; ``None``/``"none"``/
    ``"default"``/anything unrecognized (e.g. a named CSS color, never used in
    our vocabulary) -> ``None``, meaning "don't paint this"."""
    if not value:
        return None
    v = value.strip()
    if v.lower() in ("none", "default", ""):
        return None
    if v.startswith("#") and len(v) == 7:
        try:
            return (int(v[1:3], 16), int(v[3:5], 16), int(v[5:7], 16))
        except ValueError:
            return None
    if v.startswith("#") and len(v) == 4:
        try:
            return tuple(int(c * 2, 16) for c in v[1:])  # type: ignore[return-value]
        except ValueError:
            return None
    return None


def strip_label_html(value: str | None) -> str:
    """Best-effort plain-text extraction from a (possibly rich-HTML) mxCell
    label. draw.io labels are HTML fragments (``<b>``, ``<font color=...>``,
    ``<p style=...>`` are common, e.g. the step-circle numbers); we render
    the whole label in ONE style (the cell's own fontColor/fontStyle/
    fontSize), so per-span markup is stripped rather than honored — this
    matches the "geometric fidelity, not pixel-parity" goal, and in our
    vocabulary the cell-level style already matches the inline one anyway.
    ``<br>`` becomes a newline so multi-line labels still wrap sensibly.
    """
    if not value:
        return ""
    text = _BR_RE.sub("\n", value)
    text = _TAG_RE.sub("", text)
    return html.unescape(text)


def corner_radius(style: dict[str, str], w: float, h: float) -> float:
    """Pixel corner radius for a rect honoring ``rounded=1``/``arcSize``/
    ``absoluteArcSize`` — unifies plain rects (radius 0), fixed-radius boxes
    (``absoluteArcSize=1``: arcSize IS the pixel radius) and pills
    (no ``absoluteArcSize``: arcSize is a 0-100 percentage of min(w, h); at
    arcSize=50 that's exactly min(w, h)/2, i.e. a full stadium/pill shape).
    Always clamped to <= min(w, h)/2 (mirrors mxgraph's own clamp — a radius
    can never exceed half the shorter side).
    """
    if style.get("rounded") != "1":
        return 0.0
    m = min(w, h)
    if m <= 0:
        return 0.0
    arc = _safe_float(style.get("arcSize"), 15.0)
    if style.get("absoluteArcSize") == "1":
        radius = arc
    else:
        radius = (arc / 100.0) * m
    return max(0.0, min(radius, m / 2.0))


def dash_spec(style: dict[str, str], scale: float) -> tuple[float, float] | None:
    """``None`` for a solid line; else an (on_px, off_px) pair to feed
    ``draw_polyline``. Per the spec: ``dashed=1`` alone (or a "wide" pattern
    like our own ``dashPattern=8 4``) -> a 6-4 dash; a "narrow" pattern like
    our own ``dashPattern=1 4`` (short on-segment) -> a smaller, denser
    dotted look. Both ``dashed`` styles set ``dashed=1``; ``dashPattern`` is
    the only signal that distinguishes them.
    """
    if style.get("dashed") != "1":
        return None
    dotted = False
    dp = style.get("dashPattern")
    if dp:
        nums = re.findall(r"[0-9.]+", dp)
        if len(nums) >= 2:
            on, off = float(nums[0]), float(nums[1])
            if off > 0 and on <= off / 2.0:
                dotted = True
    on_px, off_px = (2.0, 4.0) if dotted else (6.0, 4.0)
    return (max(1.0, on_px * scale), max(1.0, off_px * scale))


# ─────────────────────────────────────────────────────────────────────────
# Cell model + parsing
# ─────────────────────────────────────────────────────────────────────────
@dataclass
class Cell:
    id: str
    parent: str | None
    style: str
    value: str
    visible: bool
    vertex: bool
    edge: bool
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    relative: bool = False
    source: str | None = None
    target: str | None = None
    points: list[tuple[float, float]] = field(default_factory=list)
    offset: tuple[float, float] | None = None


def _model_of(page_root: ET.Element) -> ET.Element | None:
    """Normalize decode_diagram_pages()'s documented inline-vs-compressed
    asymmetry: it hands back the <diagram> for inline pages but the
    <mxGraphModel> itself for compressed ones."""
    if page_root.tag == "mxGraphModel":
        return page_root
    return page_root.find("mxGraphModel")


def parse_cells(root_container: ET.Element) -> tuple[dict[str, Cell], list[str]]:
    """Walk a ``<root>`` element's children (plain ``<mxCell>`` and
    ``<object>``-wrapped ``<mxCell>``, see the module docstring) into a
    ``(cells_by_id, doc_order_ids)`` pair. ``doc_order_ids`` only lists
    vertex/edge cells (not the two boilerplate layer cells id="0"/"1") and
    preserves file order, which is also draw.io's paint (z-)order.
    """
    cells: dict[str, Cell] = {}
    order: list[str] = []

    for child in root_container:
        if child.tag == "mxCell":
            cid = child.get("id")
            mxcell = child
            raw_value = mxcell.get("value")
        elif child.tag == "object":
            cid = child.get("id")
            mxcell = child.find("mxCell")
            if mxcell is None:
                continue
            raw_value = mxcell.get("value")
            if raw_value is None:
                raw_value = child.get("label")
        else:
            continue
        if not cid:
            continue

        vertex = mxcell.get("vertex") == "1"
        edge = mxcell.get("edge") == "1"
        cell = Cell(
            id=cid,
            parent=mxcell.get("parent"),
            style=mxcell.get("style") or "",
            value=raw_value or "",
            visible=mxcell.get("visible") != "0",
            vertex=vertex,
            edge=edge,
            source=mxcell.get("source"),
            target=mxcell.get("target"),
        )

        geom = mxcell.find("mxGeometry")
        if geom is not None:
            cell.x = _safe_float(geom.get("x"), 0.0)
            cell.y = _safe_float(geom.get("y"), 0.0)
            # Clamped to >= 0: a negative width/height would produce an
            # "inverted" box (x1 < x0) that PIL's rounded_rectangle rejects
            # outright (plain rectangle/ellipse silently no-op instead) --
            # never a crash risk from malformed input either way.
            cell.w = max(0.0, _safe_float(geom.get("width"), 0.0))
            cell.h = max(0.0, _safe_float(geom.get("height"), 0.0))
            cell.relative = geom.get("relative") == "1"
            points_el = geom.find("Array[@as='points']")
            if points_el is not None:
                cell.points = [
                    (_safe_float(pt.get("x"), 0.0), _safe_float(pt.get("y"), 0.0))
                    for pt in points_el.findall("mxPoint")
                ]
            offset_el = geom.find("mxPoint[@as='offset']")
            if offset_el is not None:
                cell.offset = (_safe_float(offset_el.get("x"), 0.0), _safe_float(offset_el.get("y"), 0.0))

        cells[cid] = cell
        if cell.visible and (vertex or edge):
            order.append(cid)

    return cells, order


# ─────────────────────────────────────────────────────────────────────────
# Geometry resolution: parent-relative vertices, edge polylines, then
# edge-parented ("relative") label/pill children -- in that dependency order.
# ─────────────────────────────────────────────────────────────────────────
def resolve_all_rects(cells: dict[str, Cell]) -> dict[str, tuple[float, float]]:
    """Absolute (x, y) top-left for every vertex NOT parented to an edge
    (those are resolved later by ``resolve_edge_child_rects``, once edge
    polylines are known). Recursion bottoms out at the implicit root layers
    ("0"/"1", absent from ``cells``) or at a non-vertex/edge parent."""
    memo: dict[str, tuple[float, float]] = {}
    visiting: set[str] = set()

    def resolve(cid: str) -> tuple[float, float]:
        if cid in memo:
            return memo[cid]
        if cid in visiting:
            print(f"WARNING: cyclic parent chain at cell {cid!r}; treating its position as (0, 0)", file=sys.stderr)
            return (0.0, 0.0)
        visiting.add(cid)
        cell = cells[cid]
        parent = cells.get(cell.parent) if cell.parent else None
        if parent is not None and parent.vertex:
            px, py = resolve(cell.parent)  # type: ignore[arg-type]
            pt = (px + cell.x, py + cell.y)
        else:
            pt = (cell.x, cell.y)
        visiting.discard(cid)
        memo[cid] = pt
        return pt

    for cid, cell in cells.items():
        if not cell.vertex:
            continue
        parent = cells.get(cell.parent) if cell.parent else None
        if parent is not None and parent.edge:
            continue  # edge-label child -- resolved in _resolve_edge_child_rects
        resolve(cid)
    return memo


def _edge_endpoint(
    ref_id: str | None, style: dict[str, str], abs_rects: dict[str, tuple[float, float]],
    cells: dict[str, Cell], is_source: bool,
) -> tuple[float, float] | None:
    if not ref_id or ref_id not in cells or not cells[ref_id].vertex:
        return None
    topleft = abs_rects.get(ref_id)
    if topleft is None:
        return None
    x, y = topleft
    cell = cells[ref_id]
    fx_key, fy_key = ("exitX", "exitY") if is_source else ("entryX", "entryY")
    fx, fy = style.get(fx_key), style.get(fy_key)
    if fx is not None and fy is not None:
        try:
            return (x + float(fx) * cell.w, y + float(fy) * cell.h)
        except ValueError:
            pass
    return (x + cell.w / 2.0, y + cell.h / 2.0)


def resolve_all_edges(
    cells: dict[str, Cell], abs_rects: dict[str, tuple[float, float]]
) -> dict[str, list[tuple[float, float]]]:
    """Each edge's full polyline (unscaled canvas coords): its resolved
    source anchor, then any explicit waypoints, then its resolved target
    anchor. Per the spec, an edge with no source/target and no waypoints
    (degenerate/malformed) is simply skipped -- there's nothing sane to draw.
    """
    paths: dict[str, list[tuple[float, float]]] = {}
    for cid, cell in cells.items():
        if not cell.edge:
            continue
        style = parse_style(cell.style)
        pts: list[tuple[float, float]] = []
        src = _edge_endpoint(cell.source, style, abs_rects, cells, is_source=True)
        if src:
            pts.append(src)
        pts.extend(cell.points)
        tgt = _edge_endpoint(cell.target, style, abs_rects, cells, is_source=False)
        if tgt:
            pts.append(tgt)
        if len(pts) >= 2:
            paths[cid] = pts
    return paths


def point_at_fraction(path: list[tuple[float, float]], frac: float) -> tuple[float, float]:
    """mxGraph's edge-label placement rule: frac=-1 is the path start,
    frac=0 its arc-length midpoint (the common case -- no explicit ``x`` on
    the label's relative geometry), frac=1 its end."""
    if not path:
        return (0.0, 0.0)
    if len(path) == 1:
        return path[0]
    seg_lens = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path, path[1:])]
    total = sum(seg_lens)
    if total <= 0:
        return path[0]
    target = (max(-1.0, min(1.0, frac)) + 1.0) / 2.0 * total
    acc = 0.0
    for i, seglen in enumerate(seg_lens):
        if acc + seglen >= target or i == len(seg_lens) - 1:
            local = 0.0 if seglen <= 0 else max(0.0, min(1.0, (target - acc) / seglen))
            (x0, y0), (x1, y1) = path[i], path[i + 1]
            return (x0 + (x1 - x0) * local, y0 + (y1 - y0) * local)
        acc += seglen
    return path[-1]


def resolve_edge_child_rects(
    cells: dict[str, Cell], edge_paths: dict[str, list[tuple[float, float]]]
) -> dict[str, tuple[float, float]]:
    """Absolute top-left for vertices parented to an EDGE (protocol/interface
    pills riding an edge, e.g. "Authenticate", "OData"): mxGraph places
    these at a fractional point along the edge's path (default: the
    midpoint) plus a pixel offset -- see the module docstring."""
    out: dict[str, tuple[float, float]] = {}
    for cid, cell in cells.items():
        if not cell.vertex:
            continue
        parent = cells.get(cell.parent) if cell.parent else None
        if parent is None or not parent.edge:
            continue
        path = edge_paths.get(cell.parent) if cell.parent else None
        if not path:
            out[cid] = (0.0, 0.0)
            continue
        frac = cell.x if cell.relative else 0.0
        px, py = point_at_fraction(path, frac)
        if cell.offset:
            out[cid] = (px + cell.offset[0], py + cell.offset[1])
        else:
            out[cid] = (px - cell.w / 2.0, py - cell.h / 2.0)
    return out


# ─────────────────────────────────────────────────────────────────────────
# Fonts (deterministic: no timestamps, a fixed family resolution order)
# ─────────────────────────────────────────────────────────────────────────
# Arimo (SIL OFL-1.1 -- see REUSE.toml): a Google-Fonts family metric-
# compatible with Arial/Helvetica, i.e. the same family our .drawio
# vocabulary's own fontFamily=Helvetica styles ask for. Bundled under
# assets/fonts/ so every environment renders from the SAME font bytes --
# see the module docstring's Determinism paragraph.
_FONT_CACHE: dict[tuple[str, int], "ImageFont.FreeTypeFont | ImageFont.ImageFont"] = {}
_warned_no_bundled_font = False
_warned_no_truetype = False

_FONT_FILENAMES = {
    (False, False): "Arimo-Regular.ttf",
    (True, False): "Arimo-Bold.ttf",
    (False, True): "Arimo-Italic.ttf",
    (True, True): "Arimo-BoldItalic.ttf",
}


def _font_filename(bold: bool, italic: bool) -> str:
    return _FONT_FILENAMES[(bool(bold), bool(italic))]


def load_font(size_px: int, bold: bool = False, italic: bool = False):
    """Resolve the bundled Arimo face for (bold, italic) by its ABSOLUTE
    path under assets/fonts/ FIRST, so rendering is identical on every
    machine (this repo ships the font; we never depend on what happens to
    be installed system-wide). Only if that bundled file is somehow
    missing/unreadable do we fall back to a bare-filename
    ImageFont.truetype() lookup (in case the system's own FreeType search
    path happens to resolve a same-named font) and, failing that, Pillow's
    non-scalable ImageFont.load_default() with a one-time WARNING -- text
    still renders (color/position honored), just with approximate metrics
    and no bold/italic distinction.
    """
    global _warned_no_bundled_font, _warned_no_truetype
    size_px = max(1, size_px)
    filename = _font_filename(bold, italic)
    key = (filename, size_px)
    cached = _FONT_CACHE.get(key)
    if cached is not None:
        return cached

    font = None
    bundled_path = FONTS_DIR / filename
    try:
        font = ImageFont.truetype(str(bundled_path), size_px)
    except Exception:
        if not _warned_no_bundled_font:
            print(
                f"WARNING: bundled font {bundled_path} not resolvable via "
                "ImageFont.truetype() (missing/corrupt assets/fonts/?); falling back "
                "to a bare-filename lookup on this system's own font search path -- "
                "renders may no longer be identical across environments.",
                file=sys.stderr,
            )
            _warned_no_bundled_font = True
        try:
            font = ImageFont.truetype(filename, size_px)
        except Exception:
            font = None

    if font is None:
        if not _warned_no_truetype:
            print(
                "WARNING: no usable TrueType font found (bundled or system); falling "
                "back to ImageFont.load_default() -- text will render with "
                "approximate metrics and no bold/italic distinction.",
                file=sys.stderr,
            )
            _warned_no_truetype = True
        try:
            font = ImageFont.load_default(size=size_px)
        except TypeError:
            font = ImageFont.load_default()  # Pillow < 10.1: no `size` kwarg

    _FONT_CACHE[key] = font
    return font


# ─────────────────────────────────────────────────────────────────────────
# Icon atlas (see the sha1 contract in the module docstring)
# ─────────────────────────────────────────────────────────────────────────
_ATLAS_INDEX_CACHE: dict | None = None
_ICON_CACHE: dict[str, "Image.Image"] = {}


def load_atlas_index() -> dict:
    global _ATLAS_INDEX_CACHE
    if _ATLAS_INDEX_CACHE is None:
        if ATLAS_INDEX_PATH.exists():
            _ATLAS_INDEX_CACHE = json.loads(ATLAS_INDEX_PATH.read_text(encoding="utf-8"))
        else:
            _ATLAS_INDEX_CACHE = {"by_name": {}, "by_sha1": {}}
    return _ATLAS_INDEX_CACHE


def load_icon(rel_path: str) -> "Image.Image | None":
    cached = _ICON_CACHE.get(rel_path)
    if cached is not None:
        return cached
    p = ATLAS_DIR / rel_path
    if p.exists():
        img = Image.open(p).convert("RGBA")  # .convert() fully decodes -- safe to detach from the file
    else:
        # Embedded fallback: the Claude Desktop / claude.ai bundle packs every
        # atlas PNG as base64 into index.json's ``embedded`` map (one file instead
        # of ~360 loose PNGs, to stay under the 200-file Skills upload limit).
        blob = load_atlas_index().get("embedded", {}).get(rel_path)
        if not blob:
            return None
        img = Image.open(io.BytesIO(base64.b64decode(blob))).convert("RGBA")
    _ICON_CACHE[rel_path] = img
    return img


def fit_icon(img: "Image.Image", box_w: int, box_h: int) -> "Image.Image":
    """Scale ``img`` to fit inside a box_w x box_h box, preserving aspect
    ratio (mirrors build-icon-atlas.py's own fit-then-center convention)."""
    if box_w <= 0 or box_h <= 0 or img.width <= 0 or img.height <= 0:
        return img
    scale = min(box_w / img.width, box_h / img.height)
    new_w, new_h = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    if (new_w, new_h) == img.size:
        return img
    return img.resize((new_w, new_h), Image.Resampling.LANCZOS)


def apply_opacity(img: "Image.Image", opacity_pct: float) -> "Image.Image":
    """Scale an RGBA image's alpha channel by opacity_pct/100 -- drawio's
    ``opacity=N`` style key (0-100), used by watermark image cells."""
    img = img.convert("RGBA")
    r, g, b, a = img.split()
    factor = max(0.0, min(100.0, opacity_pct)) / 100.0
    a = a.point(lambda v: int(v * factor))
    return Image.merge("RGBA", (r, g, b, a))


# ─────────────────────────────────────────────────────────────────────────
# Drawing primitives
# ─────────────────────────────────────────────────────────────────────────
def draw_polyline(
    draw: "ImageDraw.ImageDraw", points: list[tuple[float, float]], color: tuple[int, int, int],
    width: int, dash: tuple[float, float] | None,
) -> None:
    """A solid or dashed connected polyline. PIL has no native dash support,
    so a dashed line is walked manually in on/off phases that carry
    continuously across segment boundaries (so a multi-waypoint orthogonal
    edge still looks like ONE continuously-dashed line, not a fresh dash
    pattern restarting at each bend).
    """
    if len(points) < 2:
        return
    if dash is None:
        draw.line(points, fill=color, width=width, joint="curve")
        return
    on, off = dash
    on_phase = True
    dist_in_phase = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        seg_len = math.hypot(x1 - x0, y1 - y0)
        if seg_len <= 0:
            continue
        ux, uy = (x1 - x0) / seg_len, (y1 - y0) / seg_len
        traveled = 0.0
        while traveled < seg_len - 1e-9:
            phase_len = on if on_phase else off
            step = min(phase_len - dist_in_phase, seg_len - traveled)
            step = max(step, 0.0)
            if on_phase and step > 0:
                sx, sy = x0 + ux * traveled, y0 + uy * traveled
                ex, ey = x0 + ux * (traveled + step), y0 + uy * (traveled + step)
                draw.line([(sx, sy), (ex, ey)], fill=color, width=width)
            traveled += step
            dist_in_phase += step
            if dist_in_phase >= phase_len - 1e-9:
                dist_in_phase = 0.0
                on_phase = not on_phase
            if step <= 0:
                break  # safety valve: never spin on a zero-length step


def draw_arrowhead(
    draw: "ImageDraw.ImageDraw", from_pt: tuple[float, float], to_pt: tuple[float, float],
    size: float, color: tuple[int, int, int],
) -> None:
    """A small filled triangle at ``to_pt``, pointing away from ``from_pt``
    -- our vocabulary's only arrow style (``blockThin``, always filled)."""
    dx, dy = to_pt[0] - from_pt[0], to_pt[1] - from_pt[1]
    dist = math.hypot(dx, dy)
    if dist <= 1e-6:
        return
    ux, uy = dx / dist, dy / dist
    length, half_w = size * 2.5, size * 1.2
    back = (to_pt[0] - ux * length, to_pt[1] - uy * length)
    perp = (-uy, ux)
    left = (back[0] + perp[0] * half_w, back[1] + perp[1] * half_w)
    right = (back[0] - perp[0] * half_w, back[1] - perp[1] * half_w)
    draw.polygon([to_pt, left, right], fill=color)


def label_band_rect(rect: tuple[float, float, float, float], style: dict[str, str]) -> tuple[float, float, float, float]:
    """The (unscaled) rect a cell's label should be drawn into, honoring
    ``verticalLabelPosition`` (``top``/``bottom``: the label sits OUTSIDE
    the shape, above/below it -- used by icon captions, e.g. a service
    icon's name below it) as distinct from ``verticalAlign`` (alignment
    INSIDE whatever rect it's given, already handled by ``draw_label``
    itself). Anything else (unset, or draw.io's other ``verticalLabelPosition``
    values, never emitted by our vocabulary) draws the label inside the
    shape's own rect, unchanged. Our renderer doesn't auto-size text like
    draw.io does (see the module docstring: geometric fidelity, not
    pixel-parity), so the external band is a fixed height proportional to
    fontSize rather than a true auto-grow box.
    """
    vlp = style.get("verticalLabelPosition")
    if vlp not in ("top", "bottom"):
        return rect
    x, y, w, h = rect
    band_h = max(16.0, _safe_float(style.get("fontSize"), 12.0) * 1.8)
    if vlp == "bottom":
        return (x, y + h, w, band_h)
    return (x, y - band_h, w, band_h)


def draw_label(draw: "ImageDraw.ImageDraw", rect: tuple[float, float, float, float],
               raw_value: str, style: dict[str, str], scale: float) -> None:
    """Render ``raw_value`` inside ``rect`` (unscaled x, y, w, h) honoring
    the cell's align/verticalAlign/fontStyle/fontColor/fontSize -- the
    "text" primitive, used both standalone (bare ``text`` shape) and as any
    other shape's label."""
    text = strip_label_html(raw_value)
    if not text.strip():
        return
    size_px = max(1, round(_safe_float(style.get("fontSize"), 12.0) * scale))
    fs = _safe_int(style.get("fontStyle"), 0)
    font = load_font(size_px, bold=bool(fs & 1), italic=bool(fs & 2))
    color = parse_color(style.get("fontColor")) or (0, 0, 0)
    align = style.get("align", "center")
    valign = style.get("verticalAlign", "middle")
    pil_align = align if align in ("left", "center", "right") else "left"

    x, y, w, h = rect
    px0, py0, pw, ph = x * scale, y * scale, w * scale, h * scale
    pad_l = _safe_float(style.get("spacingLeft"), 4.0) * scale
    pad_t = _safe_float(style.get("spacingTop"), 4.0) * scale
    pad_r = _safe_float(style.get("spacingRight"), 4.0) * scale
    pad_b = _safe_float(style.get("spacingBottom"), 4.0) * scale

    bbox = draw.multiline_textbbox((0, 0), text, font=font, align=pil_align, spacing=4)
    bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]

    if align == "left":
        tx = px0 + pad_l
    elif align == "right":
        tx = px0 + pw - pad_r - bw
    else:
        tx = px0 + (pw - bw) / 2.0

    if valign == "top":
        ty = py0 + pad_t
    elif valign == "bottom":
        ty = py0 + ph - pad_b - bh
    else:
        ty = py0 + (ph - bh) / 2.0

    draw.multiline_text((tx - bbox[0], ty - bbox[1]), text, font=font, fill=color, align=pil_align, spacing=4)


def draw_edge_label(draw: "ImageDraw.ImageDraw", path: list[tuple[float, float]],
                     raw_value: str, style: dict[str, str], scale: float) -> None:
    """An edge's own ``value`` (e.g. "FatturaPA"), centered at its path's
    midpoint -- distinct from a protocol/interface PILL, which is a
    separate child vertex cell resolved and drawn on its own."""
    text = strip_label_html(raw_value)
    if not text.strip():
        return
    size_px = max(1, round(_safe_float(style.get("fontSize"), 10.0) * scale))
    fs = _safe_int(style.get("fontStyle"), 0)
    font = load_font(size_px, bold=bool(fs & 1), italic=bool(fs & 2))
    bbox = draw.multiline_textbbox((0, 0), text, font=font, align="center")
    bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    mx, my = point_at_fraction(path, 0.0)
    mx, my = mx * scale, my * scale
    pad = 2.0 * scale
    bg = parse_color(style.get("labelBackgroundColor"))
    if bg is not None:
        draw.rectangle([mx - bw / 2 - pad, my - bh / 2 - pad, mx + bw / 2 + pad, my + bh / 2 + pad], fill=bg)
    color = parse_color(style.get("fontColor")) or (0, 0, 0)
    draw.multiline_text((mx - bw / 2 - bbox[0], my - bh / 2 - bbox[1]), text, font=font, fill=color, align="center")


def draw_cylinder(
    draw: "ImageDraw.ImageDraw", box_px: tuple[float, float, float, float],
    fill: tuple[int, int, int] | None, stroke: tuple[int, int, int] | None,
    sw: int, cap_h_px: float,
) -> None:
    """A simple database-cylinder glyph for mxgraph's ``cylinder3``/generic
    ``cylinder`` basic shapes (our ``db`` molecule): a rectangular body
    capped by two ellipses, giving the classic 3D-cylinder silhouette. Not
    a faithful mxgraph ``cylinder3`` reproduction (no perspective-skew
    tuning of the cap) -- geometric fidelity only, per the module
    docstring; this is a fidelity nicety, kept deliberately simple.
    """
    x0, y0, x1, y1 = box_px
    cap_h = max(4.0, min(cap_h_px, (y1 - y0) / 2.0))
    half_cap = cap_h / 2.0

    # Body: the cylinder's straight walls, spanning between the two caps'
    # vertical centers (the caps themselves paint over its top/bottom ends).
    draw.rectangle([x0, y0 + half_cap, x1, y1 - half_cap], fill=fill)
    if stroke is not None:
        draw.line([(x0, y0 + half_cap), (x0, y1 - half_cap)], fill=stroke, width=sw)
        draw.line([(x1, y0 + half_cap), (x1, y1 - half_cap)], fill=stroke, width=sw)

    # Bottom cap, then the top cap drawn LAST so its full outline (the
    # "lid" arc that visually distinguishes this from a plain rect) is
    # crisp on top rather than partially covered by the body fill.
    draw.ellipse([x0, y1 - cap_h, x1, y1], fill=fill, outline=stroke, width=sw)
    draw.ellipse([x0, y0, x1, y0 + cap_h], fill=fill, outline=stroke, width=sw)


def draw_placeholder(canvas: "Image.Image", rect_px: tuple[float, float, float, float]) -> None:
    """A grey filled circle standing in for an icon whose data-URI has no
    atlas entry -- the render still succeeds (exit 0), just visibly
    incomplete for that one cell (a WARNING is printed by the caller)."""
    x0, y0, x1, y1 = rect_px
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    d = min(x1 - x0, y1 - y0) * 0.7
    ImageDraw.Draw(canvas).ellipse([cx - d / 2, cy - d / 2, cx + d / 2, cy + d / 2], fill=PLACEHOLDER_RGB)


_ICON_EDGE_INSET = 2.0  # mxgraph's own small built-in image-to-shape-edge
# margin. Deliberately NOT style.get("spacingLeft"/"spacingRight"/"spacingTop"
# /"spacingBottom") -- those pad the LABEL TEXT (already honored by
# draw_label itself) and, in a molecule like the backend box, are
# DELIBERATELY large (spacingLeft=44) so the text clears a left-aligned
# icon -- reusing them here for the icon's OWN inset would shove the icon
# by that same large amount and make it overlap the (now-also-shifted)
# text instead of sitting next to it.


def icon_box_rect(rect: tuple[float, float, float, float], style: dict[str, str]) -> tuple[float, float, float, float]:
    """The (unscaled) box a cell's icon should be fit into.

    Defaults to the WHOLE cell rect -- the plain ``shape=image`` case, and
    any other cell whose style carries an ``image=`` with no positioning
    hints (e.g. the resolved ``sap-btp-chip`` text cell) -- narrowed by
    ``imageWidth``/``imageHeight``/``imageAlign``/``imageVerticalAlign``
    when present: a smaller, positioned icon INSIDE a larger shape, e.g. a
    ``shape=label`` backend box's left-aligned service icon, or a
    capability chip's top-centered one. Mirrors mxgraph's own image-label
    layout keys closely enough for our vocabulary; not a general mxgraph
    label-layout engine.
    """
    x, y, w, h = rect
    box_w = _safe_float(style.get("imageWidth"), w)
    box_h = _safe_float(style.get("imageHeight"), h)
    box_w = min(box_w, w) if box_w > 0 else w
    box_h = min(box_h, h) if box_h > 0 else h

    h_align = style.get("imageAlign", "center")
    if h_align == "left":
        bx = x + _ICON_EDGE_INSET
    elif h_align == "right":
        bx = x + w - box_w - _ICON_EDGE_INSET
    else:
        bx = x + (w - box_w) / 2.0

    v_align = style.get("imageVerticalAlign", "middle")
    if v_align == "top":
        by = y + _ICON_EDGE_INSET
    elif v_align == "bottom":
        by = y + h - box_h - _ICON_EDGE_INSET
    else:
        by = y + (h - box_h) / 2.0

    return (bx, by, box_w, box_h)


def draw_image_cell(canvas: "Image.Image", cell: Cell, style: dict[str, str],
                     rect: tuple[float, float, float, float], scale: float, atlas: dict) -> None:
    """Resolve ``cell``'s ``image=`` data-URI to an atlas PNG by sha1 (the
    load-bearing contract -- see the module docstring) and paste it into
    ``rect`` (already narrowed to the icon's own box by ``icon_box_rect``
    when the caller isn't a bare ``shape=image`` cell), preserving aspect
    ratio and honoring ``opacity=N`` (watermarks). A lookup miss draws a
    grey placeholder circle sized to ``rect`` and prints a WARNING; it
    never raises."""
    uri = extract_image_value(cell.style)
    icon = None
    digest = None
    if uri:
        digest = sha1_of(uri)
        rel = atlas.get("by_sha1", {}).get(digest)
        if rel is None and cell.value:
            rel = atlas.get("by_name", {}).get(cell.value)  # documented fallback path
        if rel:
            icon = load_icon(rel)

    x, y, w, h = rect
    x0, y0 = x * scale, y * scale
    box_w, box_h = max(1, round(w * scale)), max(1, round(h * scale))

    if icon is None:
        print(
            f"WARNING: no icon-atlas entry for image cell {cell.id!r}"
            f"{f' (sha1={digest})' if digest else ' (no image= value found)'}; using grey placeholder",
            file=sys.stderr,
        )
        draw_placeholder(canvas, (x0, y0, x0 + box_w, y0 + box_h))
        return

    fitted = fit_icon(icon, box_w, box_h)
    opacity = style.get("opacity")
    if opacity is not None:
        fitted = apply_opacity(fitted, _safe_float(opacity, 100.0))
    px = int(round(x0 + (box_w - fitted.width) / 2.0))
    py = int(round(y0 + (box_h - fitted.height) / 2.0))
    canvas.paste(fitted, (px, py), fitted)


def draw_vertex(canvas: "Image.Image", draw: "ImageDraw.ImageDraw", cell: Cell,
                 rect: tuple[float, float, float, float], scale: float, atlas: dict) -> None:
    style = parse_style(cell.style)
    x, y, w, h = rect
    shape = style.get("shape")

    if shape == "image":
        draw_image_cell(canvas, cell, style, icon_box_rect(rect, style), scale, atlas)
        if cell.value:
            draw_label(draw, label_band_rect(rect, style), cell.value, style, scale)
        return

    x0, y0 = x * scale, y * scale
    x1, y1 = (x + w) * scale, (y + h) * scale
    fill = parse_color(style.get("fillColor"))
    stroke = parse_color(style.get("strokeColor"))
    sw = max(1, round(_safe_float(style.get("strokeWidth"), 1.0) * scale))

    if shape in ("cylinder3", "cylinder"):
        cap_h_px = _safe_float(style.get("size"), 15.0) * scale
        draw_cylinder(draw, (x0, y0, x1, y1), fill, stroke, sw, cap_h_px)
    elif style.get("ellipse") == "1":
        draw.ellipse([x0, y0, x1, y1], fill=fill, outline=stroke, width=sw)
    elif style.get("text") == "1":
        pass  # label-only shape: no background/border by convention
    else:
        # "label" is a recognized rect molecule that also carries an
        # embedded icon (a backend box / capability chip -- see
        # icon_box_rect); anything else with a shape= we don't specifically
        # handle still degrades to a plain rect (never a crash), but now
        # says so, symmetric with the icon-atlas-miss WARNING below.
        if shape not in (None, "label"):
            print(f"WARNING: unhandled shape={shape!r}; drawing plain rect", file=sys.stderr)
        radius = corner_radius(style, w, h) * scale
        if radius > 0:
            draw.rounded_rectangle([x0, y0, x1, y1], radius=int(round(radius)), fill=fill, outline=stroke, width=sw)
        else:
            draw.rectangle([x0, y0, x1, y1], fill=fill, outline=stroke, width=sw)

    # Any non-image shape can STILL carry an embedded icon (e.g. a
    # shape=label backend box's service icon, a capability chip's icon
    # grid entry, or a resolved sap-btp-chip text cell's logo) -- composite
    # it in addition to the rect/label above rather than silently dropping
    # it (previously only a bare shape=image cell ever got an icon at all).
    if extract_image_value(cell.style):
        draw_image_cell(canvas, cell, style, icon_box_rect(rect, style), scale, atlas)

    if cell.value:
        draw_label(draw, rect, cell.value, style, scale)


def draw_edge(draw: "ImageDraw.ImageDraw", cell: Cell, path: list[tuple[float, float]], scale: float) -> None:
    if len(path) < 2:
        return
    style = parse_style(cell.style)
    stroke = parse_color(style.get("strokeColor")) or (0, 0, 0)
    sw = max(1, round(_safe_float(style.get("strokeWidth"), 1.0) * scale))
    pts_px = [(px * scale, py * scale) for px, py in path]

    draw_polyline(draw, pts_px, stroke, sw, dash_spec(style, scale))

    end_arrow = style.get("endArrow")
    if end_arrow and end_arrow != "none":
        size_px = max(2.0, _safe_float(style.get("endSize"), 4.0) * scale)
        draw_arrowhead(draw, pts_px[-2], pts_px[-1], size_px, stroke)
    start_arrow = style.get("startArrow")
    if start_arrow and start_arrow != "none":
        size_px = max(2.0, _safe_float(style.get("startSize"), 4.0) * scale)
        draw_arrowhead(draw, pts_px[1], pts_px[0], size_px, stroke)

    if cell.value:
        draw_edge_label(draw, path, cell.value, style, scale)


# ─────────────────────────────────────────────────────────────────────────
# Top-level render
# ─────────────────────────────────────────────────────────────────────────
def render_drawio(path: Path, scale: float = 1.0) -> "Image.Image":
    pages = decode_diagram_pages(path)
    if not pages:
        raise RenderError(f"no renderable <diagram> page found in {path}")
    if len(pages) > 1:
        print(f"WARNING: {path} has {len(pages)} pages; rendering only the first", file=sys.stderr)
    _name, page_root = pages[0]

    model = _model_of(page_root)
    if model is None:
        raise RenderError(f"no <mxGraphModel> found in the first page of {path}")
    root_container = model.find("root")
    if root_container is None:
        raise RenderError(f"<mxGraphModel> has no <root> in {path}")

    page_w = _safe_float(model.get("pageWidth"), DEFAULT_PAGE_W)
    page_h = _safe_float(model.get("pageHeight"), DEFAULT_PAGE_H)

    cells, order = parse_cells(root_container)
    abs_rects = resolve_all_rects(cells)
    edge_paths = resolve_all_edges(cells, abs_rects)
    edge_child_rects = resolve_edge_child_rects(cells, edge_paths)
    all_rects = {**abs_rects, **edge_child_rects}

    atlas = load_atlas_index()

    W, H = max(1, round(page_w * scale)), max(1, round(page_h * scale))
    canvas = Image.new("RGB", (W, H), BACKGROUND_RGB)
    draw = ImageDraw.Draw(canvas)

    for cid in order:
        cell = cells[cid]
        if cell.edge:
            draw_edge(draw, cell, edge_paths.get(cid, []), scale)
        elif cell.vertex:
            topleft = all_rects.get(cid)
            if topleft is None:
                continue
            rect = (topleft[0], topleft[1], cell.w, cell.h)
            draw_vertex(canvas, draw, cell, rect, scale, atlas)

    return canvas


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Render OUR emitted .drawio vocabulary to a PNG, pure-Python (no draw.io/Electron)."
    )
    ap.add_argument("input", help="Path to a .drawio file.")
    ap.add_argument("--out", help="Output PNG path (default: input with a .png extension).")
    ap.add_argument("--scale", type=float, default=1.0, help="Render scale factor (default 1.0).")
    args = ap.parse_args(argv)

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"ERROR: file not found: {in_path}", file=sys.stderr)
        return 2

    out_path = Path(args.out) if args.out else in_path.with_suffix(".png")

    try:
        canvas = render_drawio(in_path, scale=args.scale)
    except RenderError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - never traceback for a preview render
        print(f"ERROR: could not render {in_path}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, format="PNG", optimize=True)
    print(f"OK: rendered {out_path} ({canvas.width}x{canvas.height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
