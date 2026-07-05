#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""build-icon-atlas.py — pre-rasterize SAP icons + brand chips into a PNG atlas.

_pure_render.py (Task 10) draws diagrams without draw.io/Electron, so it
cannot rely on draw.io's own SVG-in-style renderer. This script pre-rasterizes
every icon-bearing entry of assets/shape-index.json (``services`` +
``genericIcons``) plus every entry of the brand pack (assets/brand-pack/,
assets/brand-pack.local/ when present) into fixed-size PNGs the pure renderer
can just paste — no SVG parsing, no rasterization, at render time.

────────────────────────────────────────────────────────────────────────────
SHA1 CONTRACT — read this before touching Task 10 (_pure_render.py)
────────────────────────────────────────────────────────────────────────────
index.json maps BOTH the source "name" AND ``sha1(<data-URI string>)`` to a
PNG file. The sha1 key is PRIMARY (exact, content-addressed); the name key is
a fallback lookup path. For the sha1 to ever match at render time, this
script must hash the EXACT string generate-drawio.py writes into the emitted
``.drawio`` — not the raw shape-index.json field.

generate-drawio.py never embeds a drawioStyle verbatim: every style passes
through its ``_safe_img()`` helper first —

    style.replace(";base64,", ",")

— because draw.io style strings are ';'-delimited, so a literal ``;base64,``
inside an image data-URI breaks style parsing (see ``_safe_img``'s own
docstring in generate-drawio.py: this is why generic icons like the
user/database glyphs used to render blank). The resulting ``image=<value>;``
value is then read back out with ``re.search(r"image=([^;]+)", style)``
(generate-drawio.py's ``_extract_image_uri``).

This script reproduces BOTH steps byte-for-byte (``normalize_style`` /
``extract_image_value`` below) instead of importing generate-drawio.py,
mirroring the choice harvest-brand-assets.py already made: it carries its own
copy of the identical ``.replace(";base64,", ",")`` line (see that module's
docstring) rather than sharing a util import.

Concretely, for a shape-index.json ``services``/``genericIcons`` entry:

    uri  = extract_image_value(entry["drawioStyle"])   # normalizes, then extracts
    sha1 = hashlib.sha1(uri.encode("utf-8")).hexdigest()

assets/brand-pack{,.local}/index.json entries store their ``dataUri`` value
ALREADY in this exact normalized comma form (harvest-brand-assets.py's own
module docstring: "dataUri values are persisted in draw.io's embeddable comma
form ... the standard ';base64' marker is stripped"). So for those:

    uri  = normalize_style(entry["dataUri"])   # no-op in practice; defense in depth
    sha1 = hashlib.sha1(uri.encode("utf-8")).hexdigest()

Any change to this normalization MUST be made in lockstep with
generate-drawio.py's ``_safe_img``/``_extract_image_uri`` AND _pure_render.py
(Task 10), or sha1 lookups silently start missing at render time.
tests/test_icon_atlas.py cross-checks this module's output against the real
generate-drawio.py functions as a regression guard on exactly that drift.

────────────────────────────────────────────────────────────────────────────
Rasterizer resolution order
────────────────────────────────────────────────────────────────────────────
  1. ``resvg`` CLI, if on PATH — deterministic, spec-compliant SVG rendering.
  2. ``cairosvg`` Python module, if importable.
  3. Otherwise: exit with a clear hint (brew install resvg / pip install
     cairosvg).

Every source (SVG or PNG; icon or brand badge) is fit inside a
``--size``x``--size`` (default 96x96) box preserving its aspect ratio, then
centered on a transparent canvas of exactly that size. This is a no-op resize
for the square service/generic icon SVGs (16x16 / 24x24 / 32x32 viewBoxes),
and is the "keep aspect, pad transparent" behavior for non-square brand
badges (the SAP logo chip is 412x204; aws-badge, azure-badge, etc. are all
non-square too).

────────────────────────────────────────────────────────────────────────────
Public / local split
────────────────────────────────────────────────────────────────────────────
assets/icon-atlas/       — committed. Official SAP service/generic icons +
                            assets/brand-pack/ (public brand chips only).
assets/icon-atlas.local/ — gitignored, mirrors assets/brand-pack.local/
                            (aws/azure/cf/rise/... badges under a stricter
                            license). Never generated, and never required,
                            when assets/brand-pack.local/ is absent.

Usage:
    python3 build-icon-atlas.py --date 2026-07-05
    python3 build-icon-atlas.py --date 2026-07-05 --only "SAP HANA Cloud" --only aws-badge
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

# Prefer defusedxml for parsing SVG bytes (guards XXE / billion-laughs);
# fall back to the stdlib parser when it isn't installed. Mirrors
# scripts/_drawio_io.py's identical posture (which this script does not
# import from, since it parses <mxfile>/<mxlibrary> shapes, not bare SVG
# roots).
try:
    from defusedxml.ElementTree import fromstring as _xml_fromstring
except Exception:  # pragma: no cover - defusedxml optional
    from xml.etree.ElementTree import fromstring as _xml_fromstring

SCRIPT_VERSION = "1.0.0"

# Names/keys known to be unrasterizable by either backend. Kept EMPTY: every
# icon in the current corpus (344 services x S/M/L + 246 genericIcons + the
# brand pack) rasterizes cleanly with cairosvg 2.9 / resvg (see
# tests/test_icon_atlas.py — a full build fails loudly on any *unlisted*
# failure). Only add an entry here with a comment naming the specific SVG
# feature that broke both backends. --only bypasses this list: a targeted,
# explicit retry is always allowed to try again.
SKIP_LIST: frozenset[str] = frozenset()

_IMAGE_RE = re.compile(r"image=([^;]+)")


# ─────────────────────────────────────────────────────────────────────────
# sha1 contract (see module docstring above)
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
# Sources
# ─────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class IconSource:
    name: str                  # by_name key: shape-index "name", or brand-pack dict key
    value: str                 # drawioStyle string, or a bare (already-normalized) data-URI
    is_style: bool             # True: `value` is a full style string; False: `value` IS the URI
    size: str | None = None    # "S"/"M"/"L" when known — used only to break by_name ties


def resolved_uri(src: IconSource) -> str | None:
    if src.is_style:
        return extract_image_value(src.value)
    return normalize_style(src.value)


def iter_shape_index_sources(shape_index_path: Path) -> list[IconSource]:
    """services + genericIcons entries that carry an embedded image.

    Skips text-only chips (no ``image=data:`` in their style) — those are not
    icons per the Task 3 spec ("some entries may have no image ... skip
    those, they're not icons").
    """
    data = json.loads(shape_index_path.read_text(encoding="utf-8"))
    out: list[IconSource] = []
    for bucket in ("services", "genericIcons"):
        for entry in data.get(bucket, []):
            name = entry.get("name")
            style = entry.get("drawioStyle")
            if not name or not style or "image=data:" not in style:
                continue
            out.append(IconSource(name=name, value=style, is_style=True, size=entry.get("size")))
    return out


def iter_brand_pack_sources(index_path: Path) -> list[IconSource]:
    """One IconSource per assets/brand-pack{,.local}/index.json entry.

    Returns [] (never raises) when index_path is absent — brand-pack.local is
    gitignored and must not be required for a build to succeed.
    """
    if not index_path.exists():
        return []
    data = json.loads(index_path.read_text(encoding="utf-8"))
    out: list[IconSource] = []
    for key, entry in data.items():
        uri = entry.get("dataUri")
        if uri:
            out.append(IconSource(name=key, value=uri, is_style=False))
    return out


# ─────────────────────────────────────────────────────────────────────────
# data-URI decoding
#
# Defensive about payload encoding (base64 with/without the ";base64"
# marker, URL-encoded, or raw unescaped SVG) — mirrors the string-level
# tolerance _drawio_io.py / harvest-brand-assets.py already document for
# these data-URIs; this is the first script that needs the actual decoded
# image bytes rather than just the string. In the current corpus every
# payload is base64 (verified: 590 shape-index + 8 brand-pack entries, 0
# decode failures), so the base64 branch is the one that actually fires.
# ─────────────────────────────────────────────────────────────────────────
def decode_data_uri(value: str) -> tuple[str, bytes]:
    """Return (mime, raw_bytes) for a ``data:<mime>[;base64],<payload>`` (or
    the marker-stripped ``data:<mime>,<payload>`` comma form used throughout
    this codebase — see module docstring) data-URI value."""
    if not value.startswith("data:"):
        raise ValueError(f"not a data URI: {value[:40]!r}")
    header, _, payload = value[len("data:"):].partition(",")
    mime = (header.split(";")[0] or "application/octet-stream").strip()
    try:
        raw = base64.b64decode(payload, validate=True)
        if raw:
            return mime, raw
    except (binascii.Error, ValueError):
        pass
    decoded_text = urllib.parse.unquote(payload)
    if decoded_text.lstrip().startswith("<"):
        return mime, decoded_text.encode("utf-8")
    if payload.lstrip().startswith("<"):
        return mime, payload.encode("utf-8")
    raise ValueError(f"unrecognized data-URI payload encoding (mime={mime})")


def intrinsic_svg_size(svg_bytes: bytes) -> tuple[float, float]:
    """(width, height) from the SVG root's ``viewBox`` attribute, else its
    ``width``/``height`` attributes, else a 1:1 square fallback.

    Every icon in the current corpus declares a viewBox (verified); the
    square fallback only guards a future/foreign SVG that declares neither.
    """
    try:
        root = _xml_fromstring(svg_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"could not parse SVG: {exc}") from exc
    vb = root.attrib.get("viewBox")
    if vb:
        parts = vb.replace(",", " ").split()
        if len(parts) == 4:
            w, h = float(parts[2]), float(parts[3])
            if w > 0 and h > 0:
                return w, h
    w_attr, h_attr = root.attrib.get("width"), root.attrib.get("height")
    if w_attr and h_attr:
        try:
            w = float(re.sub(r"[a-zA-Z%]+$", "", w_attr))
            h = float(re.sub(r"[a-zA-Z%]+$", "", h_attr))
            if w > 0 and h > 0:
                return w, h
        except ValueError:
            pass
    return 1.0, 1.0


def fit_dimensions(intrinsic_w: float, intrinsic_h: float, size: int) -> tuple[int, int]:
    """Largest (w, h) that fits inside size x size while preserving aspect."""
    scale = min(size / intrinsic_w, size / intrinsic_h)
    return max(1, round(intrinsic_w * scale)), max(1, round(intrinsic_h * scale))


def fit_and_pad(img: Image.Image, size: int) -> Image.Image:
    """Scale ``img`` to fit a size x size box preserving aspect ratio, then
    center it on a transparent size x size RGBA canvas.

    A no-op resize for the already-square service/generic icon renders; pads
    non-square brand badges instead of distorting them (see module
    docstring).
    """
    img = img.convert("RGBA")
    scale = min(size / img.width, size / img.height)
    new_w = max(1, round(img.width * scale))
    new_h = max(1, round(img.height * scale))
    if (new_w, new_h) != img.size:
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(img, ((size - new_w) // 2, (size - new_h) // 2), img)
    return canvas


# ─────────────────────────────────────────────────────────────────────────
# Rasterizer backends
# ─────────────────────────────────────────────────────────────────────────
class Rasterizer:
    """Wraps whichever SVG->PNG backend ``detect_rasterizer()`` found."""

    def __init__(self, kind: str):
        self.kind = kind  # "resvg" | "cairosvg"

    def rasterize(self, svg_bytes: bytes, width: int, height: int) -> bytes:
        """Render ``svg_bytes`` to PNG bytes at approximately (width,
        height) — callers must not assume the backend honored the exact
        pixel size (resvg's --width/--height are a recommendation, not a
        guarantee: https://github.com/linebender/resvg/issues/779) and must
        re-fit the result themselves (see ``render_png`` -> ``fit_and_pad``).
        """
        if self.kind == "resvg":
            return self._resvg(svg_bytes, width, height)
        return self._cairosvg(svg_bytes, width, height)

    @staticmethod
    def _resvg(svg_bytes: bytes, width: int, height: int) -> bytes:
        with tempfile.TemporaryDirectory() as td:
            in_path, out_path = Path(td) / "in.svg", Path(td) / "out.png"
            in_path.write_bytes(svg_bytes)
            subprocess.run(
                ["resvg", "--width", str(width), "--height", str(height),
                 str(in_path), str(out_path)],
                check=True, capture_output=True,
            )
            return out_path.read_bytes()

    @staticmethod
    def _cairosvg(svg_bytes: bytes, width: int, height: int) -> bytes:
        import cairosvg
        return cairosvg.svg2png(bytestring=svg_bytes, output_width=width, output_height=height)


def detect_rasterizer() -> Rasterizer:
    if shutil.which("resvg"):
        return Rasterizer("resvg")
    try:
        import cairosvg  # noqa: F401
    except ImportError:
        pass
    else:
        return Rasterizer("cairosvg")
    print(
        "ERROR: no SVG rasterizer available.\n"
        "  macOS:   brew install resvg\n"
        "  any OS:  pip install cairosvg\n",
        file=sys.stderr,
    )
    raise SystemExit(2)


def render_png(mime: str, raw: bytes, size: int, rasterizer: Rasterizer) -> bytes:
    """Decode + fit-and-pad one image source into a final size x size PNG."""
    if mime == "image/svg+xml":
        iw, ih = intrinsic_svg_size(raw)
        w, h = fit_dimensions(iw, ih, size)
        img = Image.open(io.BytesIO(rasterizer.rasterize(raw, w, h)))
    elif mime in ("image/png", "image/jpeg", "image/jpg"):
        img = Image.open(io.BytesIO(raw))
    else:
        raise ValueError(f"unsupported image mime: {mime}")
    canvas = fit_and_pad(img, size)
    buf = io.BytesIO()
    # optimize=True + a fresh Image.new() canvas (no inherited .info metadata)
    # keeps output byte-stable across reruns: no tEXt/tIME chunks, and PIL's
    # PNG encoder is a deterministic function of pixels + these save kwargs.
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────
# Build
# ─────────────────────────────────────────────────────────────────────────
def _prefer_size(existing: str | None, new: str | None) -> bool:
    """Tie-break for by_name collisions.

    services/genericIcons repeat the same "name" across S/M/L size variants,
    each with a genuinely different embedded image. Prefer 'M', mirroring
    ShapeIndex's own canonical-lookup preference in generate-drawio.py (its
    _by_name / _generic dicts apply the identical rule), so the atlas's
    name-fallback path resolves to the SAME variant the emitter's
    resolve()/resolve_generic() would pick for a plain name query.
    """
    if existing == "M":
        return False
    return new == "M"


def process_sources(
    sources: list[IconSource],
    size: int,
    rasterizer: Rasterizer,
    icons_dir: Path,
    skip_list: frozenset[str] = frozenset(),
) -> tuple[dict[str, str], dict[str, str], list[tuple[str, str]]]:
    """Rasterize ``sources`` into ``icons_dir``; return (by_name, by_sha1,
    failures) for just this batch (callers merge into any existing index)."""
    by_name: dict[str, str] = {}
    by_name_size: dict[str, str | None] = {}
    by_sha1: dict[str, str] = {}
    failures: list[tuple[str, str]] = []
    raster_cache: set[str] = set()  # sha1 digests already written this run

    for src in sources:
        if src.name in skip_list:
            continue
        uri = resolved_uri(src)
        if not uri:
            failures.append((src.name, "no image= value resolved from style"))
            continue
        digest = sha1_of(uri)
        filename = f"{digest}.png"
        rel = f"icons/{filename}"
        if digest not in raster_cache:
            try:
                mime, raw = decode_data_uri(uri)
                png_bytes = render_png(mime, raw, size, rasterizer)
            except Exception as exc:  # noqa: BLE001 - convert to a per-entry failure, keep going
                failures.append((src.name, f"{type(exc).__name__}: {exc}"))
                continue
            (icons_dir / filename).write_bytes(png_bytes)
            raster_cache.add(digest)
        by_sha1[digest] = rel
        if src.name not in by_name or _prefer_size(by_name_size.get(src.name), src.size):
            by_name[src.name] = rel
            by_name_size[src.name] = src.size

    return by_name, by_sha1, failures


def write_atlas(
    atlas_dir: Path,
    sources: list[IconSource],
    size: int,
    rasterizer: Rasterizer,
    date: str,
    only: set[str] | None,
) -> tuple[dict, list[tuple[str, str]]]:
    """Build (or incrementally update) one atlas directory's index.json."""
    icons_dir = atlas_dir / "icons"
    index_path = atlas_dir / "index.json"
    incremental = only is not None

    if incremental and index_path.exists():
        existing = json.loads(index_path.read_text(encoding="utf-8"))
        by_name = dict(existing.get("by_name", {}))
        by_sha1 = dict(existing.get("by_sha1", {}))
    else:
        by_name, by_sha1 = {}, {}
        if not incremental and icons_dir.exists():
            shutil.rmtree(icons_dir)  # full rebuild: drop stale/orphaned PNGs
    icons_dir.mkdir(parents=True, exist_ok=True)

    if incremental:
        sources = [s for s in sources if s.name in only]
        skip_list: frozenset[str] = frozenset()  # explicit --only bypasses the skip-list
    else:
        skip_list = SKIP_LIST

    new_by_name, new_by_sha1, failures = process_sources(sources, size, rasterizer, icons_dir, skip_list)
    by_name.update(new_by_name)
    by_sha1.update(new_by_sha1)

    index = {
        "by_name": by_name,
        "by_sha1": by_sha1,
        "meta": {
            "count": len(by_name),
            "generated": date,
            "rasterizer": rasterizer.kind,
            "size": size,
            "scriptVersion": SCRIPT_VERSION,
        },
    }
    index_path.write_text(
        json.dumps(index, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return index, failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Pre-rasterize SAP service/generic icons + brand-pack chips into a fixed-size PNG atlas.")
    ap.add_argument("--shape-index", type=Path, default=Path("assets/shape-index.json"))
    ap.add_argument("--brand-pack", type=Path, default=Path("assets/brand-pack"))
    ap.add_argument("--brand-pack-local", type=Path, default=Path("assets/brand-pack.local"))
    ap.add_argument("--out", type=Path, default=Path("assets/icon-atlas"))
    ap.add_argument("--out-local", type=Path, default=Path("assets/icon-atlas.local"))
    ap.add_argument("--size", type=int, default=96)
    ap.add_argument("--date", required=True,
                    help="ISO date (YYYY-MM-DD) stamped into meta.generated -- reproducible, "
                         "not datetime.now(), so reruns are byte-stable.")
    ap.add_argument("--only", action="append", default=None, metavar="NAME",
                     help="Rasterize only entries whose name/key exactly matches this "
                          "(repeatable). Merges into an existing index.json for incremental "
                          "runs; omit for a full rebuild (which also prunes stale PNGs).")
    args = ap.parse_args(argv)

    if not args.shape_index.exists():
        print(f"ERROR: shape index not found: {args.shape_index}", file=sys.stderr)
        return 2

    rasterizer = detect_rasterizer()
    only = set(args.only) if args.only else None

    shape_sources = iter_shape_index_sources(args.shape_index)
    public_brand_sources = iter_brand_pack_sources(args.brand_pack / "index.json")
    local_index_path = args.brand_pack_local / "index.json"
    local_brand_sources = iter_brand_pack_sources(local_index_path)

    if only:
        known = {s.name for s in shape_sources + public_brand_sources + local_brand_sources}
        for n in only:
            if n not in known:
                print(f"WARNING: --only {n!r} matched no known icon/brand-pack entry", file=sys.stderr)

    public_index, failures = write_atlas(
        args.out, shape_sources + public_brand_sources, args.size, rasterizer, args.date, only)

    if local_index_path.exists():
        _local_index, local_failures = write_atlas(
            args.out_local, local_brand_sources, args.size, rasterizer, args.date, only)
        failures = failures + local_failures
    else:
        print(f"INFO: {local_index_path} not present -- skipping the local atlas (never required).")

    if failures:
        print(f"WARNING: {len(failures)} entrie(s) failed to rasterize (not in the atlas):",
              file=sys.stderr)
        for name, reason in failures:
            print(f"  {name}: {reason}", file=sys.stderr)
        if only is None:
            return 1  # a full build must have zero unexplained failures

    print(f"OK: {public_index['meta']['count']} public entries -> {args.out}/index.json "
          f"(rasterizer={rasterizer.kind}, size={args.size})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
