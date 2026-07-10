#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""Shared helpers for the *scaffold-and-extend* edit tools.

The surgical edit tools (``remove-cell.py``, ``add-node.py``, ``add-edge.py``)
all load a scaffolded SAP ``.drawio``, locate cells, read/write geometry on a
grid and save with a ``.bak`` of the previous on-disk state. Those mechanics
live here so a single module owns them (DRY).

The ``.drawio`` produced by the zone-composition engine is plain XML — no
comments or CDATA to preserve beyond attributes — so ``xml.etree.ElementTree``
round-trips it faithfully. Everything below operates on the ``ElementTree``
returned by :func:`load`.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path


def load(path) -> ET.ElementTree:
    """Parse a ``.drawio`` file into an ``ElementTree``."""
    return ET.parse(path)


def save(doc: ET.ElementTree, path) -> None:
    """Write ``doc`` to ``path`` after backing up the current file.

    The ``.bak`` captures the CURRENT on-disk bytes (read before overwriting),
    so it always reflects the state prior to this save.
    """
    path = Path(path)
    if path.exists():
        Path(str(path) + ".bak").write_bytes(path.read_bytes())
    doc.write(str(path), encoding="utf-8", xml_declaration=False)


def root(doc: ET.ElementTree) -> ET.Element:
    """The ``<root>`` element under the first ``<mxGraphModel>``."""
    model = doc.getroot().find(".//mxGraphModel")
    if model is None:
        raise ValueError("no <mxGraphModel> in document")
    r = model.find("root")
    if r is None:
        raise ValueError("no <root> under <mxGraphModel>")
    return r


def iter_cells(doc: ET.ElementTree):
    """Yield every ``mxCell`` element under ``<root>``."""
    return root(doc).iter("mxCell")


def find_cell(doc: ET.ElementTree, cid: str) -> ET.Element | None:
    """The ``mxCell`` whose ``id`` equals ``cid``, or ``None``."""
    for cell in iter_cells(doc):
        if cell.get("id") == cid:
            return cell
    return None


def find_cell_by_label(doc: ET.ElementTree, label: str) -> ET.Element | None:
    """The first ``mxCell`` whose ``value`` matches ``label``.

    Prefers an exact match; falls back to a case-insensitive match.
    """
    lowered = label.lower()
    fallback: ET.Element | None = None
    for cell in iter_cells(doc):
        value = cell.get("value")
        if value is None:
            continue
        if value == label:
            return cell
        if fallback is None and value.lower() == lowered:
            fallback = cell
    return fallback


def children(doc: ET.ElementTree, cid: str) -> list[ET.Element]:
    """All ``mxCell`` elements whose ``parent`` equals ``cid``."""
    return [c for c in iter_cells(doc) if c.get("parent") == cid]


def _geo(cell: ET.Element) -> ET.Element | None:
    return cell.find("mxGeometry")


def geometry(cell: ET.Element) -> tuple[float, float, float, float]:
    """``(x, y, width, height)`` of a cell as floats (``0.0`` when absent)."""
    geo = _geo(cell)
    if geo is None:
        return (0.0, 0.0, 0.0, 0.0)

    def _f(name: str) -> float:
        v = geo.get(name)
        return float(v) if v is not None else 0.0

    return (_f("x"), _f("y"), _f("width"), _f("height"))


def set_geometry(cell: ET.Element, x, y, w, h) -> ET.Element:
    """Set the cell's ``mxGeometry`` bounds, creating the element if needed."""
    geo = _geo(cell)
    if geo is None:
        geo = ET.SubElement(cell, "mxGeometry", {"as": "geometry"})
    geo.set("x", str(x))
    geo.set("y", str(y))
    geo.set("width", str(w))
    geo.set("height", str(h))
    return geo


def snap(v, grid: int = 10) -> int:
    """Round ``v`` to the nearest multiple of ``grid`` (default 10px)."""
    return int(round(v / grid)) * grid


def add_cell(doc: ET.ElementTree, attrib: dict,
             geom: tuple | None = None) -> ET.Element:
    """Append a new ``mxCell`` under ``<root>``.

    ``attrib`` becomes the cell's attributes; ``geom`` (``(x, y, w, h)``), when
    given, is written as a child ``mxGeometry``.
    """
    cell = ET.SubElement(root(doc), "mxCell", {k: str(v) for k, v in attrib.items()})
    if geom is not None:
        set_geometry(cell, *geom)
    return cell


def remove_cell_element(doc: ET.ElementTree, cell: ET.Element) -> None:
    """Remove ``cell`` from the ``<root>`` element."""
    root(doc).remove(cell)
