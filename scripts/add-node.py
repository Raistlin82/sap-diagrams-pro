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

Both modes resolve the target group and the node's icon (via
``ShapeIndex.resolve(--service)`` / ``resolve_generic(--genericIcon)``) and build
ONE contract-styled vertex parented to the group — a box carrying the resolved
``image=`` glyph + the label (and an optional subtitle), sized from the contract
and snapped to the 10px grid. They differ only in WHERE the node lands and
whether existing cells move.

``--mode slot`` (free-slot placement — nothing else moves):

  1. resolves the target group (and, when given, the ``--near`` anchor) BEFORE
     writing anything (an unknown id exits non-zero and touches no files),
  2. builds the node vertex parented to the group, then
  3. scans the group's content box on the grid — outward from ``--near``'s rect
     (else the content-box top-left) — for the first W×H rectangle that lies
     inside the group frame and overlaps no existing child of the group, and
     snaps + sets that geometry.

``--mode append`` (localized group reflow — the group grows):

  1. appends the node as a child of the group, then
  2. RE-PACKS the group's vertex children (existing + new) with the engine's own
     packing (``_skeleton_layout._pack`` under the engine's row/grid rule,
     ``NODE_GAP`` spacing) and grows the frame to contain them — the group keeps
     its own x/y, only its w/h grow (content-origin insets + right/bottom margins
     are taken from the existing children's box, so any frame the scaffold
     produced reflows correctly), and
  3. keeps the growth LOCAL: if the grown frame runs into exactly ONE top-level
     sibling it shifts that sibling clear (reported via ``--json`` as
     ``shifted``); if it would run into ≥2 it changes nothing on disk and exits
     non-zero, suggesting ``--mode slot`` instead.

On success the graph is saved via :func:`_drawio_edit.save`, which backs up the
prior file to ``<file>.bak`` first.

Usage:
  add-node.py diagram.drawio --group sub-1 --label "Cloud ALM" \\
      --service "Cloud ALM" --mode slot --near node-3
  add-node.py diagram.drawio --group g --label "DB" --type db --mode slot --json
  add-node.py diagram.drawio --group g --label "Cloud ALM" \\
      --service "Cloud ALM" --mode append --json

Exit codes:
  0 — node added
  1 — error (parse/IO failure)
  2 — usage (missing file, a group/near id not found, or an append reflow that
      would overlap ≥2 siblings — the file is left untouched)
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
def _new_node_id(group_id: str, label: str, service: str | None,
                 generic_icon: str | None) -> str:
    """Deterministic id for a newly added node (``node-<sha1[:8]>`` over the
    group id + label + service/icon), so re-runs are stable and two distinct
    nodes don't collide. Shared by both placement modes."""
    digest = hashlib.sha1(
        f"{group_id}\x00{label}\x00{service or ''}\x00{generic_icon or ''}"
        .encode("utf-8")
    ).hexdigest()[:8]
    return f"node-{digest}"


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

    node_id = _new_node_id(group.get("id"), label, service, generic_icon)
    edit.add_cell(
        doc,
        {"id": node_id, "value": _node_value(label, subtitle), "style": style,
         "vertex": "1", "parent": group.get("id")},
        (edit.snap(x, _GRID), edit.snap(y, _GRID), w, h),
    )
    return node_id


# ─────────────────────────────────────────────────────────────────────────────
# Append placement (localized group reflow)
# ─────────────────────────────────────────────────────────────────────────────
_EPS = 1e-6
_SKELETON = None


def _skeleton():
    """Lazily load the engine's skeleton-layout backend (its ``_pack`` packing
    helper, the ``_leaf_mode`` row/grid rule and ``NODE_GAP``). Lazy — like
    ``_shape_index`` — so importing this tool and running ``--mode slot`` never
    pay for the engine module."""
    global _SKELETON
    if _SKELETON is None:
        _SKELETON = _load_sibling("_skeleton_layout")
    return _SKELETON


def _set_xy(cell: ET.Element, x, y) -> None:
    """Move a cell (x/y only), leaving its width/height untouched — so a
    re-packed or shifted cell keeps its exact original size string."""
    geo = cell.find("mxGeometry")
    if geo is None:
        geo = ET.SubElement(cell, "mxGeometry", {"as": "geometry"})
    geo.set("x", str(x))
    geo.set("y", str(y))


# Frame-furniture id markers (engine ``_stable_id`` prefixes + molecule-local
# ids, which the emitter namespaces as ``<frame-anchor>-<localid>`` — hence a
# marker matches at the id start OR right after a ``-``): the SAP-BTP chip, the
# frame title, badge slots, step circles, info/separator cells.
_DECORATION_ID_MARKERS = ("btpbadge", "btpchip", "frame-title", "badge-",
                          "sep-", "st-", "if-")


