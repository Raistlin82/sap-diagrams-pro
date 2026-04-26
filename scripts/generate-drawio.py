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

# Line styles per the guideline.
EDGE_STYLES = {
    "solid": "endArrow=classic;startArrow=none;dashed=0;strokeWidth=1.5;",
    "dashed": "endArrow=classic;startArrow=none;dashed=1;dashPattern=8 4;strokeWidth=1.5;",
    "dotted": "endArrow=classic;startArrow=none;dashed=1;dashPattern=1 4;strokeWidth=1.5;",
    "thick": "endArrow=none;startArrow=none;dashed=0;strokeWidth=4;",
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
NODE_W = 160
NODE_H = 80
NODE_GAP_X = 24
NODE_GAP_Y = 24


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Group:
    id: str
    type: str
    label: str
    position: str = "center"
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


@dataclass
class Diagram:
    title: str
    level: str
    author: str
    groups: list[Group]
    nodes: list[Node]
    edges: list[Edge]


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

    edges: list[Edge] = []
    for e in raw_edges:
        style = e.get("style", "solid")
        if style not in EDGE_STYLES:
            raise ValueError(
                f"edge {e.get('id')!r}: style must be one of "
                f"{sorted(EDGE_STYLES)} (got {style!r})"
            )
        edges.append(
            Edge(
                id=e["id"],
                source=e["source"],
                target=e["target"],
                style=style,
                label=e.get("label", ""),
                direction=e.get("direction", "forward"),
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
    """Return mapping group_id → (x, y, w, h) on the 3×3 canvas grid.

    Groups sharing the same grid cell are stacked vertically inside the cell.
    """
    by_cell: dict[tuple[int, int], list[Group]] = {}
    for g in groups:
        cell = GRID_POSITIONS.get(g.position, (1, 1))
        by_cell.setdefault(cell, []).append(g)

    layout: dict[str, tuple[int, int, int, int]] = {}
    for (cx, cy), group_list in by_cell.items():
        share = max(1, len(group_list))
        cell_h = CELL_H // share
        for idx, g in enumerate(group_list):
            x = cx * CELL_W + GROUP_PADDING
            y = cy * CELL_H + idx * cell_h + GROUP_PADDING
            w = CELL_W - 2 * GROUP_PADDING
            h = cell_h - 2 * GROUP_PADDING
            layout[g.id] = (x, y, w, h)
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
def _group_style(g: Group) -> str:
    border, fill = GROUP_STYLES.get(g.type, GROUP_STYLES["btp-layer"])
    return (
        f"rounded=1;whiteSpace=wrap;html=1;"
        f"strokeColor={border};fillColor={fill};"
        f"arcSize=12;absoluteArcSize=1;strokeWidth=1.5;"
        f"verticalAlign=top;align=left;"
        f"fontColor={PALETTE['title']};fontStyle=1;fontSize=13;"
        f"spacingLeft=12;spacingTop=8;"
    )


def _node_style(n: Node) -> str:
    # Default styled box. For real generation, an icon URL/shape would replace
    # the basic style; we keep a clean placeholder here so the diagram is
    # readable even before the shape index resolves a true SAP icon.
    return (
        f"rounded=1;whiteSpace=wrap;html=1;"
        f"strokeColor={PALETTE['btp_border']};fillColor=#FFFFFF;"
        f"arcSize=8;absoluteArcSize=1;strokeWidth=1.5;"
        f"fontColor={PALETTE['title']};fontSize=11;align=center;verticalAlign=middle;"
    )


def _edge_style(e: Edge) -> str:
    base = EDGE_STYLES[e.style]
    if e.direction == "bidirectional":
        base = base.replace("startArrow=none", "startArrow=classic")
    elif e.direction == "none":
        base = base.replace("endArrow=classic", "endArrow=none")
    return f"{base}fontSize=10;fontColor={PALETTE['text']};"


def emit(diagram: Diagram) -> str:
    nodes_by_id = {n.id: n for n in diagram.nodes}
    group_geo = layout_groups(diagram.groups)

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
            "pageWidth": str(CANVAS_W),
            "pageHeight": str(CANVAS_H),
            "math": "0",
            "shadow": "0",
        },
    )
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", attrib={"id": "0"})
    ET.SubElement(root, "mxCell", attrib={"id": "1", "parent": "0"})

    # 1. Title
    title_id = _stable_id("title", diagram.title)
    title_cell = ET.SubElement(
        root,
        "mxCell",
        attrib={
            "id": title_id,
            "value": f"{diagram.title} [{diagram.level}]",
            "style": (
                f"text;html=1;align=left;verticalAlign=middle;"
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
            "x": "24",
            "y": "16",
            "width": "800",
            "height": "32",
            "as": "geometry",
        },
    )

    # 2. Groups
    for g in diagram.groups:
        if g.id not in group_geo:
            continue
        x, y, w, h = group_geo[g.id]
        g_cell = ET.SubElement(
            root,
            "mxCell",
            attrib={
                "id": _stable_id("g", g.id),
                "value": g.label,
                "style": _group_style(g),
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

    for n in diagram.nodes:
        x, y = node_xy[n.id]
        n_cell = ET.SubElement(
            root,
            "mxCell",
            attrib={
                "id": _stable_id("n", n.id),
                "value": n.label,
                "style": _node_style(n),
                "vertex": "1",
                "parent": "1",
            },
        )
        ET.SubElement(
            n_cell,
            "mxGeometry",
            attrib={
                "x": str(x),
                "y": str(y),
                "width": str(NODE_W),
                "height": str(NODE_H),
                "as": "geometry",
            },
        )

    # 4. Edges
    for e in diagram.edges:
        e_cell = ET.SubElement(
            root,
            "mxCell",
            attrib={
                "id": _stable_id("e", e.id),
                "value": e.label,
                "style": _edge_style(e),
                "edge": "1",
                "parent": "1",
                "source": _stable_id("n", e.source),
                "target": _stable_id("n", e.target),
            },
        )
        ET.SubElement(e_cell, "mxGeometry", attrib={"relative": "1", "as": "geometry"})

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
    args = parser.parse_args(argv)

    payload = _read_input(args.input)
    diagram = parse_json(payload)
    xml = emit(diagram)
    _write_output(xml, args.out)
    if args.out != "-":
        print(f"✅ Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
