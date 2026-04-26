#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""
generate-drawio.py — JSON intermediate → SAP-compliant .drawio XML.

Reads a JSON description of a SAP solution diagram (groups + nodes + edges +
metadata) and emits a deterministic .drawio file styled per the official SAP
BTP Solution Diagram Guideline (Horizon palette + atomic design).

Usage:
    python3 generate-drawio.py input.json --out diagram.drawio
    cat input.json | python3 generate-drawio.py - --out -    # stdin → stdout

JSON schema: see assets/shape-index.schema.json (companion repo).

Design notes:
- Layout is greedy and deterministic: groups are positioned based on their
  ``position`` field (top-left, top, top-right, left, center, right,
  bottom-left, bottom, bottom-right) on a 3×3 grid; nodes inside each group
  are auto-flowed in rows. This matches the visual conventions used in the 30
  reference architectures of SAP Architecture Center.
- Style strings encode the Horizon palette (BTP #0070F2/#EBF8FF, non-SAP
  #475E75/#F5F6F7) and the line-style conventions (solid=sync, dashed=async,
  dotted=optional, thick=firewall).
- IDs are stable: derived from the input ``id`` field with a short hash
  prefix. Re-generating the same JSON produces byte-identical XML.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Default location of the shape index built by build-shape-index.py.
DEFAULT_SHAPE_INDEX = (
    Path(__file__).resolve().parent.parent / "assets" / "shape-index.json"
)

# ─────────────────────────────────────────────────────────────────────────────
# Horizon palette (from btp-solution-diagrams/guideline/docs/btp_guideline/foundation.md)
# ─────────────────────────────────────────────────────────────────────────────
PALETTE = {
    "btp_border": "#0070F2",
    "btp_fill": "#EBF8FF",
    "non_sap_border": "#475E75",
    "non_sap_fill": "#F5F6F7",
    "title": "#1D2D3E",
    "text": "#556B82",
    "positive_border": "#188918",
    "positive_fill": "#F5FAE5",
    "critical_border": "#C35500",
    "critical_fill": "#FFF8D6",
    "negative_border": "#D20A0A",
    "negative_fill": "#FFEAF4",
    "accent_teal_border": "#07838F",
    "accent_teal_fill": "#DAFDF5",
    "accent_purple_border": "#5D36FF",
    "accent_purple_fill": "#F1ECFF",
    "accent_pink_border": "#CC00DC",
    "accent_pink_fill": "#FFF0FA",
}

# Group type → (border, fill) per atomic-design Organisms.
GROUP_STYLES = {
    "user": (PALETTE["non_sap_border"], "#FFFFFF"),
    "third-party": (PALETTE["non_sap_border"], PALETTE["non_sap_fill"]),
    "btp-layer": (PALETTE["btp_border"], PALETTE["btp_fill"]),
    "sap-app": (PALETTE["btp_border"], "#FFFFFF"),
    "non-sap": (PALETTE["non_sap_border"], PALETTE["non_sap_fill"]),
    "external": (PALETTE["non_sap_border"], PALETTE["non_sap_fill"]),
}

# Line styles match the SAP `connectors.xml` taxonomy:
#   direct (solid)   → synchronous data flow
#   indirect (dashed)→ asynchronous / event-driven
#   optional (dotted)→ conditional flow
#   firewall (thick) → boundary marker only
# Mandatory drawio attributes per the SAP convention observed across all 11
# editable example diagrams: bendable=1 (lets users tweak waypoints
# manually), rounded=0 (sharp orthogonal corners), endArrow=blockThin,
# endFill=1, endSize=4. orthogonalEdgeStyle is the most common routing
# (123/238 SAP edges sampled).
_EDGE_BASE = (
    "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;"
    "html=1;endArrow=blockThin;endFill=1;endSize=4;startSize=4;"
    "strokeColor={stroke};strokeWidth=1.5;bendable=1;startArrow=none;"
)
EDGE_STYLES = {
    "solid":  _EDGE_BASE + "dashed=0;",
    "dashed": _EDGE_BASE + "dashed=1;dashPattern=8 4;",
    "dotted": _EDGE_BASE + "dashed=1;dashPattern=1 4;",
    "thick":  _EDGE_BASE.replace("strokeWidth=1.5", "strokeWidth=3")
                        .replace("endArrow=blockThin", "endArrow=none")
                        .replace("endFill=1", "endFill=0")
                        + "dashed=0;",
}