def _is_decoration(cell: ET.Element) -> bool:
    """Whether a group child is frame FURNITURE (a header/decoration cell) rather
    than reflowable content.

    The engine marks every decoration cell ``connectable="0"`` (the "SAP BTP"
    chip, frame title, hyperscaler/runtime badge slots, step circles, …), so that
    is the primary, robust signal; a known decoration id segment is a
    belt-and-suspenders fallback for a template cell that was left connectable.
    Content nodes (``n-…``) and sub-group frames (``g-…``) match neither."""
    if (cell.get("connectable") or "") == "0":
        return True
    cid = cell.get("id") or ""
    return any(cid.startswith(m) or f"-{m}" in cid for m in _DECORATION_ID_MARKERS)


def _partition_children(
    doc: ET.ElementTree, group_id: str,
) -> tuple[list[tuple[ET.Element, float, float, float, float]],
           list[tuple[ET.Element, float, float, float, float]]]:
    """Split the group's non-edge, positive-size VERTEX children into
    ``(content, decorations)`` as ``(cell, x, y, w, h)`` in group-local coords.

    Only CONTENT is re-packed / measured; DECORATIONS (the frame's own header
    band — chip, title, badge slots — see ``_is_decoration``) keep their geometry
    untouched, and the content origin is pushed below their band. Re-packing them
    inline would collapse content up into the header (the whole point of the
    observed-inset approach)."""
    content: list[tuple[ET.Element, float, float, float, float]] = []
    decos: list[tuple[ET.Element, float, float, float, float]] = []
    for c in edit.children(doc, group_id):
        if c.get("edge") == "1":
            continue
        x, y, w, h = edit.geometry(c)
        if w <= 0 or h <= 0:
            continue
        (decos if _is_decoration(c) else content).append((c, x, y, w, h))
    return content, decos


