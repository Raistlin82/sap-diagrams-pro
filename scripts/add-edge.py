#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""Wire a new styled, orthogonally-routed edge between two existing vertices of a
scaffolded ``.drawio`` — part of the hybrid *scaffold* path's edit toolkit.

Adding an edge to a real SAP template is not just an ``<mxCell edge="1">``: the
connector has to carry the right *flow-family* colour (the six ``edge-*`` style
molecules), leave and enter its endpoints on the facing sides (so it reads as a
clean orthogonal line, not a diagonal), and — when the diagram has a NETWORK
separator — keep its protocol pill *off* the seam (a pill parked on the
"NETWORK" caption is the classic ``TEXT_OVERLAP`` the quality gate flags).

So this tool:

  1. resolves both endpoint cells BEFORE writing anything (an unknown id exits
     non-zero and touches no files),
  2. styles the edge from ``--flowFamily`` via the contract's ``edge-<family>``
     molecule (fallback ``edge-default``), adding an ``orthogonalEdgeStyle`` with
     ``exit*/entry*`` anchors chosen from the endpoints' relative centres,
  3. emits ONE interior waypoint (a clean L elbow),
  4. drops an optional protocol pill (rounded ``arcSize=50`` chip, coloured by
     ``--kind``/text via the pill molecule) and an optional edge label as
     separate cells on the edge's longest segment, and
  5. shifts the pill clear of the separator keep-out bands (bar + caption) when a
     ``netsep`` cell is present in the file.

Ids are stable — derived from a short hash of ``(source, target)`` — so a
re-run addressing the same pair overwrites rather than duplicates. On success the
graph is saved via :func:`_drawio_edit.save`, which backs up the prior file to
``<file>.bak`` first.

Usage:
  add-edge.py diagram.drawio --source idp --target ias --flowFamily identity \\
      --label "Login" --pill "SAML2/OIDC"
  add-edge.py diagram.drawio --source a --target b --style dashed --json