# Layout grid for groups (3×3).
GRID_POSITIONS = {
    "top-left": (0, 0),
    "top": (1, 0),
    "top-center": (1, 0),
    "top-right": (2, 0),
    "left": (0, 1),
    "center": (1, 1),
    "middle": (1, 1),
    "right": (2, 1),
    "bottom-left": (0, 2),
    "bottom": (1, 2),
    "bottom-center": (1, 2),
    "bottom-right": (2, 2),
}

# Canvas geometry (matches SAP example diagrams).
CANVAS_W = 1600
CANVAS_H = 1000
CELL_W = CANVAS_W // 3
CELL_H = CANVAS_H // 3
GROUP_PADDING = 24
# Plain (no SAP icon) box sizing — used for users, non-SAP, unresolved services.
NODE_W = 160
NODE_H = 80
# SAP icon node sizing — square icon + label below it. Matches the SAP shape
# library style `verticalLabelPosition=bottom;verticalAlign=top;`.
ICON_W = 80
ICON_H = 80
NODE_GAP_X = 24
NODE_GAP_Y = 36  # extra vertical room because labels sit below SAP icons


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Group:
    id: str
    type: str
    label: str
    position: str = "center"
    parent: str | None = None  # id of parent group; None = top-level (3x3 grid)
    nodes: list[str] = field(default_factory=list)


@dataclass
class Node:
    id: str
    label: str
    service: str | None = None
    icon: str | None = None
    group: str | None = None
    tier: str | None = None


@dataclass
class Edge:
    id: str
    source: str
    target: str
    style: str = "solid"
    label: str = ""
    direction: str = "forward"  # forward | bidirectional | none
    # Semantic edge type for SAP-canonical highlights:
    #   "default"   — standard non-SAP grey #475E75 line + plain label
    #   "trust"     — pink #CC00DC bidirectional + pill label (IAS-XSUAA / IDP)
    #   "positive"  — green  #188918 (success/certified flow)
    #   "critical"  — orange #C35500 (degraded/at-risk flow)
    #   "negative"  — red    #D20A0A (failed/deprecated flow)
    kind: str = "default"


@dataclass
class Diagram:
    title: str
    level: str
    author: str
    groups: list[Group]
    nodes: list[Node]
    edges: list[Edge]


# ─────────────────────────────────────────────────────────────────────────────
# Shape index — maps service names → SAP icon style strings
# ─────────────────────────────────────────────────────────────────────────────
class ShapeIndex:
    """Wrap shape-index.json and provide fast service-name resolution."""

    def __init__(self, services: list[dict[str, Any]]):
        self._by_name: dict[str, dict[str, Any]] = {}
        self._by_alias: dict[str, dict[str, Any]] = {}
        self._by_techid: dict[str, dict[str, Any]] = {}
        for s in services:
            name = s.get("name", "")
            tech = s.get("techId", "")
            size = s.get("size", "M")
            # Prefer M-size for the canonical lookup — resilient default.
            existing = self._by_name.get(name)
            if not existing or (existing.get("size") != "M" and size == "M"):
                self._by_name[name] = s
            if tech:
                existing = self._by_techid.get(tech)
                if not existing or (existing.get("size") != "M" and size == "M"):
                    self._by_techid[tech] = s
            for alias in s.get("aliases", []) or []:
                existing = self._by_alias.get(alias.lower())
                if not existing or (existing.get("size") != "M" and size == "M"):
                    self._by_alias[alias.lower()] = s

    @classmethod
    def load(cls, path: Path | None = None) -> "ShapeIndex":
        """Load from JSON. Returns an empty index if the file is missing."""
        target = path or DEFAULT_SHAPE_INDEX
        if not target.exists():
            return cls([])
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls([])
        return cls(data.get("services", []))

    def resolve(self, query: str | None) -> dict[str, Any] | None:
        """Lookup priority: exact name → exact alias (case-insensitive) →
        exact techId → fuzzy substring on name. Returns None on miss."""
        if not query:
            return None
        if query in self._by_name:
            return self._by_name[query]
        if query.lower() in self._by_alias:
            return self._by_alias[query.lower()]
        if query in self._by_techid:
            return self._by_techid[query]
        # Fuzzy: case-insensitive substring on canonical names.
        ql = query.lower()
        for name, svc in self._by_name.items():
            if ql in name.lower() or name.lower() in ql:
                return svc
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────────────────────
def _validate_level(level: str) -> str:
    level = level.upper().strip()
    if level not in {"L0", "L1", "L2"}:
        raise ValueError(f"level must be L0, L1 or L2 (got {level!r})")
    return level


