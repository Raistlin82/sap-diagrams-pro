#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""Drop a new styled node into an existing group of a scaffolded ``.drawio`` —
part of the hybrid *scaffold* path's edit toolkit.

Adding a node to a real SAP template is more than an ``<mxCell vertex="1">``: the
node has to carry the canonical SAP service *icon* (resolved the SAME way the
zone-composition emitter resolves it — through ``generate-drawio.ShapeIndex`` —
so a scaffolded diagram and an extended one draw the exact same glyph), sit at a
box size taken from the style contract, and land in a spot that does NOT collide
with the group's existing children.

This module implements **only** ``--mode slot`` (the ``--mode append`` reflow is a
separate later task and raises ``NotImplementedError`` for now). Slot mode:

  1. resolves the target group (and, when given, the ``--near`` anchor) BEFORE
     writing anything (an unknown id exits non-zero and touches no files),
  2. resolves the icon via ``ShapeIndex.resolve(--service)`` (or
     ``resolve_generic(--genericIcon)``) and builds ONE vertex parented to the
     group — a contract-styled box carrying the resolved ``image=`` glyph + the
     label (and an optional subtitle),
  3. picks the box size from the style contract (snapped to the 10px grid), then
  4. scans the group's content box on the grid — outward from ``--near``'s rect
     (else the content-box top-left) — for the first W×H rectangle that lies
     inside the group frame and overlaps no existing child of the group, and
     snaps + sets that geometry.

On success the graph is saved via :func:`_drawio_edit.save`, which backs up the
prior file to ``<file>.bak`` first.

Usage:
  add-node.py diagram.drawio --group sub-1 --label "Cloud ALM" \\
      --service "Cloud ALM" --mode slot --near node-3
  add-node.py diagram.drawio --group g --label "DB" --type db --mode slot --json

Exit codes:
  0 — node added
  1 — error (parse/IO failure)
  2 — usage (missing file, or a group/near id not found)
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_HERE = Path(__file__).resolve().parent

_GRID = 10


def _load_sibling(name: str):
    """Import a sibling ``scripts/<name>.py`` the repo's guarded, path-based way
    (check ``sys.modules`` first, then ``spec_from_file_location``) so this
    process and the test harness share one module identity — the convention
    ``remove-cell.py`` / ``add-edge.py`` / ``conftest.load_script`` all use.

    A dashed filename (``generate-drawio``) can't be a Python module name, so the
    module key is the dash-free form (``generate_drawio``) — mirroring
    ``conftest.load_script`` exactly, which is what makes the ShapeIndex loaded
    here and in the tests the SAME class object."""
    key = name.replace("-", "_")
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, _HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod             # register BEFORE exec (see conftest note)
    spec.loader.exec_module(mod)
    return mod


edit = _load_sibling("_drawio_edit")
M = _load_sibling("_molecules")
Rect = _load_sibling("_geom_checks").Rect


# ─────────────────────────────────────────────────────────────────────────────
# Icon resolution (reuse the emitter's ShapeIndex path)
# ─────────────────────────────────────────────────────────────────────────────
_SHAPE_INDEX = None


def _shape_index():
    """Lazily load (once) the emitter's ``ShapeIndex`` from generate-drawio.py.

    Loaded lazily — not at import — so the (large) emitter module is only paid
    for when a node actually needs an icon, and importing this tool stays cheap.
    """
    global _SHAPE_INDEX
    if _SHAPE_INDEX is None:
        _SHAPE_INDEX = _load_sibling("generate-drawio").ShapeIndex.load()
    return _SHAPE_INDEX


def _extract_uri(drawio_style: str | None) -> str | None:
    """Pull the ``image=<dataUri>`` out of a ShapeIndex icon style, normalising
    the ``;base64,`` form to the comma form draw.io parses inside a style string
    (the same fix ``generate-drawio._safe_img`` applies — a ``;`` mid-URI would
    otherwise truncate the image and render the shape blank)."""
    if not drawio_style:
        return None
    m = re.search(r"image=([^;]+)", drawio_style.replace(";base64,", ","))
    return m.group(1) if m else None


def _resolve_icon_uri(service: str | None, generic_icon: str | None) -> str | None:
    """Resolve a node's icon dataUri: an explicit ``--genericIcon`` wins (author
    choice — the emitter's own priority), else the ``--service`` name. Returns
    ``None`` when nothing resolves — an unresolved icon is never an error, the
    node simply renders icon-less."""
    idx = _shape_index()
    entry = idx.resolve_generic(generic_icon) if generic_icon else None
    if entry is None and service:
        entry = idx.resolve(service)
    return _extract_uri(entry.get("drawioStyle")) if entry else None