Exit codes:
  0 — edge added
  1 — error (parse/IO failure)
  2 — usage (missing file, or a source/target id not found)
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load_sibling(name: str):
    """Import a dash-free sibling ``scripts/<name>.py`` the repo's guarded,
    path-based way (check ``sys.modules`` first, then ``spec_from_file_location``)
    so this process and the test harness share one module identity — the same
    convention ``remove-cell.py`` and ``conftest.load_script`` use."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod            # register BEFORE exec (see conftest note)
    spec.loader.exec_module(mod)
    return mod


edit = _load_sibling("_drawio_edit")
M = _load_sibling("_molecules")
cr = _load_sibling("_channel_router")

# Plain / flowFamily edge label: a white-backed text box (matches the emitter's
# routing-active label style in generate-drawio.py) so it hides the connector
# behind the text.
_LABEL_STYLE = ("text;html=1;whiteSpace=wrap;rounded=0;strokeColor=none;"
                "fillColor=#FFFFFF;fontColor=#556B82;fontSize=10;"
                "align=center;verticalAlign=middle;")

# px the pill/label keeps clear of a separator band after a nudge (a hair more
# than the render's stroke so the strict overlap test can't graze it).
_CLEAR_MARGIN = 4.0


def _fmt(v: float) -> str:
    """Compact float for a style string ("1", "0.5", not "1.0")."""
    return "%g" % float(v)


def _abs_box(doc: ET.ElementTree, cell: ET.Element) -> tuple[float, float, float, float]:
    """Absolute ``(x, y, w, h)`` of a cell, summing ancestor origins so a node
    nested in a group still yields diagram-space coordinates (needed for the
    separator clearance to line up with the bar's absolute geometry)."""
    x, y, w, h = edit.geometry(cell)
    parent_id = cell.get("parent")
    seen: set[str] = set()
    while parent_id and parent_id not in ("0", "1") and parent_id not in seen:
        seen.add(parent_id)
        parent = edit.find_cell(doc, parent_id)
        if parent is None:
            break
        px, py, _pw, _ph = edit.geometry(parent)
        x += px
        y += py
        parent_id = parent.get("parent")
    return x, y, w, h


def _ports(src_box, dst_box):
    """Choose exit/entry anchors from the endpoints' relative centres: the edge
    leaves the source on the side facing the target and enters the target on the
    opposite side. Returns ``(exit_frac, entry_frac, exit_pt, entry_pt)`` where
    the fracs are draw.io ``(X, Y)`` anchors in ``[0,1]`` and the pts are the
    absolute anchor coordinates."""
    sx, sy, sw, sh = src_box
    dx, dy, dw, dh = dst_box
    scx, scy = sx + sw / 2.0, sy + sh / 2.0
    dcx, dcy = dx + dw / 2.0, dy + dh / 2.0
    ddx, ddy = dcx - scx, dcy - scy

    if abs(ddx) >= abs(ddy):                       # horizontal primary
        if ddx >= 0:
            exit_frac, entry_frac = (1.0, 0.5), (0.0, 0.5)
        else:
            exit_frac, entry_frac = (0.0, 0.5), (1.0, 0.5)
    else:                                          # vertical primary
        if ddy >= 0:
            exit_frac, entry_frac = (0.5, 1.0), (0.5, 0.0)
        else:
            exit_frac, entry_frac = (0.5, 0.0), (0.5, 1.0)

    exit_pt = (sx + sw * exit_frac[0], sy + sh * exit_frac[1])
    entry_pt = (dx + dw * entry_frac[0], dy + dh * entry_frac[1])
    return exit_frac, entry_frac, exit_pt, entry_pt


def _elbow(exit_pt, entry_pt) -> tuple[float, float]:
    """The single interior waypoint of a clean L between two orthogonal anchors:
    the long leg runs along the dominant axis first, so pills parked on the
    longest segment sit on that leg."""
    (ex, ey), (nx, ny) = exit_pt, entry_pt
    if abs(nx - ex) >= abs(ny - ey):               # horizontal-dominant → H then V
        return (nx, ey)
    return (ex, ny)                                # vertical-dominant → V then H


def _build_edge_style(base: str, exit_frac, entry_frac, line_style: str | None) -> str:
    """Compose the connector style: the family molecule + an orthogonal route
    with explicit exit/entry anchors + the optional line modifier. Appended keys
    win in draw.io's last-value-wins style map, so a modifier cleanly overrides
    the molecule's stroke width / dashing."""
    style = base if base.endswith(";") else base + ";"
    if "edgeStyle=" not in base:                   # edge-default has no route style
        style = "edgeStyle=orthogonalEdgeStyle;" + style
    ex, ey = exit_frac
    nx, ny = entry_frac
    style += (f"exitX={_fmt(ex)};exitY={_fmt(ey)};exitDx=0;exitDy=0;"
              f"entryX={_fmt(nx)};entryY={_fmt(ny)};entryDx=0;entryDy=0;")
    if line_style == "dashed":
        style += "dashed=1;"
    elif line_style == "dotted":
        style += "dashed=1;dashPattern=1 4;"
    elif line_style == "thick":
        style += "strokeWidth=3;"
    return style


def _nudge_clear(x: float, y: float, w: float, h: float, obstacles) -> tuple[float, float]:
    """Shift a ``(x, y, w, h)`` rect (top-left origin) horizontally until it
    clears every obstacle band — the separator is vertical, so an x-shift moves
    the pill to just before/after the crossing (what the channel router does)."""
    Rect = cr.Rect
    for _ in range(64):
        rect = Rect(x, y, w, h)
        hit = next((o for o in obstacles if rect.intersects(o)), None)
        if hit is None:
            break
        cx = x + w / 2.0
        if cx <= hit.cx:
            x = hit.x - w - _CLEAR_MARGIN          # park left of the band
        else:
            x = hit.right + _CLEAR_MARGIN          # park right of the band
    return x, y


def _separator_net_sep(doc: ET.ElementTree) -> dict | None:
    """Reconstruct ``{x, y0, y1}`` from a ``netsep`` cell if the file has one
    (id prefix ``netsep`` or a ``jumpStyle=gap`` stroke), reading its
    source/target points (falling back to its waypoint Array)."""
    for cell in edit.iter_cells(doc):
        style = cell.get("style") or ""
        cid = cell.get("id") or ""
        if "jumpStyle=gap" not in style and not cid.startswith("netsep"):
            continue
        geo = cell.find("mxGeometry")
        if geo is None:
            continue
        pts: dict[str, tuple[float, float]] = {}
        for mp in geo.findall("mxPoint"):
            role = mp.get("as")
            if role in ("sourcePoint", "targetPoint"):
                pts[role] = (float(mp.get("x", 0) or 0), float(mp.get("y", 0) or 0))
        if "sourcePoint" in pts and "targetPoint" in pts:
            (sx, sy), (_tx, ty) = pts["sourcePoint"], pts["targetPoint"]
        else:
            arr = geo.find("Array")
            aps = ([(float(p.get("x", 0) or 0), float(p.get("y", 0) or 0))
                    for p in arr.findall("mxPoint")] if arr is not None else [])
            if len(aps) < 2:
                continue
            (sx, sy), (_tx, ty) = aps[0], aps[-1]
        return {"x": sx, "y0": min(sy, ty), "y1": max(sy, ty)}
    return None


def _emit_slot_cell(doc, cid, value, style, center, w, h, obstacles):
    """Emit a pill/label vertex centred on ``center`` (nudged clear of the
    separator bands), returning the created element."""
    x = center[0] - w / 2.0
    y = center[1] - h / 2.0
    if obstacles:
        x, y = _nudge_clear(x, y, w, h, obstacles)
    return edit.add_cell(
        doc,
        {"id": cid, "value": value, "style": style,
         "vertex": "1", "parent": "1", "connectable": "0"},
        (x, y, w, h),
    )


def add_edge(doc, source, target, *, flow_family=None, kind=None,
             pill_text=None, label_text=None, line_style=None) -> dict:
    """Wire the styled orthogonal edge (+ optional pill/label) between the two
    resolved endpoint cells. Returns the ids emitted."""
    contract = M.load_contract()

    src_box = _abs_box(doc, source)
    dst_box = _abs_box(doc, target)
    exit_frac, entry_frac, exit_pt, entry_pt = _ports(src_box, dst_box)
    waypoint = _elbow(exit_pt, entry_pt)

    digest = hashlib.sha1(
        f"{source.get('id')}\x00{target.get('id')}".encode("utf-8")
    ).hexdigest()[:8]
    edge_id = f"edge-{digest}"

    base = M.flow_family_style(flow_family or "default", contract)
    style = _build_edge_style(base, exit_frac, entry_frac, line_style)

    edge_cell = edit.add_cell(
        doc,
        {"id": edge_id, "value": "", "style": style,
         "edge": "1", "parent": "1", "source": source.get("id"),
         "target": target.get("id")},
    )
    geo = ET.SubElement(edge_cell, "mxGeometry",
                        {"relative": "1", "as": "geometry"})
    arr = ET.SubElement(geo, "Array", {"as": "points"})
    wp = (float(edit.snap(waypoint[0])), float(edit.snap(waypoint[1])))
    ET.SubElement(arr, "mxPoint", {"x": _fmt(wp[0]), "y": _fmt(wp[1])})

    # Longest segment of the L → where the pill/label park.
    path = [exit_pt, wp, entry_pt]
    a, b = cr._longest_segment(path)
    seg_mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    horizontal = abs(a[0] - b[0]) >= abs(a[1] - b[1])

    net_sep = _separator_net_sep(doc)
    bands = cr._sep_obstacle_rects(net_sep) if net_sep else ()

    result = {"edge": edge_id, "pill": None, "label": None}

    if pill_text is not None:
        stub = type("_E", (), {"id": digest, "pill": pill_text,
                               "label": label_text, "kind": kind or ""})()
        pill_mol = M.pill(stub, contract)
        pw, ph = cr.pill_dims(pill_text)
        pill_id = f"pill-{digest}"
        _emit_slot_cell(doc, pill_id, pill_mol["value"], pill_mol["style"],
                        seg_mid, pw, ph, bands)
        result["pill"] = pill_id

    if label_text is not None:
        lw, lh = cr.label_dims(label_text)
        # Offset the label perpendicular to the segment so it never sits on the
        # pill (below a horizontal leg, right of a vertical one).
        if horizontal:
            lbl_center = (seg_mid[0], seg_mid[1] + cr.PILL_H / 2.0 + lh / 2.0 + 4.0)
        else:
            lbl_center = (seg_mid[0] + cr.pill_dims(pill_text or "")[0] / 2.0
                          + lw / 2.0 + 4.0, seg_mid[1])
        label_id = f"label-{digest}"
        _emit_slot_cell(doc, label_id, label_text, _LABEL_STYLE,
                        lbl_center, lw, lh, bands)
        result["label"] = label_id

    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("drawio", type=Path)
    ap.add_argument("--source", required=True, help="id of the source cell")
    ap.add_argument("--target", required=True, help="id of the target cell")
    ap.add_argument("--flowFamily", dest="flow_family",
                    help="identity|provisioning|master-data|transport|firewall|default")
    ap.add_argument("--kind", help="canonical pill kind (trust|authenticate|authorize|…)")
    ap.add_argument("--pill", help="protocol pill text (e.g. \"SAML2/OIDC\")")
    ap.add_argument("--label", help="edge label text")
    ap.add_argument("--style", dest="line_style",
                    choices=["solid", "dashed", "dotted", "thick"],
                    help="line modifier (default: the family molecule's own)")
    ap.add_argument("--json", action="store_true",
                    help="print {\"edge\":id,\"pill\":id|null,\"label\":id|null}")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if not args.drawio.exists():
        print(f"{args.drawio}: file not found", file=sys.stderr)
        return 2

    try:
        doc = edit.load(args.drawio)
    except (ET.ParseError, OSError) as exc:
        print(f"add-edge failed to parse {args.drawio}: {exc}", file=sys.stderr)
        return 1

    source = edit.find_cell(doc, args.source)
    if source is None:
        print(f"no cell with id {args.source!r} (source)", file=sys.stderr)
        return 2
    target = edit.find_cell(doc, args.target)
    if target is None:
        print(f"no cell with id {args.target!r} (target)", file=sys.stderr)
        return 2

    result = add_edge(
        doc, source, target,
        flow_family=args.flow_family, kind=args.kind,
        pill_text=args.pill, label_text=args.label, line_style=args.line_style,
    )

    try:
        edit.save(doc, args.drawio)
    except OSError as exc:
        print(f"add-edge failed to write {args.drawio}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result))
    else:
        extras = [k for k in ("pill", "label") if result[k]]
        suffix = f" (+{', '.join(extras)})" if extras else ""
        print(f"add-edge: {result['edge']} wired {args.source}→{args.target}"
              f"{suffix} in {args.drawio}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
