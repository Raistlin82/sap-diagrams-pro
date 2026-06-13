#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""Deterministic zone-composition layout backend for sap-diagrams-pro.

Replaces the graphviz `dot` backend. SAP solution diagrams read along a
*horizontal* axis — consumers on the LEFT, the BTP platform in the CENTER, the
backing systems (SAP apps, on-prem, 3rd-party) on the RIGHT — with containers
that auto-size to their contents. `dot`'s topological `rankdir=TB` layout ignores
that composition (and the `position` field entirely); this engine honours it.

Algorithm (bottom-up):
  1. **measure** every group: pack its nodes (and nested sub-groups) and size the
     box to fit content + padding (top reserve for the label / BTP logo badge).
  2. **assign** each top-level group to a column (LEFT/CENTER/RIGHT) and band
     (TOP/MIDDLE/BOTTOM) from its ``type`` + ``position`` (+ optional ``zone``).
  3. **place** columns left→right with even gaps, vertically centring each column;
     reserve a top band for the title and a bottom band for the legend.

Returns the same shape the renderer expects from the old dot backend::

    {"groups": {gid:(x,y,w,h)}, "nodes": {nid:(x,y,w,h)},
     "edges": {eid:[(x,y)…]}, "canvas": (w,h)}

All coordinates are absolute (drawio top-left origin); the renderer converts to
parent-relative for nested cells. Pure-Python, no external process, fully
deterministic → byte-identical re-runs.
"""
from __future__ import annotations

import math
from typing import Any

# ── Sizing atoms (kept in sync with the renderer via icon_size()) ────────────
LABEL_H = 24          # vertical room reserved under an icon for its caption
CHAR_W = 6.6          # ~Helvetica advance at 12px, for label-width estimates
TEXT_MIN, TEXT_MAX = 44, 150

NODE_GAP = 34         # gap between nodes inside a leaf group (room for edge pills)
LANE_GAP = 36         # gap between sub-groups (lanes) inside a parent
ZONE_HGAP = 96        # horizontal gap between LEFT / CENTER / RIGHT columns
ZONE_VGAP = 52        # vertical gap between groups stacked in one column
MARGIN = 40           # canvas margin (left / right)
TOP_BAND = 64         # reserved for the diagram title
BOTTOM_BAND = 76      # reserved for the legend + "Diagram Level" caption

# Backend box molecule (RIGHT zone: SAP apps / on-prem / 3rd-party)
BOX_MIN_W, BOX_MAX_W = 156, 240
BOX_H, BOX_H_SUB = 60, 74

BACKEND_TYPES = {"sap-app", "non-sap", "third-party", "external"}


def icon_size(level: str) -> int:
    """Canonical square service/generic icon size for the given level."""
    return 32 if str(level).upper() == "L2" else 48


def _text_w(s: str) -> float:
    return min(TEXT_MAX, max(TEXT_MIN, len(s or "") * CHAR_W + 12))


def _node_is_icon(node, group_type: str, shape_index) -> bool:
    # Must agree with the renderer's _node_style: an icon is drawn only when a
    # genericIcon is set or the service resolves to a shape. (Phase 3 will give
    # user-zone nodes a default person icon in both places.)
    if getattr(node, "genericIcon", None):
        return True
    svc = (shape_index.resolve(getattr(node, "service", None))
           if (shape_index and getattr(node, "service", None)) else None)
    return bool(svc and svc.get("drawioStyle"))


def _footprint(node, group_type: str, level: str, shape_index) -> tuple[float, float, bool]:
    """Return (width, height, is_icon) the node occupies in layout space.

    Icon footprints reserve room for the caption underneath; backend boxes are
    sized to hold an icon + title (+ optional subtitle); plain boxes wrap a label.
    """
    icon = icon_size(level)
    is_icon = _node_is_icon(node, group_type, shape_index)
    label = getattr(node, "label", "") or ""

    if group_type in BACKEND_TYPES:
        # White rounded box with an icon on the left + title (+subtitle).
        w = min(BOX_MAX_W, max(BOX_MIN_W, _text_w(label) + icon + 30))
        h = BOX_H_SUB if getattr(node, "subtitle", None) else BOX_H
        return w, h, False  # rendered as a box, not a bare icon

    if is_icon:
        return max(icon, _text_w(label)), icon + LABEL_H, True

    # plain labelled box (no icon resolved)
    return min(BOX_MAX_W, max(132, _text_w(label) + 28)), 54, False


def _choose_cols(n: int) -> int:
    """Columns for a balanced, slightly-wide grid (SAP diagrams favour landscape)."""
    if n <= 1:
        return 1
    return max(1, math.ceil(math.sqrt(n * 1.6)))


def _pack(items: list[tuple[float, float]], mode: str, gap: float
          ) -> tuple[list[tuple[float, float]], float, float]:
    """Place item (w,h) boxes in 'row' | 'col' | 'grid'. Returns (positions,W,H)."""
    if not items:
        return [], 0.0, 0.0

    if mode == "row":
        row_h = max(h for _, h in items)
        pos, x = [], 0.0
        for w, h in items:
            pos.append((x, (row_h - h) / 2))
            x += w + gap
        return pos, x - gap, row_h

    if mode == "col":
        col_w = max(w for w, _ in items)
        pos, y = [], 0.0
        for w, h in items:
            pos.append(((col_w - w) / 2, y))
            y += h + gap
        return pos, col_w, y - gap

    # grid with per-column widths + per-row heights (tighter than uniform cells;
    # avoids narrow items sitting in a wide column)
    n = len(items)
    cols = _choose_cols(n)
    rows = math.ceil(n / cols)
    col_w = [0.0] * cols
    row_h = [0.0] * rows
    for i, (w, h) in enumerate(items):
        c, r = i % cols, i // cols
        col_w[c] = max(col_w[c], w)
        row_h[r] = max(row_h[r], h)
    col_x = [sum(col_w[:c]) + c * gap for c in range(cols)]
    row_y = [sum(row_h[:r]) + r * gap for r in range(rows)]
    pos = []
    for i, (w, h) in enumerate(items):
        c, r = i % cols, i // cols
        pos.append((col_x[c] + (col_w[c] - w) / 2, row_y[r] + (row_h[r] - h) / 2))
    return pos, sum(col_w) + (cols - 1) * gap, sum(row_h) + (rows - 1) * gap


def _padding(group_type: str, has_children: bool) -> tuple[float, float, float]:
    """(x, top, bottom) padding for a group box."""
    if group_type == "user":
        return 8.0, 26.0, 8.0            # frameless-ish: small label reserve
    if group_type == "btp-layer":
        return 18.0, (54.0 if has_children else 44.0), 18.0   # logo badge + lane labels
    return 16.0, 32.0, 14.0


def _leaf_mode(group, group_type: str, n: int) -> str:
    flow = getattr(group, "flow", None)
    if flow in ("row", "col", "grid"):
        return flow
    if group_type == "user" or group_type in BACKEND_TYPES:
        return "col"                      # stack people / backend boxes vertically
    return "row" if n <= 4 else "grid"    # BTP services: row when few, else grid


class _Meas:
    __slots__ = ("gid", "w", "h", "node_rel", "child_rel")

    def __init__(self, gid, w, h, node_rel, child_rel):
        self.gid = gid                    # group id (or None for synthetic)
        self.w, self.h = w, h
        self.node_rel = node_rel          # [(node, rx, ry, w, h)]
        self.child_rel = child_rel        # [(_Meas, rx, ry)]


def compute_layout(diagram, shape_index) -> dict[str, Any]:
    level = diagram.level
    groups_by_id = {g.id: g for g in diagram.groups}
    children_by_parent: dict[str, list] = {}
    for g in diagram.groups:
        if g.parent:
            children_by_parent.setdefault(g.parent, []).append(g)
    nodes_by_group: dict[str, list] = {}
    orphans: list = []
    for n in diagram.nodes:
        if n.group and n.group in groups_by_id:
            nodes_by_group.setdefault(n.group, []).append(n)
        else:
            orphans.append(n)

    # ---- measure ------------------------------------------------------------
    def measure(group) -> _Meas:
        gtype = group.type
        direct = nodes_by_group.get(group.id, [])
        children = children_by_parent.get(group.id, [])
        pad_x, pad_top, pad_bot = _padding(gtype, bool(children))
        node_fps = [(n, *_footprint(n, gtype, level, shape_index)[:2]) for n in direct]

        node_rel: list = []
        child_rel: list = []

        if children:
            child_meas = [measure(c) for c in children]
            items: list[tuple[float, float]] = []
            direct_pack = None
            if node_fps:
                dpos, dW, dH = _pack([(w, h) for _, w, h in node_fps],
                                     "row" if len(node_fps) <= 4 else "grid", NODE_GAP)
                direct_pack = (dpos, dW, dH)
                items.append((dW, dH))
            items += [(cm.w, cm.h) for cm in child_meas]
            mode = getattr(group, "flow", None) or ("row" if len(items) <= 3 else "grid")
            positions, CW, CH = _pack(items, mode, LANE_GAP)
            idx = 0
            if direct_pack:
                bx, by = positions[0]
                dpos, _, _ = direct_pack
                for (n, w, h), (rx, ry) in zip(node_fps, dpos):
                    node_rel.append((n, pad_x + bx + rx, pad_top + by + ry, w, h))
                idx = 1
            for cm, (cx, cy) in zip(child_meas, positions[idx:]):
                child_rel.append((cm, pad_x + cx, pad_top + cy))
            box_w, box_h = CW + 2 * pad_x, CH + pad_top + pad_bot
        else:
            mode = _leaf_mode(group, gtype, len(node_fps))
            positions, CW, CH = _pack([(w, h) for _, w, h in node_fps], mode, NODE_GAP)
            for (n, w, h), (rx, ry) in zip(node_fps, positions):
                node_rel.append((n, pad_x + rx, pad_top + ry, w, h))
            box_w, box_h = CW + 2 * pad_x, CH + pad_top + pad_bot

        return _Meas(group.id, box_w, box_h, node_rel, child_rel)

    top_level = [g for g in diagram.groups if not g.parent]
    measures = {g.id: measure(g) for g in top_level}

    # ---- assign columns & bands --------------------------------------------
    def column(g) -> str:
        z = getattr(g, "zone", None)
        if z in ("left", "center", "right"):
            return z
        p = (g.position or "").lower()
        if "left" in p:
            return "left"
        if "right" in p:
            return "right"
        if p in ("top", "top-center", "center", "middle", "bottom", "bottom-center"):
            return "center"
        return {"user": "left", "btp-layer": "center"}.get(g.type, "right")

    def band(g) -> int:
        p = (g.position or "").lower()
        if p.startswith("top"):
            return 0
        if p.startswith("bottom"):
            return 2
        return 1

    cols: dict[str, list] = {"left": [], "center": [], "right": []}
    for g in top_level:
        cols[column(g)].append(g)
    for c in cols:
        cols[c].sort(key=lambda g: (band(g), top_level.index(g)))

    col_w = {c: (max((measures[g.id].w for g in gs), default=0.0)) for c, gs in cols.items()}
    col_h = {c: (sum(measures[g.id].h for g in gs) + max(0, len(gs) - 1) * ZONE_VGAP)
             for c, gs in cols.items()}
    max_h = max(col_h.values()) if any(col_h.values()) else 0.0

    out_groups: dict[str, tuple[int, int, int, int]] = {}
    out_nodes: dict[str, tuple[int, int, int, int]] = {}

    def place(meas: _Meas, x: float, y: float) -> None:
        if meas.gid is not None:
            out_groups[meas.gid] = (int(round(x)), int(round(y)),
                                    int(round(meas.w)), int(round(meas.h)))
        for node, rx, ry, w, h in meas.node_rel:
            out_nodes[node.id] = (int(round(x + rx)), int(round(y + ry)),
                                  int(round(w)), int(round(h)))
        for cm, rx, ry in meas.child_rel:
            place(cm, x + rx, y + ry)

    cursor = float(MARGIN)
    for c in ("left", "center", "right"):
        gs = cols[c]
        if not gs:
            continue
        cw = col_w[c]
        y = TOP_BAND + (max_h - col_h[c]) / 2.0
        for g in gs:
            m = measures[g.id]
            place(m, cursor + (cw - m.w) / 2.0, y)
            y += m.h + ZONE_VGAP
        cursor += cw + ZONE_HGAP

    canvas_w = cursor - ZONE_HGAP + MARGIN if cursor > MARGIN else MARGIN * 2
    canvas_h = TOP_BAND + max_h + BOTTOM_BAND

    # ---- orphan nodes (no group): centred row just below the content --------
    if orphans:
        fps = [(_footprint(n, "default", level, shape_index)[:2]) for n in orphans]
        pos, W, H = _pack([(w, h) for w, h in fps], "row" if len(orphans) <= 5 else "grid", NODE_GAP)
        ox = max(MARGIN, (canvas_w - W) / 2.0)
        oy = TOP_BAND + max_h + 12
        for n, (px, py), (w, h) in zip(orphans, pos, fps):
            out_nodes[n.id] = (int(round(ox + px)), int(round(oy + py)),
                               int(round(w)), int(round(h)))
        canvas_h = max(canvas_h, int(oy + H + BOTTOM_BAND))

    return {
        "groups": out_groups,
        "nodes": out_nodes,
        "edges": {},                      # rely on exit/entry anchors; waypoints added later
        "canvas": (int(round(canvas_w)), int(round(canvas_h))),
    }