def parse_json(payload: dict[str, Any]) -> Diagram:
    meta = payload.get("metadata", {})
    title = meta.get("title", "Untitled SAP Diagram")
    level = _validate_level(meta.get("level", "L1"))
    author = meta.get("author", "")

    raw_groups = payload.get("groups", [])
    raw_nodes = payload.get("nodes", [])
    raw_edges = payload.get("edges", [])

    # Build group → nodes membership.
    group_map: dict[str, Group] = {}
    for g in raw_groups:
        gid = g["id"]
        group_map[gid] = Group(
            id=gid,
            type=g.get("type", "btp-layer"),
            label=g.get("label", gid),
            position=g.get("position", "center"),
            parent=g.get("parent"),
            nodes=[],
        )

    nodes: list[Node] = []
    for n in raw_nodes:
        node = Node(
            id=n["id"],
            label=n.get("label", n["id"]),
            service=n.get("service"),
            icon=n.get("icon"),
            group=n.get("group"),
            tier=n.get("tier"),
        )
        nodes.append(node)
        if node.group and node.group in group_map:
            group_map[node.group].nodes.append(node.id)

    valid_kinds = {
        "default", "trust", "positive", "critical", "negative",
        "authenticate", "authorize", "generic_protocol",
    }
    edges: list[Edge] = []
    for e in raw_edges:
        style = e.get("style", "solid")
        if style not in EDGE_STYLES:
            raise ValueError(
                f"edge {e.get('id')!r}: style must be one of "
                f"{sorted(EDGE_STYLES)} (got {style!r})"
            )
        kind = e.get("kind", "default")
        if kind not in valid_kinds:
            raise ValueError(
                f"edge {e.get('id')!r}: kind must be one of "
                f"{sorted(valid_kinds)} (got {kind!r})"
            )
        edges.append(
            Edge(
                id=e["id"],
                source=e["source"],
                target=e["target"],
                style=style,
                label=e.get("label", ""),
                direction=e.get("direction", "forward"),
                kind=kind,
            )
        )

    return Diagram(
        title=title,
        level=level,
        author=author,
        groups=list(group_map.values()),
        nodes=nodes,
        edges=edges,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Layout
# ─────────────────────────────────────────────────────────────────────────────
def _stable_id(prefix: str, key: str) -> str:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}-{digest}"


def layout_groups(groups: list[Group]) -> dict[str, tuple[int, int, int, int]]:
    """Two-pass layout: top-level groups on 3×3 canvas grid, nested
    sub-groups inside their parent's geometry.

    Pass 1 — top-level (parent=None): groups sharing the same grid cell stack
    vertically inside the cell.

    Pass 2 — nested (parent set): children flow inside parent's inner area.
      • 1-2 children → horizontal split.
      • 3 children   → horizontal split (matches the SAP "Inbound | Core |
                        Outbound" convention).
      • 4 children   → 2×2 grid.
      • 5+ children  → 2-column grid (rows = ceil(n/2)).
    """
    layout: dict[str, tuple[int, int, int, int]] = {}

    top_level = [g for g in groups if not g.parent]
    children_by_parent: dict[str, list[Group]] = {}
    for g in groups:
        if g.parent:
            children_by_parent.setdefault(g.parent, []).append(g)

    # Pass 1 — top-level on 3x3 grid.
    by_cell: dict[tuple[int, int], list[Group]] = {}
    for g in top_level:
        cell = GRID_POSITIONS.get(g.position, (1, 1))
        by_cell.setdefault(cell, []).append(g)

    for (cx, cy), group_list in by_cell.items():
        share = max(1, len(group_list))
        cell_h = CELL_H // share
        for idx, g in enumerate(group_list):
            x = cx * CELL_W + GROUP_PADDING
            y = cy * CELL_H + idx * cell_h + GROUP_PADDING
            w = CELL_W - 2 * GROUP_PADDING
            h = cell_h - 2 * GROUP_PADDING
            layout[g.id] = (x, y, w, h)

    # Pass 2 — nested sub-groups inside their parent.
    NESTED_INNER_PAD = 8
    NESTED_LABEL_RESERVE = 32  # space at top for parent's own label

    for parent_id, children in children_by_parent.items():
        if parent_id not in layout:
            # Orphan child (parent missing) — place in center cell as fallback.
            cx, cy = (1 * CELL_W) + GROUP_PADDING, (1 * CELL_H) + GROUP_PADDING
            layout[children[0].id] = (cx, cy, 200, 100)
            continue
        px, py, pw, ph = layout[parent_id]
        n = len(children)
        inner_x = px + NESTED_INNER_PAD
        inner_y = py + NESTED_LABEL_RESERVE
        inner_w = pw - 2 * NESTED_INNER_PAD
        inner_h = ph - NESTED_LABEL_RESERVE - NESTED_INNER_PAD

        if n <= 3:
            # Horizontal split (Inbound | Core | Outbound pattern).
            cols, rows = n, 1
        elif n == 4:
            cols, rows = 2, 2
        else:
            cols, rows = 2, (n + 1) // 2

        cell_w = (inner_w - (cols - 1) * NESTED_INNER_PAD) // cols
        cell_h = (inner_h - (rows - 1) * NESTED_INNER_PAD) // rows
        for idx, child in enumerate(children):
            col = idx % cols
            row = idx // cols
            cx = inner_x + col * (cell_w + NESTED_INNER_PAD)
            cy = inner_y + row * (cell_h + NESTED_INNER_PAD)
            layout[child.id] = (cx, cy, cell_w, cell_h)

    return layout