def add_node_append(
    doc: ET.ElementTree, group: ET.Element, *, label: str,
    service: str | None = None, generic_icon: str | None = None,
    subtitle: str | None = None, node_type: str | None = None,
) -> dict:
    """Append a new node as a child of ``group`` and REFLOW ONLY that group.

    The group's vertex children (existing + the new node) are re-packed with the
    engine's own packing (``_skeleton_layout._pack`` under the ``_leaf_mode``
    row/grid rule, ``NODE_GAP`` spacing); the frame grows to contain them while
    keeping its own x/y. Growth is localized: a grown frame that runs into
    exactly ONE top-level sibling shifts that sibling clear (returned in
    ``shifted``); with ≥2 it returns them in ``conflicts`` and shifts nothing
    (the caller must not save — the reflow can't be localized).

    Insets: the content's top-left origin and the right/bottom margins are read
    from the EXISTING CONTENT children's bounding box inside the current frame
    (an empty group falls back to ``_molecules.frame_insets``). A scaffolded
    ``.drawio`` group carries no engine ``type``, so ``frame_insets`` alone would
    hand back the one generic inset for every frame and re-pack children over a
    molecule frame's own header (the "SAP BTP" chip / badge row); preserving the
    observed insets keeps the reflow correct for whatever frame the scaffold
    produced. Frame FURNITURE (``_is_decoration`` — the header band) is never
    re-packed and never moved; the content origin is pushed below it.

    Returns ``{"id", "shifted": [ids], "conflicts": [ids]}``; mutates ``doc``."""
    SL = _skeleton()
    contract = M.load_contract()
    uri = _resolve_icon_uri(service, generic_icon)
    style, w, h = _node_style_and_size(node_type, uri, contract)
    gid = group.get("id")
    node_id = _new_node_id(gid, label, service, generic_icon)

    gx, gy, old_gw, old_gh = edit.geometry(group)
    content, decos = _partition_children(doc, gid)

    # Decorations (the header band) are left where they are; the content must
    # start BELOW their bottom edge, and the grown frame must still enclose them.
    deco_bottom = max((y + h for _, _, y, _, h in decos), default=0.0)
    deco_right = max((x + w for _, x, _, w, _ in decos), default=0.0)

    # Content-origin insets + right/bottom margins, observed from the CONTENT box.
    if content:
        left = min(x for _, x, _, _, _ in content)
        top = max(deco_bottom, min(y for _, _, y, _, _ in content))
        max_r = max(x + cw for _, x, _, cw, _ in content)
        max_b = max(y + ch for _, _, y, _, ch in content)
        right_margin = max(0.0, old_gw - max_r)
        bottom_margin = max(0.0, old_gh - max_b)
    else:
        pad_x, pad_top, pad_bot = M.frame_insets(None, contract)
        left, right_margin, bottom_margin = pad_x, pad_x, pad_bot
        top = max(deco_bottom, pad_top)

    # Re-pack the existing CONTENT footprints + the new node under the engine rule.
    items = [(cw, ch) for _, _, _, cw, ch in content] + [(float(w), float(h))]
    mode = SL._leaf_mode(None, None, len(items))
    positions, _pw, _ph = SL._pack(items, mode, SL.NODE_GAP)

    ox0 = edit.snap(left, _GRID)
    oy0 = edit.snap(top, _GRID)
    rights: list[float] = []
    bottoms: list[float] = []
    for (cell, _x, _y, cw, ch), (rx, ry) in zip(content, positions):
        nx = edit.snap(ox0 + rx, _GRID)
        ny = edit.snap(oy0 + ry, _GRID)
        _set_xy(cell, nx, ny)                     # keep each child's own w/h
        rights.append(nx + cw)
        bottoms.append(ny + ch)
    nrx, nry = positions[len(content)]
    nnx = edit.snap(ox0 + nrx, _GRID)
    nny = edit.snap(oy0 + nry, _GRID)
    edit.add_cell(
        doc,
        {"id": node_id, "value": _node_value(label, subtitle), "style": style,
         "vertex": "1", "parent": gid},
        (nnx, nny, w, h),
    )
    rights.append(nnx + w)
    bottoms.append(nny + h)

    # Grow the frame to contain the re-packed content + preserved margins, never
    # shrinking below the old size (so the untouched decorations stay enclosed).
    new_gw = max(int(round(old_gw)),
                 edit.snap(max(max(rights) + right_margin, deco_right), _GRID))
    new_gh = max(int(round(old_gh)),
                 edit.snap(max(max(bottoms) + bottom_margin, deco_bottom), _GRID))
    edit.set_geometry(group, int(round(gx)), int(round(gy)), new_gw, new_gh)

    # Localized growth: g's x/y are fixed, so it only ever grows toward +x / +y.
    # A sibling that the NEW frame overlaps but the OLD frame did not is one the
    # growth ran into — shift a single one clear, refuse on two or more.
    old_rect = Rect(gx, gy, old_gw, old_gh)
    new_rect = Rect(gx, gy, float(new_gw), float(new_gh))
    old_right, old_bottom = gx + old_gw, gy + old_gh
    new_right, new_bottom = gx + new_gw, gy + new_gh

    newly: list[tuple[ET.Element, float, float]] = []
    for s in edit.children(doc, group.get("parent")):
        # Only real sibling GROUPS matter — skip self, edges, and top-level
        # furniture (title / watermark / legend / NETWORK separator are
        # connectable=0 decorations, not siblings to shove aside).
        if (s is group or s.get("vertex") != "1" or s.get("edge") == "1"
                or _is_decoration(s)):
            continue
        sx, sy, sw, sh = edit.geometry(s)
        srect = Rect(sx, sy, sw, sh)
        if new_rect.intersects(srect) and not old_rect.intersects(srect):
            newly.append((s, sx, sy))

    shifted: list[str] = []
    conflicts: list[str] = []
    if len(newly) == 1:
        s, sx, sy = newly[0]
        # Push it just past the grown edge on whichever side the growth reached
        # it from (right and/or below) — the minimal clear, = the overlap amount.
        dx = (new_right - sx) if (sx >= old_right - _EPS and new_right > sx) else 0.0
        dy = (new_bottom - sy) if (sy >= old_bottom - _EPS and new_bottom > sy) else 0.0
        if dx or dy:
            _set_xy(s, edit.snap(sx + dx, _GRID), edit.snap(sy + dy, _GRID))
            shifted = [s.get("id")]
        else:                                     # engulfed head-on — can't localize
            conflicts = [s.get("id")]
    elif len(newly) >= 2:
        conflicts = [s.get("id") for s, _, _ in newly]

    return {"id": node_id, "shifted": shifted, "conflicts": conflicts}


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
                    help="slot: free-slot placement (nothing else moves); "
                         "append: add as a child and reflow the group (grows the "
                         "frame; shifts one sibling if the growth reaches it)")
    ap.add_argument("--near", dest="near_id",
                    help="id of a cell to place the new node beside (slot mode)")
    ap.add_argument("--json", action="store_true",
                    help="print {\"id\": \"<newid>\"} to stdout")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if not args.drawio.exists():
        print(f"{args.drawio}: file not found", file=sys.stderr)
        return 2

    try:
        doc = edit.load(args.drawio)
    except (ET.ParseError, OSError) as exc:
        print(f"add-node failed to parse {args.drawio}: {exc}", file=sys.stderr)
        return 1

    group = edit.find_cell(doc, args.group)
    if group is None:
        print(f"no cell with id {args.group!r} (group)", file=sys.stderr)
        return 2

    if args.mode == "append":
        result = add_node_append(
            doc, group, label=args.label, service=args.service,
            generic_icon=args.generic_icon, subtitle=args.subtitle,
            node_type=args.node_type,
        )
        if result["conflicts"]:
            ids = ", ".join(result["conflicts"])
            print(
                f"add-node --mode append: growing group {args.group!r} to fit "
                f"{args.label!r} would overlap {len(result['conflicts'])} sibling "
                f"groups ({ids}); the reflow can't be localized — retry with "
                f"--mode slot to drop the node in place without reflowing.",
                file=sys.stderr,
            )
            return 2                              # nothing saved → file untouched
        try:
            edit.save(doc, args.drawio)
        except OSError as exc:
            print(f"add-node failed to write {args.drawio}: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps({"id": result["id"], "shifted": result["shifted"]}))
        else:
            extra = (f" (shifted {', '.join(result['shifted'])} clear of the grown "
                     "frame)" if result["shifted"] else "")
            print(f"add-node: {result['id']} appended to group {args.group!r} in "
                  f"{args.drawio}{extra}", file=sys.stderr)
        return 0

    # ── slot mode ──────────────────────────────────────────────────────────
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
