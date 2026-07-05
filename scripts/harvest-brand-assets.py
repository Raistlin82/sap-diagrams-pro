#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""
harvest-brand-assets.py — collect logo / badge data-URIs into a brand pack.

Reads assets/brand-pack.manifest.json to learn WHICH keys to harvest and
where they come from. The manifest — not this script — decides
confidentiality: each entry's "public" flag routes it to --out-public or
--out-local. Two source kinds:

  source: official  — the entry's "official_ref" ("<file>:<title>") is
                       looked up in an SAP btp-solution-diagrams shape
                       library (--official-repo), an mxlibrary XML file
                       whose body is a JSON array of {title, xml, w, h}.
  source: exemplar   — the entry's "match" (value_regex + optional mime)
                       is matched against every image-bearing cell found
                       across the positional .drawio source files.

Output: {key: {dataUri, source, from, license_note}} written as
"index.json" under --out-public and/or --out-local. Harvesting is
best-effort: an asset that can't be resolved prints a WARNING and is
skipped rather than failing the run.

Usage:
    python3 harvest-brand-assets.py \\
        --manifest assets/brand-pack.manifest.json \\
        --out-public assets/brand-pack --out-local assets/brand-pack.local \\
        --official-repo ~/tools/btp-solution-diagrams \\
        exemplar1.drawio exemplar2.drawio ...