def layout_nodes(
    group: Group,
    group_geo: tuple[int, int, int, int],
    nodes_by_id: dict[str, Node],
) -> dict[str, tuple[int, int]]:
    """Flow nodes inside a group in rows, return id → (x, y)."""
    gx, gy, gw, gh = group_geo
    inner_x = gx + 16
    inner_y = gy + 32  # space for group title
    cols = max(1, (gw - 16) // (NODE_W + NODE_GAP_X))
    out: dict[str, tuple[int, int]] = {}
    for idx, nid in enumerate(group.nodes):
        if nid not in nodes_by_id:
            continue
        col = idx % cols
        row = idx // cols
        x = inner_x + col * (NODE_W + NODE_GAP_X)
        y = inner_y + row * (NODE_H + NODE_GAP_Y)
        out[nid] = (x, y)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# XML emission
# ─────────────────────────────────────────────────────────────────────────────
def _group_style(g: Group, is_nested: bool = False) -> str:
    border, fill = GROUP_STYLES.get(g.type, GROUP_STYLES["btp-layer"])
    if is_nested:
        # Sub-group inside a parent organism: keep the same border for visual
        # cohesion, but use white fill so the parent's #EBF8FF reads as the
        # "BTP territory" and the sub-groups read as lanes inside it.
        fill = "#FFFFFF"
        font_size = 11  # smaller than the parent's 13
    else:
        font_size = 13
    return (
        f"rounded=1;whiteSpace=wrap;html=1;"
        f"strokeColor={border};fillColor={fill};"
        f"arcSize=12;absoluteArcSize=1;strokeWidth=1.5;"
        f"verticalAlign=top;align=left;"
        f"fontColor={PALETTE['title']};fontStyle=1;fontSize={font_size};"
        f"spacingLeft=12;spacingTop=6;"
    )


def _node_style(n: Node, shape_index: "ShapeIndex") -> tuple[str, bool, str]:
    """Return (style_string, is_sap_icon, display_label).

    When the node's ``service`` resolves in the shape index, we use the SAP
    icon's drawioStyle directly (an SVG inline base64 image). The display
    label becomes the SAP canonical name unless the JSON intermediate
    explicitly overrides it via Node.label.
    """
    svc = shape_index.resolve(n.service)
    if svc and svc.get("drawioStyle"):
        # SAP icon found — use the official style.
        canonical = svc.get("name") or n.label
        # Prefer user-provided label; fall back to canonical SAP name.
        label = n.label if n.label and n.label != n.id else canonical
        return svc["drawioStyle"], True, label
    # Fallback: clean styled box that respects the Horizon palette.
    style = (
        f"rounded=1;whiteSpace=wrap;html=1;"
        f"strokeColor={PALETTE['btp_border']};fillColor=#FFFFFF;"
        f"arcSize=8;absoluteArcSize=1;strokeWidth=1.5;"
        f"fontColor={PALETTE['title']};fontSize=11;align=center;verticalAlign=middle;"
    )
    return style, False, n.label


# Edge kind colours sourced verbatim from
# btp-solution-diagrams/assets/.../annotations_and_interfaces.xml so the
# rendered pills match the SAP shape library output pixel-for-pixel.
_EDGE_KIND_STROKE = {
    "default":          PALETTE["non_sap_border"],     # #475E75
    "trust":            PALETTE["accent_pink_border"], # #CC00DC
    "positive":         PALETTE["positive_border"],    # #188918
    "critical":         PALETTE["critical_border"],    # #C35500
    "negative":         PALETTE["negative_border"],    # #D20A0A
    "authenticate":     "#188918",                      # SAP green pill
    "authorize":        "#470bed",                      # SAP purple pill
    "generic_protocol": "#475f75",                      # SAP grey pill
}

# Per-kind pill (label) styling. Empty string = no pill, use inline label.
_EDGE_KIND_PILL = {
    "trust": {
        "stroke": "#CC00DC",
        "fill":   "#fff0fa",
        "fontColor": "#CC00DC",
    },
    "authenticate": {
        "stroke": "#188918",
        "fill":   "#f5fae5",
        "fontColor": "#188918",
    },
    "authorize": {
        "stroke": "#470bed",
        "fill":   "#f1edff",
        "fontColor": "#470bed",
    },
    "generic_protocol": {
        "stroke": "#475f75",
        "fill":   "#f5f6f7",
        "fontColor": "#1D2D3E",
    },
}


def _edge_style(
    e: Edge,
    src_geom: tuple[int, int, int, int] | None = None,
    tgt_geom: tuple[int, int, int, int] | None = None,
) -> str:
    """Build the SAP-canonical edge style.

    Stroke colour comes from the edge's ``kind`` (default grey, trust pink,
    semantic green/orange/red). Trust edges are also automatically rendered
    as bidirectional because trust is a symmetric relationship in SAP IAM
    diagrams.
    """
    stroke = _EDGE_KIND_STROKE.get(e.kind, PALETTE["non_sap_border"])
    base = EDGE_STYLES[e.style].format(stroke=stroke)
    direction = e.direction
    # Trust is canonically a two-way relationship (IAS ↔ XSUAA / IDP).
    # Authenticate and authorize are one-directional by SAP convention.
    if e.kind == "trust" and direction == "forward":
        direction = "bidirectional"
    if direction == "bidirectional":
        base += "startArrow=blockThin;startFill=1;"
    elif direction == "none":
        base = base.replace("endArrow=blockThin", "endArrow=none").replace("endFill=1", "endFill=0")
    # labelBackgroundColor with explicit padding hides the line behind the
    # text — without this, edge labels sit on top of the route and become
    # unreadable when the route passes over another shape.
    style = (
        f"{base}fontSize=10;fontColor={PALETTE['text']};"
        f"labelBackgroundColor=#FFFFFF;labelBorderColor=none;"
        f"verticalAlign=middle;align=center;"
    )

    # Compute exit/entry anchors when both endpoints' geometries are known.
    if src_geom and tgt_geom:
        exit_anchor, entry_anchor = _compute_anchors(src_geom, tgt_geom)
        style += (
            f"exitX={exit_anchor[0]};exitY={exit_anchor[1]};exitDx=0;exitDy=0;"
            f"entryX={entry_anchor[0]};entryY={entry_anchor[1]};entryDx=0;entryDy=0;"
        )
    return style


def _compute_anchors(
    src: tuple[int, int, int, int],
    tgt: tuple[int, int, int, int],
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Pick the side of source and target where the edge attaches.

    Anchors are expressed in drawio's relative-to-shape units (0..1).
    Strategy: choose the dominant axis between centres, then attach to the
    facing midpoint. Result: orthogonal L/U routes that hug shape edges.
    """
    sx, sy, sw, sh = src
    tx, ty, tw, th = tgt
    src_cx, src_cy = sx + sw / 2, sy + sh / 2
    tgt_cx, tgt_cy = tx + tw / 2, ty + th / 2
    dx = tgt_cx - src_cx
    dy = tgt_cy - src_cy

    if abs(dx) >= abs(dy):
        # Horizontal dominance.
        if dx >= 0:
            return (1.0, 0.5), (0.0, 0.5)  # source-right → target-left
        return (0.0, 0.5), (1.0, 0.5)      # source-left  → target-right
    # Vertical dominance.
    if dy >= 0:
        return (0.5, 1.0), (0.5, 0.0)      # source-bottom → target-top
    return (0.5, 0.0), (0.5, 1.0)          # source-top    → target-bottom


def emit(
    diagram: Diagram,
    shape_index: "ShapeIndex | None" = None,
    layout: str = "auto",
) -> str:
    """Render the diagram to .drawio XML.

    ``layout`` selects the positioning backend:
      - ``"auto"``   — try graphviz dot, fall back to greedy if dot is missing
      - ``"dot"``    — require dot; raise SystemExit if unavailable
      - ``"greedy"`` — force the built-in 3×3 grid layout
    """
    if shape_index is None:
        shape_index = ShapeIndex.load()
    nodes_by_id = {n.id: n for n in diagram.nodes}

    dot_result = None
    if layout in ("auto", "dot"):
        # Load _dot_layout via importlib so the script works regardless of
        # whether scripts/ is on sys.path.
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "_dot_layout", Path(__file__).resolve().parent / "_dot_layout.py"
        )
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _dot_compute_layout = _mod.compute_layout
        dot_result = _dot_compute_layout(diagram, shape_index)
        if dot_result is None and layout == "dot":
            raise SystemExit(
                "ERROR: --layout dot requested but graphviz `dot` is not "
                "available, returned an error, or produced unparseable JSON. "
                "Install with `brew install graphviz` (macOS) or "
                "`apt install graphviz` (Debian/Ubuntu)."
            )

    if dot_result:
        group_geo = dot_result["groups"]
        node_geo = dot_result["nodes"]
        edge_waypoints: dict[str, list[tuple[float, float]]] = dot_result["edges"]
        canvas_w, canvas_h = dot_result["canvas"]
    else:
        group_geo = layout_groups(diagram.groups)
        node_geo = {}
        edge_waypoints = {}
        canvas_w, canvas_h = CANVAS_W, CANVAS_H

    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    mxfile = ET.Element(
        "mxfile",
        attrib={
            "host": "sap-diagrams-pro",
            "modified": timestamp,
            "agent": "sap-diagrams-pro/0.1.0",
            "version": "24.7.8",
        },
    )
    diagram_el = ET.SubElement(
        mxfile,
        "diagram",
        attrib={"id": _stable_id("d", diagram.title), "name": diagram.title},
    )
    model = ET.SubElement(
        diagram_el,
        "mxGraphModel",
        attrib={
            "dx": "1422",
            "dy": "754",
            "grid": "1",
            "gridSize": "10",
            "guides": "1",
            "tooltips": "1",
            "connect": "1",
            "arrows": "1",
            "fold": "1",
            "page": "1",
            "pageScale": "1",
            "pageWidth": str(canvas_w),
            "pageHeight": str(canvas_h),
            "math": "0",
            "shadow": "0",
        },
    )
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", attrib={"id": "0"})
    ET.SubElement(root, "mxCell", attrib={"id": "1", "parent": "0"})

    # 1. Title — full-width centered band at the top of the canvas, leaving
    # margin on both sides. Avoids the empty-top-left corner you'd otherwise
    # see when content shifts toward the centre under dot layout.
    title_id = _stable_id("title", diagram.title)
    title_w = max(canvas_w - 96, 600)
    title_cell = ET.SubElement(
        root,
        "mxCell",
        attrib={
            "id": title_id,
            "value": f"{diagram.title} [{diagram.level}]",
            "style": (
                f"text;html=1;align=center;verticalAlign=middle;"
                f"fontColor={PALETTE['title']};fontSize=18;fontStyle=1;"
            ),
            "vertex": "1",
            "parent": "1",
        },
    )
    ET.SubElement(
        title_cell,
        "mxGeometry",
        attrib={
            "x": "48",
            "y": "12",
            "width": str(title_w),
            "height": "32",
            "as": "geometry",
        },
    )

    # 2. Groups — top-level FIRST (so they sit behind sub-groups in z-order),
    #    then nested sub-groups on top. Use real drawio parenting: a nested
    #    sub-group's mxCell parent is its top-level parent's cell id, and its
    #    geometry is RELATIVE to that parent's origin.
    top_level_groups = [g for g in diagram.groups if not g.parent]
    nested_groups = [g for g in diagram.groups if g.parent]

    # Map group_id → emitted mxCell id (so children can reference it).
    group_cell_ids: dict[str, str] = {}

    for g in top_level_groups:
        if g.id not in group_geo:
            continue
        x, y, w, h = group_geo[g.id]
        cell_id = _stable_id("g", g.id)
        group_cell_ids[g.id] = cell_id
        g_cell = ET.SubElement(
            root,
            "mxCell",
            attrib={
                "id": cell_id,
                "value": g.label,
                "style": _group_style(g, is_nested=False),
                "vertex": "1",
                "parent": "1",
            },
        )
        ET.SubElement(
            g_cell,
            "mxGeometry",
            attrib={
                "x": str(x),
                "y": str(y),
                "width": str(w),
                "height": str(h),
                "as": "geometry",
            },
        )

    for g in nested_groups:
        if g.id not in group_geo or g.parent not in group_geo:
            continue
        x, y, w, h = group_geo[g.id]
        px, py, _, _ = group_geo[g.parent]
        rel_x, rel_y = x - px, y - py
        cell_id = _stable_id("g", g.id)
        group_cell_ids[g.id] = cell_id
        parent_cell_id = group_cell_ids.get(g.parent, "1")
        g_cell = ET.SubElement(
            root,
            "mxCell",
            attrib={
                "id": cell_id,
                "value": g.label,
                "style": _group_style(g, is_nested=True),
                "vertex": "1",
                "parent": parent_cell_id,
            },
        )
        ET.SubElement(
            g_cell,
            "mxGeometry",
            attrib={
                "x": str(rel_x),
                "y": str(rel_y),
                "width": str(w),
                "height": str(h),
                "as": "geometry",
            },
        )

    # 3. Nodes (parented to "1", positioned absolutely; group is visual only)
    node_xy: dict[str, tuple[int, int]] = {}
    for g in diagram.groups:
        if g.id not in group_geo:
            continue
        node_xy.update(layout_nodes(g, group_geo[g.id], nodes_by_id))

    # Orphan nodes (no group): drop in center cell.
    orphans = [n for n in diagram.nodes if n.id not in node_xy]
    cx, cy = (1 * CELL_W) + GROUP_PADDING, (1 * CELL_H) + GROUP_PADDING + 32
    for idx, n in enumerate(orphans):
        node_xy[n.id] = (cx + (idx % 4) * (NODE_W + NODE_GAP_X), cy + (idx // 4) * (NODE_H + NODE_GAP_Y))

    # Track each node's absolute geometry so edges can compute anchor points
    # based on their endpoints' relative positions.
    node_abs_geom: dict[str, tuple[int, int, int, int]] = {}

    for n in diagram.nodes:
        style, is_icon, label = _node_style(n, shape_index)
        # When dot layout is in effect, node_geo provides exact positions and
        # sizes. Otherwise fall back to the greedy node_xy + ICON/PLAIN
        # default sizes.
        if n.id in node_geo:
            x, y, w, h = node_geo[n.id]
        else:
            x, y = node_xy.get(n.id, (0, 0))
            w, h = (ICON_W, ICON_H) if is_icon else (NODE_W, NODE_H)
        node_abs_geom[n.id] = (x, y, w, h)

        # Real drawio parenting: if the node belongs to a (sub-)group, parent
        # the mxCell to that group cell and emit relative coordinates.
        parent_cell_id = "1"
        rel_x, rel_y = x, y
        if n.group and n.group in group_cell_ids and n.group in group_geo:
            parent_cell_id = group_cell_ids[n.group]
            gx, gy, _, _ = group_geo[n.group]
            rel_x, rel_y = x - gx, y - gy

        n_cell = ET.SubElement(
            root,
            "mxCell",
            attrib={
                "id": _stable_id("n", n.id),
                "value": label,
                "style": style,
                "vertex": "1",
                "parent": parent_cell_id,
            },
        )
        ET.SubElement(
            n_cell,
            "mxGeometry",
            attrib={
                "x": str(rel_x),
                "y": str(rel_y),
                "width": str(w),
                "height": str(h),
                "as": "geometry",
            },
        )

    # 4. Edges — exit/entry anchors + optional waypoints from dot.
    for e in diagram.edges:
        src_geom = node_abs_geom.get(e.source)
        tgt_geom = node_abs_geom.get(e.target)
        edge_id = _stable_id("e", e.id)
        # For pill-rendered kinds (trust, authenticate, authorize,
        # generic_protocol), the visible label sits in a separate rounded
        # vertex child (drawio does NOT honour arcSize on inline labels).
        # The edge cell itself carries an empty value so the label isn't
        # duplicated.
        has_pill = e.kind in _EDGE_KIND_PILL
        inline_value = "" if has_pill else e.label
        e_cell = ET.SubElement(
            root,
            "mxCell",
            attrib={
                "id": edge_id,
                "value": inline_value,
                "style": _edge_style(e, src_geom, tgt_geom),
                "edge": "1",
                "parent": "1",
                "source": _stable_id("n", e.source),
                "target": _stable_id("n", e.target),
            },
        )
        geom_el = ET.SubElement(
            e_cell, "mxGeometry", attrib={"relative": "1", "as": "geometry"}
        )
        # When dot has computed waypoints for this edge, embed them so the
        # final route follows the SAP-style L/U paths instead of straight
        # diagonals.
        wps = edge_waypoints.get(e.id, [])
        if wps:
            arr = ET.SubElement(geom_el, "Array", attrib={"as": "points"})
            for wx, wy in wps:
                ET.SubElement(
                    arr,
                    "mxPoint",
                    attrib={"x": str(int(round(wx))), "y": str(int(round(wy)))},
                )

        # Pill labels for the 4 SAP-canonical edge kinds (trust,
        # authenticate, authorize, generic_protocol). Each kind has its
        # own stroke + fill + fontColor sourced from the SAP
        # annotations_and_interfaces.xml library. The pill's geometry uses
        # relative=1 with offset so it centres on the edge's midpoint
        # regardless of the edge's actual length or routing.
        if has_pill and e.label:
            pill_def = _EDGE_KIND_PILL[e.kind]
            # Width adapts to label length (rough heuristic: ~6.5 px/char +
            # padding) so longer labels like "Generic Protocol" don't get
            # clipped, while short ones like "Trust" stay tight.
            pill_w = max(64, min(180, len(e.label) * 7 + 24))
            pill = ET.SubElement(
                root,
                "mxCell",
                attrib={
                    "id": _stable_id("p", e.id),
                    "value": e.label,
                    "style": (
                        f"rounded=1;whiteSpace=wrap;html=1;arcSize=50;"
                        f"strokeColor={pill_def['stroke']};"
                        f"fillColor={pill_def['fill']};"
                        f"fontColor={pill_def['fontColor']};"
                        f"fontStyle=1;strokeWidth=1.5;fontSize=11;"
                        f"align=center;verticalAlign=middle;"
                    ),
                    "vertex": "1",
                    "parent": edge_id,
                    "connectable": "0",
                },
            )
            pill_geom = ET.SubElement(
                pill,
                "mxGeometry",
                attrib={"width": str(pill_w), "height": "26", "relative": "1", "as": "geometry"},
            )
            ET.SubElement(
                pill_geom,
                "mxPoint",
                attrib={"x": str(-pill_w // 2), "y": "-13", "as": "offset"},
            )

    return _serialize(mxfile)


def _serialize(root: ET.Element) -> str:
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n'


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def _read_input(path: str) -> dict[str, Any]:
    if path == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_output(content: str, path: str) -> None:
    if path == "-":
        sys.stdout.write(content)
    else:
        Path(path).write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a SAP-compliant .drawio file from a JSON intermediate representation."
    )
    parser.add_argument("input", help="Path to JSON input ('-' for stdin).")
    parser.add_argument(
        "--out",
        default="-",
        help="Path to .drawio output ('-' for stdout, default).",
    )
    parser.add_argument(
        "--layout",
        choices=("auto", "dot", "greedy"),
        default="auto",
        help=(
            "Layout backend. 'auto' uses graphviz dot if available else "
            "the built-in greedy 3x3 grid. 'dot' requires graphviz. "
            "'greedy' forces the built-in layout."
        ),
    )
    args = parser.parse_args(argv)

    payload = _read_input(args.input)
    diagram = parse_json(payload)
    xml = emit(diagram, layout=args.layout)
    _write_output(xml, args.out)
    if args.out != "-":
        print(f"✅ Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