# ─────────────────────────────────────────────────────────────────────────────
# Node cell: contract-driven style + size
# ─────────────────────────────────────────────────────────────────────────────
# --type → the style-contract molecule whose style + geometry the node borrows.
# The default (no --type) is a right-zone "backend box": a white rounded box that
# carries the resolved SAP icon on its left with the label beside it — the
# single-cell service-node the emitter draws for SAP/3rd-party apps.
_TYPE_MOLECULE = {
    "product": "product-box",
    "chip": "chip",
    "db": "db",
}
_DEFAULT_MOLECULE = "backend-box"
# Molecules that render as a titled box and can host a left icon overlay.
_ICON_BOX_MOLECULES = {"backend-box", "product-box"}


def _node_value(label: str, subtitle: str | None) -> str:
    """The cell value: the bare label, or (with a subtitle) the two-line HTML
    the emitter's backend box uses — bold title over a small muted subtitle."""
    if subtitle:
        return (f'<b>{label}</b>'
                f'<div style="font-size:9px;color:#556B82;line-height:13px;">'
                f'{subtitle}</div>')
    return label


def _node_style_and_size(
    node_type: str | None, uri: str | None, contract: dict,
) -> tuple[str, int, int]:
    """``(style, w, h)`` for the new node — style + geometry both sourced from the
    style contract's molecule for ``node_type``.

    When an icon resolved and the molecule is a titled box, the resolved
    ``image=`` glyph is composited onto its left (``shape=label``, icon-left /
    label-right — the emitter's ``_backend_box`` layout), so the node reads as a
    proper SAP service box. W/H come from the contract and are snapped to the
    10px grid so the whole placed rect stays grid-aligned."""
    molname = _TYPE_MOLECULE.get(node_type or "", _DEFAULT_MOLECULE)
    mol = contract["molecules"][molname]
    base = mol["style"]
    geo = mol.get("geometry", {})
    w = edit.snap(float(geo.get("w", 202.0)), _GRID)
    h = edit.snap(float(geo.get("h", 70.0)), _GRID)
    if uri and molname in _ICON_BOX_MOLECULES:
        style = (base + "shape=label;imageAlign=left;imageVerticalAlign=middle;"
                 "imageWidth=28;imageHeight=28;spacingLeft=44;spacingRight=8;"
                 "align=left;verticalAlign=middle;image=" + uri + ";")
    else:
        style = base
    return style, w, h


# ─────────────────────────────────────────────────────────────────────────────
# Slot placement
# ─────────────────────────────────────────────────────────────────────────────
def _child_obstacles(doc: ET.ElementTree, group_id: str) -> list[Rect]:
    """Group-local rects of the group's existing VERTEX children (edges and
    zero-size cells can't be collided with, so they're skipped). Child geometry
    is already relative to the group's origin, i.e. the same frame a new child's
    geometry lives in."""
    rects: list[Rect] = []
    for c in edit.children(doc, group_id):
        if c.get("edge") == "1":
            continue
        x, y, w, h = edit.geometry(c)
        if w <= 0 or h <= 0:
            continue
        rects.append(Rect(x, y, w, h))
    return rects


def _grid_positions(hi: float, grid: int = _GRID) -> list[int]:
    """Grid-aligned coordinates ``0, grid, 2*grid, …`` up to (and including) the
    largest that is ``<= hi`` — the candidate top-left offsets that keep a rect
    inside a content box of the matching extent."""
    if hi < 0:
        return []
    out: list[int] = []
    v = 0
    while v <= hi + 1e-9:
        out.append(v)
        v += grid
    return out


def _find_slot(
    group_wh: tuple[float, float], near: tuple[float, float] | None,
    obstacles: list[Rect], w: int, h: int, grid: int = _GRID,
) -> tuple[int, int]:
    """First free W×H slot in the group's content box, scanning outward on the
    grid from ``near`` (else the content-box top-left).

    "Free" = the whole rect lies inside the content box (``[0,gw] × [0,gh]``) and
    overlaps no obstacle (``Rect.intersects`` is strict, so a rect merely
    *touching* a child's edge is allowed). Among the free candidates the one
    closest (Euclidean) to the scan origin wins — deterministic ties break toward
    smaller x then y (iteration order). Falls back to ``(0, 0)`` when nothing
    fits (e.g. a node bigger than its group)."""
    gw, gh = group_wh
    xs = _grid_positions(gw - w, grid)
    ys = _grid_positions(gh - h, grid)
    if not xs or not ys:
        return 0, 0
    sx, sy = near if near is not None else (0.0, 0.0)
    best: tuple[int, int] | None = None
    best_d = float("inf")
    for x in xs:
        for y in ys:
            cand = Rect(x, y, w, h)
            if any(cand.intersects(o) for o in obstacles):
                continue
            d = (x - sx) ** 2 + (y - sy) ** 2
            if d < best_d:
                best_d = d
                best = (x, y)
    return best if best is not None else (0, 0)


