#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""_drawio_io.py — shared draw.io / mxlibrary parsing helpers.

Two source formats appear across the SAP btp-solution-diagrams corpus and the
Lutech exemplars:

  * ``.drawio`` files (``<mxfile>`` with one or more ``<diagram>`` pages, each
    page either inline ``<mxGraphModel>`` XML or a compressed base64 payload).
  * ``mxlibrary`` shape libraries (``<mxlibrary>[JSON array]</mxlibrary>`` whose
    body is a JSON array of ``{title, xml, w, h}`` entries).

Both ``harvest-brand-assets.py`` (brand pack) and ``build-style-contract.py``
(style contract) need to read these, so the two primitives live here:

    decode_diagram_pages(path) -> list[(name, root_element)]
    parse_mxlibrary(path)      -> list[entry] | None

Escaping note (there is a regression test guarding this): ``parse_mxlibrary``
returns each entry's ``xml`` value exactly as ElementTree's ``.text`` yields it
— i.e. after the single round of XML entity-decoding that parsing the outer
``<mxlibrary>`` wrapper already performs. Callers MUST NOT ``html.unescape()``
it again: a second decode corrupts entries whose ``value="…"`` itself holds
escaped rich text (e.g. the "(Text Only)" chips).
"""
from __future__ import annotations

import base64
import json
import sys
import urllib.parse
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

# Prefer defusedxml for parsing untrusted .drawio/.xml input (guards XXE /
# billion-laughs); fall back to the stdlib parser when it isn't installed.
# Mirrors scripts/validate-drawio.py's posture.
try:
    from defusedxml.ElementTree import fromstring as _xml_fromstring
    from defusedxml.ElementTree import parse as _xml_parse
except Exception:  # pragma: no cover - defusedxml optional
    from xml.etree.ElementTree import fromstring as _xml_fromstring
    from xml.etree.ElementTree import parse as _xml_parse


# ── draw.io page decompression ───────────────────────────────────────────
def decode_diagram_text(text: str) -> str | None:
    """Turn a compressed ``<diagram>`` text payload back into raw XML.

    draw.io stores each page either inline (child ``<mxGraphModel>`` element —
    handled by :func:`decode_diagram_pages` before this is reached) or
    compressed: base64 → raw DEFLATE (zlib, -15 window bits ⇒ no
    header/checksum) → URL-decoded UTF-8 text. Content that already starts
    with ``'<'`` is passed through unchanged.
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


def decode_diagram_pages(path: Path) -> list[tuple[str, ET.Element]]:
    """Return one ``(page_name, root_element)`` per diagram page.

    ``root_element`` is always safe to walk with ``.iter("mxCell")``: for an
    inline page it is the ``<diagram>`` element itself (its ``<mxGraphModel>``
    child holds the cells); for a compressed page it is the freshly-parsed
    ``<mxGraphModel>`` root. Pages that fail to decode/parse are skipped with a
    WARNING rather than aborting the read.
    """
    tree = _xml_parse(path)
    root = tree.getroot()
    diagrams = root.findall("diagram") if root.tag == "mxfile" else [root]
    pages: list[tuple[str, ET.Element]] = []
    for d in diagrams:
        name = d.get("name") or ""
        if list(d):
            # Already-parsed XML tree (uncompressed page).
            pages.append((name, d))
            continue
        xml_text = decode_diagram_text(d.text or "")
        if not xml_text:
            continue
        try:
            pages.append((name, _xml_fromstring(xml_text)))
        except ET.ParseError as exc:
            print(f"WARNING: could not parse a decompressed diagram page: {exc}", file=sys.stderr)
    return pages


# ── official-library resolution ──────────────────────────────────────────
def parse_mxlibrary(path: Path) -> list[dict] | None:
    """Parse an mxlibrary XML file (``<mxlibrary>[JSON array]</mxlibrary>``).

    Returns the decoded JSON array of entries, ``[]`` for an empty library, or
    ``None`` when the file is missing / malformed (a WARNING is printed). See
    the module docstring for the escaping contract on each entry's ``xml``.
    """
    path = Path(path)
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


def parse_entry_cells(entry_xml: str) -> ET.Element | None:
    """Parse one mxlibrary entry's ``xml`` payload into its root element.

    Convenience wrapper so callers do not re-import the defused parser. Returns
    ``None`` (with a WARNING) on a parse error. Do NOT pre-unescape ``entry_xml``
    — see the module docstring.
    """
    try:
        return _xml_fromstring(entry_xml or "")
    except ET.ParseError as exc:
        print(f"WARNING: could not parse mxlibrary entry xml: {exc}", file=sys.stderr)
        return None