This script is standalone (no imports from the other scripts/*.py files)
so it can be dropped in or run in isolation.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

# Prefer defusedxml for parsing untrusted .drawio/.xml input (guards XXE /
# billion-laughs); fall back to the stdlib parser when it isn't installed.
# Mirrors scripts/validate-drawio.py's posture — duplicated here (not
# imported) so this script stays standalone.
try:
    from defusedxml.ElementTree import fromstring as _xml_fromstring
    from defusedxml.ElementTree import parse as _xml_parse
except Exception:  # pragma: no cover - defusedxml optional
    from xml.etree.ElementTree import fromstring as _xml_fromstring
    from xml.etree.ElementTree import parse as _xml_parse

OFFICIAL_LICENSE_NOTE = "SAP btp-solution-diagrams, Apache-2.0"
EXEMPLAR_LICENSE_NOTE = "trademark — local use only, do not redistribute"
LIB_SUBPATH = Path("assets/shape-libraries-and-editable-presets/draw.io")


# ── draw.io page decompression ───────────────────────────────────────────
def _decode_diagram_text(text: str) -> str | None:
    """Turn a compressed <diagram> text payload back into raw XML.

    draw.io stores each page either inline (child <mxGraphModel> element —
    handled by the caller before this is reached) or compressed: base64 →
    raw DEFLATE (zlib, -15 window bits ⇒ no header/checksum) → URL-decoded
    UTF-8 text. Content that already starts with '<' is passed through.
    """
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith("<"):
        return stripped
    try:
        raw = base64.b64decode(stripped)
        inflated = zlib.decompress(raw, -15)
        return urllib.parse.unquote(inflated.decode("utf-8"))
    except Exception as exc:
        print(f"WARNING: could not decompress a <diagram> page: {exc}", file=sys.stderr)
        return None


def _diagram_pages(root: ET.Element) -> list[ET.Element]:
    """Return one parsed element per diagram page, decompressing as needed."""
    diagrams = root.findall("diagram") if root.tag == "mxfile" else [root]
    pages: list[ET.Element] = []
    for d in diagrams:
        if list(d):
            # Already-parsed XML tree (uncompressed page): mxCells are
            # reachable directly via .iter() below.
            pages.append(d)
            continue
        xml_text = _decode_diagram_text(d.text or "")
        if not xml_text:
            continue
        try:
            pages.append(_xml_fromstring(xml_text))
        except ET.ParseError as exc:
            print(f"WARNING: could not parse a decompressed diagram page: {exc}", file=sys.stderr)
    return pages


def _load_pages(path: Path) -> list[ET.Element]:
    tree = _xml_parse(path)
    return _diagram_pages(tree.getroot())


# ── style parsing ────────────────────────────────────────────────────────
def _extract_image_data_uri(style: str) -> str | None:
    """Pull the image=data:... value out of a cell's style string.

    draw.io style strings are ';'-delimited key=value pairs, but a raster
    image embedded the standard way (`data:image/png;base64,<payload>`)
    contains a literal ';' before "base64" that is NOT a delimiter — naive
    splitting truncates the payload right there. SAP's own official
    libraries sidestep this by omitting the ";base64" marker entirely
    (`data:image/svg+xml,<payload>` — see generate-drawio.py's _safe_img
    for the same fix applied on the emit side). Normalize the former to
    the latter before splitting so both forms parse intact, and so the
    dataUri we persist is always safe to re-embed in a new style string.
    """
    if not style or "image=data:" not in style:
        return None
    normalized = style.replace(";base64,", ",")
    for chunk in normalized.split(";"):
        if "=" not in chunk:
            continue
        key, _, value = chunk.partition("=")
        if key.strip() == "image" and value.startswith("data:"):
            return value
    return None


def _data_uri_mime(data_uri: str) -> str:
    m = re.match(r"^data:([^;,]*)", data_uri)
    return m.group(1) if m else ""


def _nearest_value(cell: ET.Element, cells_by_id: dict[str, ET.Element]) -> str:
    """The cell's own value, else its immediate parent's value (or "")."""
    value = (cell.get("value") or "").strip()
    if value:
        return value
    parent = cells_by_id.get(cell.get("parent") or "")
    if parent is not None:
        return (parent.get("value") or "").strip()
    return ""


# ── exemplar matching ────────────────────────────────────────────────────
def _collect_exemplar_candidates(sources: list[Path]) -> list[tuple[str, str, str]]:
    """Return (source_filename, candidate_value, data_uri) for every
    image-bearing cell across all source .drawio files, in file-then-
    document order (so 'largest payload wins' ties break deterministically
    on first occurrence).
    """
    candidates: list[tuple[str, str, str]] = []
    for src in sources:
        try:
            pages = _load_pages(src)
        except Exception as exc:
            print(f"WARNING: could not read {src}: {exc}", file=sys.stderr)
            continue
        for page in pages:
            cells = list(page.iter("mxCell"))
            cells_by_id = {c.get("id"): c for c in cells if c.get("id")}
            for cell in cells:
                data_uri = _extract_image_data_uri(cell.get("style") or "")
                if not data_uri:
                    continue
                value = _nearest_value(cell, cells_by_id)
                candidates.append((src.name, value, data_uri))
    return candidates


def _best_exemplar_match(
    candidates: list[tuple[str, str, str]], match_spec: dict
) -> tuple[str, str] | None:
    """Return (dataUri, fromFilename) for the largest-payload match, or None."""
    value_regex = match_spec.get("value_regex")
    if not value_regex:
        return None
    value_pat = re.compile(value_regex)
    mime_pat = re.compile(match_spec["mime"]) if match_spec.get("mime") else None

    best: tuple[str, str] | None = None
    best_size = -1
    for source_name, value, data_uri in candidates:
        if not value_pat.search(value):
            continue
        if mime_pat is not None and not mime_pat.fullmatch(_data_uri_mime(data_uri)):
            continue
        size = len(data_uri)
        if size > best_size:
            best_size = size
            best = (data_uri, source_name)
    return best


# ── official-library resolution ──────────────────────────────────────────
def _parse_mxlibrary(path: Path) -> list[dict] | None:
    """Parse an mxlibrary XML file (<mxlibrary>[JSON array]</mxlibrary>)."""
    if not path.exists():
        return None
    try:
        tree = _xml_parse(path)
    except Exception as exc:
        print(f"WARNING: could not parse library {path}: {exc}", file=sys.stderr)
        return None
    root = tree.getroot()
    if root.tag != "mxlibrary":
        print(f"WARNING: {path} is not an <mxlibrary> file (root=<{root.tag}>)", file=sys.stderr)
        return None
    text = (root.text or "").strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"WARNING: could not JSON-decode library body of {path}: {exc}", file=sys.stderr)
        return None


def _resolve_official(
    asset: dict, official_repo: Path | None, lib_cache: dict[str, list[dict] | None]
) -> tuple[str, str] | None:
    """Return (dataUri, fromFilename) for an official_ref, or None (+ WARNING)."""
    key = asset.get("key", "?")
    ref = asset.get("official_ref", "")
    if ":" not in ref:
        print(f"WARNING: malformed official_ref {ref!r} for key {key!r}, skipping", file=sys.stderr)
        return None
    filename, _, title = ref.partition(":")

    if official_repo is None:
        print(
            f"WARNING: no --official-repo given; skipping official asset "
            f"{key!r} ({ref})",
            file=sys.stderr,
        )
        return None

    if filename not in lib_cache:
        lib_cache[filename] = _parse_mxlibrary(Path(official_repo) / LIB_SUBPATH / filename)
    entries = lib_cache[filename]
    if entries is None:
        print(
            f"WARNING: could not read official library {filename!r} for key {key!r}, skipping",
            file=sys.stderr,
        )
        return None

    matches = [e for e in entries if (e.get("title") or "") == title]
    if not matches:
        print(
            f"WARNING: title {title!r} not found in {filename} for key {key!r}, skipping",
            file=sys.stderr,
        )
        return None

    best: str | None = None
    best_size = -1
    for entry in matches:
        try:
            page_root = _xml_fromstring(html.unescape(entry.get("xml") or ""))
        except ET.ParseError as exc:
            print(f"WARNING: could not parse library entry {title!r} in {filename}: {exc}", file=sys.stderr)
            continue
        for cell in page_root.iter("mxCell"):
            data_uri = _extract_image_data_uri(cell.get("style") or "")
            if data_uri and len(data_uri) > best_size:
                best_size = len(data_uri)
                best = data_uri

    if best is None:
        print(
            f"WARNING: no image=data: style found for official_ref {ref!r} (key {key!r}) "
            "— title matched but the entry has no embedded image (e.g. a text-only chip); skipping",
            file=sys.stderr,
        )
        return None
    return best, filename


# ── output ────────────────────────────────────────────────────────────────
def _write_index(out_dir: Path, data: dict) -> None:
    if not data:
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path = out_dir / "index.json"
    index_path.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def harvest(
    manifest: dict,
    sources: list[Path],
    official_repo: Path | None,
) -> tuple[dict, dict]:
    """Return (public, local) index dicts for the given manifest + sources."""
    assets = manifest.get("assets", [])
    exemplar_candidates = _collect_exemplar_candidates(sources)
    lib_cache: dict[str, list[dict] | None] = {}

    public: dict[str, dict] = {}
    local: dict[str, dict] = {}

    for asset in assets:
        key = asset.get("key")
        if not key:
            print(f"WARNING: manifest entry missing 'key', skipping: {asset}", file=sys.stderr)
            continue
        source_kind = asset.get("source")
        target = public if asset.get("public") else local

        if source_kind == "official":
            resolved = _resolve_official(asset, official_repo, lib_cache)
            if resolved is None:
                continue
            data_uri, from_file = resolved
            target[key] = {
                "dataUri": data_uri,
                "source": "official",
                "from": from_file,
                "license_note": OFFICIAL_LICENSE_NOTE,
            }
            print(f"OK: {key!r} <- {from_file} (official, {'public' if asset.get('public') else 'local'})")
        elif source_kind == "exemplar":
            match_spec = asset.get("match") or {}
            best = _best_exemplar_match(exemplar_candidates, match_spec)
            if best is None:
                print(
                    f"WARNING: no exemplar match found for {key!r} "
                    f"(value_regex={match_spec.get('value_regex')!r}); skipping",
                    file=sys.stderr,
                )
                continue
            data_uri, from_file = best
            target[key] = {
                "dataUri": data_uri,
                "source": "exemplar",
                "from": from_file,
                "license_note": EXEMPLAR_LICENSE_NOTE,
            }
            print(f"OK: {key!r} <- {from_file} (exemplar, {'public' if asset.get('public') else 'local'})")
        else:
            print(
                f"WARNING: unknown source kind {source_kind!r} for key {key!r}, skipping",
                file=sys.stderr,
            )
            continue

    return public, local


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Harvest brand/logo assets into a public + local (confidential) brand pack."
    )
    parser.add_argument("--manifest", type=Path, required=True, help="Path to brand-pack.manifest.json.")
    parser.add_argument("--out-public", type=Path, required=True, help="Output dir for public assets.")
    parser.add_argument("--out-local", type=Path, required=True, help="Output dir for local/confidential assets.")
    parser.add_argument(
        "--official-repo",
        type=Path,
        default=None,
        help="Path to a checkout of SAP/btp-solution-diagrams (for source=official assets).",
    )
    parser.add_argument(
        "sources", nargs="*", type=Path, help="Exemplar .drawio files to scan for source=exemplar assets."
    )
    args = parser.parse_args(argv)

    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"ERROR: could not read manifest {args.manifest}: {exc}", file=sys.stderr)
        return 2

    public, local = harvest(manifest, args.sources, args.official_repo)

    _write_index(args.out_public, public)
    _write_index(args.out_local, local)

    total_assets = len(manifest.get("assets", []))
    print(
        f"Harvested {len(public)} public + {len(local)} local asset(s) "
        f"out of {total_assets} manifest entries."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
