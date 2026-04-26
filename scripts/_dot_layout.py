#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""Graphviz `dot` layout backend for sap-diagrams-pro.

Translates a ``Diagram`` into a DOT source, runs ``dot -Tjson``, and parses
the resulting layout into drawio coordinates (groups + nodes + edge
waypoints). Use as the primary layout engine when ``dot`` is on PATH; the
caller falls back to the greedy 3×3 grid otherwise.

Why dot:
  - cluster (subgraph) bounding boxes auto-size to their contents
  - splines=ortho produces L/U-shaped edge routes that hug shape edges
  - hierarchical layout with rankdir=TB matches the SAP "user → BTP →
    backend systems" reading order

Coordinate model:
  - dot's canvas origin is BOTTOM-LEFT, drawio's is TOP-LEFT → flip Y
  - dot uses points (72 DPI inches); we keep that 1:1 in drawio
  - we add ``PAD`` margin around the dot bbox to give the title and
    semantic legend (future) room to breathe
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any

PAD = 40  # extra px margin around dot's computed bbox

# Default node footprint (inches at 72 DPI). SAP icons render best as a
# 96×112 box (icon 64×64 + label below); plain boxes are wider and shorter.
ICON_W_IN = 1.4   # 100 px
ICON_H_IN = 1.3   # 94 px (icon + label below)
PLAIN_W_IN = 1.8  # 130 px
PLAIN_H_IN = 0.85 # 61 px


def _escape(s: str) -> str:
    """Escape a string for use inside a DOT quoted attribute value."""
    return (s or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _qid(s: str) -> str:
    """Always-quoted DOT identifier so kebab-case ids ('btp-core') are valid."""
    return '"' + (s or "").replace('"', '\\"') + '"'


def to_dot_source(diagram, shape_index) -> str:
    """Render a Diagram into a DOT source string.

    Each top-level group becomes a ``subgraph cluster_<id> { ... }``. Nested
    sub-groups become clusters inside their parent's subgraph. Nodes carry
    explicit width/height so dot can reserve space for both the SAP icon
    and the label below it.
    """
    lines = ["digraph SAPDiagram {"]
    lines.append("  rankdir=TB;")
    lines.append("  splines=ortho;")
    lines.append('  graph [pad="0.4", nodesep="0.5", ranksep="0.7", '
                 'compound=true, fontname="Helvetica"];')
    lines.append('  node [shape=box, fontname="Helvetica", fontsize=10, '
                 'fixedsize=true];')
    lines.append('  edge [fontname="Helvetica", fontsize=9];')
    lines.append("")

    # Build helpers.
    top_level = [g for g in diagram.groups if not g.parent]
    children_by_parent: dict[str, list] = {}
    for g in diagram.groups:
        if g.parent:
            children_by_parent.setdefault(g.parent, []).append(g)

    nodes_by_group: dict[str, list] = {}
    orphan_nodes = []
    for n in diagram.nodes:
        if n.group:
            nodes_by_group.setdefault(n.group, []).append(n)
        else:
            orphan_nodes.append(n)

    def _node_size(node) -> tuple[float, float]:
        svc = shape_index.resolve(node.service) if shape_index else None
        return (ICON_W_IN, ICON_H_IN) if svc and svc.get("drawioStyle") else (PLAIN_W_IN, PLAIN_H_IN)

    def _emit_node(node, indent: int) -> None:
        ind = " " * indent
        w, h = _node_size(node)
        lines.append(
            f'{ind}{_qid(node.id)} [label="{_escape(node.label)}", '
            f'width={w}, height={h}];'
        )

    def _emit_cluster(g, indent: int) -> None:
        ind = " " * indent
        # Cluster names need the "cluster_" prefix to be recognised by dot AND
        # must avoid '-' inside the bare identifier. Quote it.
        cluster_name = _qid(f"cluster_{g.id}")
        lines.append(f"{ind}subgraph {cluster_name} {{")
        lines.append(f'{ind}  label="{_escape(g.label)}";')
        lines.append(f'{ind}  labeljust="l";')
        lines.append(f'{ind}  fontsize=12;')
        lines.append(f'{ind}  style="rounded";')
        lines.append(f'{ind}  margin=12;')
        for child in children_by_parent.get(g.id, []):
            _emit_cluster(child, indent + 2)
        for n in nodes_by_group.get(g.id, []):
            _emit_node(n, indent + 2)
        lines.append(f"{ind}}}")

    for g in top_level:
        _emit_cluster(g, indent=2)

    for n in orphan_nodes:
        _emit_node(n, indent=2)

    lines.append("")
    # Edges — labels go on drawio's mxCell, NOT here (the splines=ortho
    # routing emits a warning about edge labels otherwise, and we render
    # cleaner labels through drawio's labelBackgroundColor anyway).
    for e in diagram.edges:
        lines.append(
            f"  {_qid(e.source)} -> {_qid(e.target)} [id=\"{_escape(e.id)}\"];"
        )
    lines.append("}")
    return "\n".join(lines)


def _parse_pos(pos: str) -> tuple[float, float]:
    """Parse 'x,y' from a dot pos attribute. Returns (0,0) on malformed."""
    if not pos or "," not in pos:
        return 0.0, 0.0
    try:
        x, y = pos.split(",", 1)
        return float(x), float(y)
    except ValueError:
        return 0.0, 0.0


def _parse_bb(bb: str) -> tuple[float, float, float, float]:
    """Parse 'x1,y1,x2,y2' from a dot bb attribute."""
    parts = (bb or "").split(",")
    if len(parts) != 4:
        return 0.0, 0.0, 0.0, 0.0
    try:
        return float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3])
    except ValueError:
        return 0.0, 0.0, 0.0, 0.0