# ─────────────────────────────────────────────────────────────────────────────
# Core
# ─────────────────────────────────────────────────────────────────────────────
def add_node_slot(
    doc: ET.ElementTree, group: ET.Element, *, label: str,
    service: str | None = None, generic_icon: str | None = None,
    subtitle: str | None = None, node_type: str | None = None,
    near: ET.Element | None = None,
) -> str:
    """Build the node cell, place it in a free slot of ``group`` and append it.
    Returns the new cell id."""
    contract = M.load_contract()
    uri = _resolve_icon_uri(service, generic_icon)
    style, w, h = _node_style_and_size(node_type, uri, contract)

    _gx, _gy, gw, gh = edit.geometry(group)
    near_xy = None
    if near is not None:
        nx, ny, _nw, _nh = edit.geometry(near)
        near_xy = (nx, ny)
    x, y = _find_slot((gw, gh), near_xy, _child_obstacles(doc, group.get("id")), w, h)

    digest = hashlib.sha1(
        f"{group.get('id')}\x00{label}\x00{service or ''}\x00{generic_icon or ''}"
        .encode("utf-8")
    ).hexdigest()[:8]
    node_id = f"node-{digest}"

    edit.add_cell(
        doc,
        {"id": node_id, "value": _node_value(label, subtitle), "style": style,
         "vertex": "1", "parent": group.get("id")},
        (edit.snap(x, _GRID), edit.snap(y, _GRID), w, h),
    )
    return node_id


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("drawio", type=Path)
    ap.add_argument("--group", required=True, help="id of the container group")
    ap.add_argument("--label", required=True, help="node label / title")
    ap.add_argument("--service", help="SAP service name to resolve an icon for")
    ap.add_argument("--genericIcon", dest="generic_icon",
                    help="generic-icon key (user, cloud-connector, …)")
    ap.add_argument("--subtitle", help="optional second line under the title")
    ap.add_argument("--type", dest="node_type", choices=["product", "chip", "db"],
                    help="node shape (default: a service backend box)")
    ap.add_argument("--capabilities",
                    help="';'-separated capability labels (reserved; not expanded "
                         "in slot mode's single vertex)")
    ap.add_argument("--mode", required=True, choices=["append", "slot"],
                    help="slot: free-slot placement in the group (append: later task)")
    ap.add_argument("--near", dest="near_id",
                    help="id of a cell to place the new node beside (slot mode)")
    ap.add_argument("--json", action="store_true",
                    help="print {\"id\": \"<newid>\"} to stdout")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if not args.drawio.exists():
        print(f"{args.drawio}: file not found", file=sys.stderr)
        return 2

    if args.mode == "append":
        # --mode append (reflow the group's packing to fit the new node) is a
        # separate later task; slot mode is the whole of THIS tool.
        raise NotImplementedError("--mode append is implemented in a later task")

    try:
        doc = edit.load(args.drawio)
    except (ET.ParseError, OSError) as exc:
        print(f"add-node failed to parse {args.drawio}: {exc}", file=sys.stderr)
        return 1

    group = edit.find_cell(doc, args.group)
    if group is None:
        print(f"no cell with id {args.group!r} (group)", file=sys.stderr)
        return 2
    near = None
    if args.near_id:
        near = edit.find_cell(doc, args.near_id)
        if near is None:
            print(f"no cell with id {args.near_id!r} (near)", file=sys.stderr)
            return 2

    node_id = add_node_slot(
        doc, group, label=args.label, service=args.service,
        generic_icon=args.generic_icon, subtitle=args.subtitle,
        node_type=args.node_type, near=near,
    )

    try:
        edit.save(doc, args.drawio)
    except OSError as exc:
        print(f"add-node failed to write {args.drawio}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"id": node_id}))
    else:
        print(f"add-node: {node_id} added to group {args.group!r} in {args.drawio}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