def _parse_edge_pos(pos: str) -> list[tuple[float, float]]:
    """Parse a dot edge spline 'pos' string into raw (x,y) points.

    Format: ``e,end_x,end_y s,start_x,start_y x1,y1 x2,y2 ... xn,yn``.
    The ``e,`` and ``s,`` markers indicate end-arrow and start-arrow
    positions; we keep all intermediate points which are the actual
    spline waypoints.
    """
    if not pos:
        return []
    out: list[tuple[float, float]] = []
    for token in pos.split():
        # Markers like "e,123.45,67.89" or "s,...": strip the marker prefix
        # and keep the coordinates as part of the route.
        if token.startswith(("e,", "s,")):
            token = token[2:]
        if "," in token:
            try:
                x_str, y_str = token.split(",", 1)
                out.append((float(x_str), float(y_str)))
            except ValueError:
                continue
    return out


def compute_layout(diagram, shape_index, *, dot_binary: str = "dot") -> dict[str, Any] | None:
    """Run dot and return drawio-aligned layout, or None if unavailable."""
    if not shutil.which(dot_binary):
        return None

    src = to_dot_source(diagram, shape_index)
    try:
        result = subprocess.run(
            [dot_binary, "-Tjson"],
            input=src.encode("utf-8"),
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return None

    if result.returncode != 0:
        # dot syntax error or runtime failure — caller falls back to greedy.
        return None

    try:
        data = json.loads(result.stdout.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    try:
        return _convert(data, diagram)
    except Exception:
        # Best-effort: any conversion bug should fall back rather than crash.
        return None


def _convert(data: dict[str, Any], diagram) -> dict[str, Any]:
    """Translate dot's coordinate output into drawio coordinates."""
    canvas_x1, canvas_y1, canvas_x2, canvas_y2 = _parse_bb(data.get("bb", ""))
    canvas_w = canvas_x2 - canvas_x1
    canvas_h = canvas_y2 - canvas_y1

    def fy(y: float) -> float:
        """Flip dot's bottom-left Y into drawio's top-left Y."""
        return canvas_h - y + PAD

    def fx(x: float) -> float:
        return x - canvas_x1 + PAD

    objects = data.get("objects", []) or []

    groups: dict[str, tuple[int, int, int, int]] = {}
    nodes: dict[str, tuple[int, int, int, int]] = {}

    for obj in objects:
        name = obj.get("name", "")
        # When dot reads quoted identifiers, the name in JSON output keeps the
        # original string without quotes (e.g. "cluster_btp-core" or "btp-in").
        if name.startswith("cluster_"):
            group_id = name[len("cluster_"):]
            x1, y1, x2, y2 = _parse_bb(obj.get("bb", ""))
            x = fx(x1)
            y = fy(y2)  # top of cluster = max-y in dot coords
            w = x2 - x1
            h = y2 - y1
            groups[group_id] = (int(round(x)), int(round(y)), int(round(w)), int(round(h)))
        else:
            cx, cy = _parse_pos(obj.get("pos", ""))
            w_in = float(obj.get("width", 1.0))
            h_in = float(obj.get("height", 0.6))
            w_px = w_in * 72.0
            h_px = h_in * 72.0
            x = fx(cx) - w_px / 2.0
            y = fy(cy) - h_px / 2.0
            nodes[name] = (int(round(x)), int(round(y)), int(round(w_px)), int(round(h_px)))

    # Map edge index → diagram.edge.id by matching tail/head names.
    edge_waypoints: dict[str, list[tuple[float, float]]] = {}
    for edge_obj in data.get("edges", []) or []:
        tail_idx = edge_obj.get("tail")
        head_idx = edge_obj.get("head")
        if tail_idx is None or head_idx is None:
            continue
        if tail_idx >= len(objects) or head_idx >= len(objects):
            continue
        tail_name = objects[tail_idx].get("name", "")
        head_name = objects[head_idx].get("name", "")
        # Find the matching diagram edge.
        match = None
        for e in diagram.edges:
            if e.source == tail_name and e.target == head_name:
                match = e
                break
        if match is None:
            continue
        raw_pts = _parse_edge_pos(edge_obj.get("pos", ""))
        flipped = [(fx(px), fy(py)) for px, py in raw_pts]
        # First point is the END marker (post 'e,' prefix), last is the start.
        # Drop endpoints to keep only internal waypoints; drawio computes
        # the actual entry/exit on shape borders from exitX/entryX anchors.
        if len(flipped) >= 3:
            edge_waypoints[match.id] = flipped[1:-1]
        else:
            edge_waypoints[match.id] = []

    return {
        "canvas": (int(round(canvas_w + 2 * PAD)), int(round(canvas_h + 2 * PAD))),
        "groups": groups,
        "nodes": nodes,
        "edges": edge_waypoints,
    }
