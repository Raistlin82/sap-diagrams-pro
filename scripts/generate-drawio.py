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
DEFAULT_CANONICAL_PILLS = (
    Path(__file__).resolve().parent.parent / "assets" / "canonical-pills.json"
)


def _load_canonical_pills() -> dict:
    """Load the catalog of 42 SAP-canonical pill labels harvested from the
    138 official .drawio files (btp-solution-diagrams + architecture-center).

    Each entry maps a label (e.g. "SAML2/OIDC", "Group", "OIDC", "ORD",
    "Health Check", "REST/SPI", "Identity Lifecycle", "Business Role")
    to its canonical colour family (green/grey/purple/pink/teal/blue) and
    exact stroke + fill hex values used in the SAP samples. This lets
    authors write `kind: "annotation", label: "Group"` and have the colour
    auto-resolve to purple #5D36FF / fill #F1ECFF, the way SAP actually
    renders it.
    """
    if not DEFAULT_CANONICAL_PILLS.exists():
        return {}
    try:
        return json.loads(DEFAULT_CANONICAL_PILLS.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


_CANONICAL_PILLS = _load_canonical_pills()

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
# A4 landscape — most-common canvas size across SAP samples (46/138 files).
# Used as the greedy-layout fallback canvas. dot layout still computes its
# own bbox; this only matters when --layout greedy is forced.
CANVAS_W = 1169
CANVAS_H = 827

# SAP uses the proprietary "72 Brand" font in their assets. We declare the
# font family chain so drawio falls back gracefully on machines without it.
SAP_FONT_FAMILY = "72,Helvetica,Arial,sans-serif"
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
    # Optional box variant (sourced from area_shapes.xml + default_shapes.xml).
    # Only applied when the node is NOT resolved to a SAP icon. Vocabulary:
    #   btp-filled, btp-outline, btp-dashed, btp-dotted (blue family)
    #   non-sap-filled, non-sap-outline, non-sap-dashed, non-sap-dotted (grey)
    #   accent-teal, accent-purple, accent-pink (highlights, with -dashed /
    #     -dotted suffixes available)
    boxStyle: str = "btp-outline"
    # Interface badge rendered as a small rounded pill at the top of the
    # node ("Interface" or custom label). Values: "sap" | "generic" | None.
    interface: str | None = None
    # Optional sequence number (1-9 typical). Placed as a circle at the
    # top-left corner of the node.
    step: int | None = None
    # Step circle colour (matches numbers.xml variants):
    #   default (dark grey gradient), blue, purple, pink, green, yellow, teal
    stepKind: str = "default"
    # Generic icon (User, Mobile, Desktop, Cloud Connector, On-Premise,
    # Third Party, Adapter, AI, Database, Server, ...) sourced from the
    # 20-03-generic-icons SAP library. When set, OVERRIDES boxStyle and
    # service icon resolution. Format: "<base>" or "<base>:<variant>"
    # where variant is sap | non-sap | highlight (default: non-sap).
    # Example: "user", "mobile:sap", "third-party"
    genericIcon: str | None = None


@dataclass
class Edge:
    id: str
    source: str
    target: str
    style: str = "solid"
    label: str = ""
    direction: str = "forward"  # forward | bidirectional | none
    # Semantic edge type for SAP-canonical highlights:
    #   "default"          — standard non-SAP grey line + plain label
    #   "trust"            — pink #CC00DC bidirectional + pill (IAS-XSUAA / IDP)
    #   "authenticate"     — green pill (user → app login)
    #   "authorize"        — purple pill (XSUAA token validation)
    #   "generic_protocol" — grey pill (named protocol: OData, REST, GraphQL)
    #   "annotation"       — fully custom pill with `pillColor` + `pillFill`
    #   "positive"         — green #188918 (success/certified, no pill)
    #   "critical"         — orange #C35500 (degraded/at-risk, no pill)
    #   "negative"         — red #D20A0A (failed/deprecated, no pill)
    kind: str = "default"
    # Custom pill colour family (only when kind="annotation"). Picks one of:
    # purple (Group/Role/Policy/Identity*), green (Authenticate-like), pink
    # (Trust-like), grey (protocol-like), blue (REST/SAML2/OIDC), teal (BTP
    # accent). The pill is always rendered with arcSize=50 + fontStyle=1.
    pillColor: str = "purple"


@dataclass
class Preset:
    """A SAP essential preset embedded into the diagram (e.g. 'user-and-client',
    'legend', 'cloud-connector', 'saml-oidc', '3rd-party-idp-and-protocols')."""
    slug: str
    x: int = 32
    y: int = 60
    label: str | None = None  # optional override of preset's internal labels


@dataclass
class Diagram:
    title: str
    level: str
    author: str
    groups: list[Group]
    nodes: list[Node]
    edges: list[Edge]
    presets: list[Preset] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Shape index — maps service names → SAP icon style strings
# ─────────────────────────────────────────────────────────────────────────────
class ShapeIndex:
    """Wrap shape-index.json and provide fast service-name resolution."""

    def __init__(
        self,
        services: list[dict[str, Any]],
        generic_icons: list[dict[str, Any]] | None = None,
        essentials: list[dict[str, Any]] | None = None,
    ):
        self._by_name: dict[str, dict[str, Any]] = {}
        self._by_alias: dict[str, dict[str, Any]] = {}
        self._by_techid: dict[str, dict[str, Any]] = {}
        # Generic icon catalog: keyed by lowercased base + variant.
        # e.g. ("user", "non-sap") → entry. Default variant is non-sap.
        self._generic: dict[tuple[str, str], dict[str, Any]] = {}
        # Essentials catalog: keyed by slug for `Diagram.presets[]` lookup.
        self._essentials: dict[str, dict[str, Any]] = {}
        for e in essentials or []:
            slug = e.get("slug")
            if slug:
                self._essentials[slug] = e
        for g in generic_icons or []:
            base = (g.get("base") or "").lower().strip()
            variant = (g.get("variant") or "non-sap").lower().strip()
            size = g.get("size", "M")
            key = (base, variant)
            existing = self._generic.get(key)
            # Prefer M size as canonical default.
            if not existing or (existing.get("size") != "M" and size == "M"):
                self._generic[key] = g
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
        return cls(
            data.get("services", []),
            data.get("genericIcons", []),
            data.get("essentials", []),
        )

    def get_essential(self, slug: str) -> dict[str, Any] | None:
        """Lookup a SAP essential preset by slug (e.g. "user-and-client",
        "legend", "cloud-connector", "saml-oidc", "3rd-party-idp-and-protocols")."""
        return self._essentials.get(slug)

    def resolve_generic(self, query: str | None) -> dict[str, Any] | None:
        """Lookup a generic icon by base name (+ optional variant suffix).

        Format: ``"<base>"`` or ``"<base>:<variant>"``.
        Variants: ``sap`` | ``non-sap`` (default) | ``highlight``.
        Aliases: ``app-clients`` → ``Mobile`` (the SAP "Mobile" icon is a
        2-monitor pictogram traditionally labelled "Application Clients").
        """
        if not query:
            return None
        q = query.strip().lower()
        variant = "non-sap"
        if ":" in q:
            q, variant = q.split(":", 1)
            variant = variant.strip()
        # Normalise common aliases to the SAP base name.
        alias_map = {
            "app-clients": "mobile",
            "application-clients": "mobile",
            "client": "mobile",
            "person": "user",
            "stick-figure": "user",
            "monitor": "desktop",
            "device": "devices",
            "third-party": "third party",
            "3rd-party": "third party",
            "on-premise": "on-premise",
            "on-prem": "on-premise",
            "cloud-connector": "cloud connector",
            "ai-agent": "ai agent",
        }
        base = alias_map.get(q, q)
        # First try exact (base, variant); fall back to non-sap if missing.
        entry = self._generic.get((base, variant))
        if not entry:
            entry = self._generic.get((base, "non-sap"))
        if not entry:
            entry = self._generic.get((base, "highlight"))
        if not entry:
            entry = self._generic.get((base, "sap"))
        return entry

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
            boxStyle=n.get("boxStyle", "btp-outline"),
            interface=n.get("interface"),
            step=n.get("step"),
            stepKind=n.get("stepKind", "default"),
            genericIcon=n.get("genericIcon"),
        )
        nodes.append(node)
        if node.group and node.group in group_map:
            group_map[node.group].nodes.append(node.id)

    valid_kinds = {
        "default", "trust", "positive", "critical", "negative",
        "authenticate", "authorize", "generic_protocol", "annotation",
    }
    valid_pill_colors = {"purple", "green", "pink", "grey", "blue", "teal"}
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
        pill_color = e.get("pillColor", "purple")
        if pill_color not in valid_pill_colors:
            raise ValueError(
                f"edge {e.get('id')!r}: pillColor must be one of "
                f"{sorted(valid_pill_colors)} (got {pill_color!r})"
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
                pillColor=pill_color,
            )
        )

    presets = []
    for p in payload.get("presets", []) or []:
        presets.append(
            Preset(
                slug=p["slug"],
                x=int(p.get("x", 32)),
                y=int(p.get("y", 60)),
                label=p.get("label"),
            )
        )

    return Diagram(
        title=title,
        level=level,
        author=author,
        groups=list(group_map.values()),
        nodes=nodes,
        edges=edges,
        presets=presets,
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
    """Return (style_string, is_icon, display_label).

    Resolution order:
      1. ``genericIcon`` — generic SAP icon (User, Mobile/Desktop, Cloud
         Connector, …) from the 20-03-generic-icons set. Highest priority
         because it's an explicit author choice.
      2. SAP service icon — SVG-inline drawioStyle from the BTP service
         catalog (resolved via ``service`` field).
      3. Plain box — variant from ``Node.boxStyle`` (defaults to
         btp-outline, the most common SAP pattern).
    """
    if n.genericIcon:
        gen = shape_index.resolve_generic(n.genericIcon)
        if gen and gen.get("drawioStyle"):
            return gen["drawioStyle"], True, n.label
    svc = shape_index.resolve(n.service)
    if svc and svc.get("drawioStyle"):
        canonical = svc.get("name") or n.label
        label = n.label if n.label and n.label != n.id else canonical
        return svc["drawioStyle"], True, label
    # Plain box — pick from the area_shapes-derived catalogue.
    style = _BOX_STYLES.get(n.boxStyle, _BOX_STYLES["btp-outline"])
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

# Palette for `kind="annotation"` — fully custom pill labels for arbitrary
# text (Group, Role, Policy, OIDC, SAML2/OIDC, SCIM, REST/SPI, REST/Token,
# Task Data, Identity Lifecycle, Source, Target, Business Role, Role
# Replica, …). Sourced from the colours observed across the 4 SAP
# reference diagrams the user pointed to.
_ANNOTATION_PILL_PALETTE = {
    "purple": {  # Group, Role, Policy, Usergroup, Business Role, …
        "stroke": "#5D36FF",
        "fill":   "#F1ECFF",
        "fontColor": "#5D36FF",
    },
    "green": {   # Authenticate-like / SAML2 / OIDC / SAML2/OIDC
        "stroke": "#188918",
        "fill":   "#F5FAE5",
        "fontColor": "#188918",
    },
    "pink": {    # Trust-like / Mutual Trust
        "stroke": "#CC00DC",
        "fill":   "#FFF0FA",
        "fontColor": "#CC00DC",
    },
    "grey": {    # Generic protocol, REST/SPI, REST/Token, Task Data
        "stroke": "#475F75",
        "fill":   "#F5F6F7",
        "fontColor": "#475F75",
    },
    "blue": {    # SAP-affiliated annotations (Protocol blue, Source/Target)
        "stroke": "#0070F2",
        "fill":   "#EBF8FF",
        "fontColor": "#0070F2",
    },
    "teal": {    # Accent teal (Protocol teal in annotations_and_interfaces.xml)
        "stroke": "#07838F",
        "fill":   "#DAFDF5",
        "fontColor": "#07838F",
    },
}

# Box style variants (for nodes WITHOUT a SAP icon). Sourced from
# area_shapes.xml + default_shapes.xml. Each entry yields a complete
# drawio style string. "filled" uses the colour family's tinted fill;
# "outline" uses white. dashed/dotted apply the SAP convention.
def _box_style_def(stroke: str, fill: str, dashed_attr: str = "") -> str:
    return (
        f"rounded=1;whiteSpace=wrap;html=1;"
        f"strokeColor={stroke};fillColor={fill};"
        f"arcSize=24;absoluteArcSize=1;strokeWidth=1.5;"
        f"fontColor={PALETTE['title']};fontSize=11;"
        f"align=center;verticalAlign=middle;{dashed_attr}"
    )


_BOX_STYLES: dict[str, str] = {
    # BTP blue family
    "btp-filled":  _box_style_def("#0070F2", "#EBF8FF"),
    "btp-outline": _box_style_def("#0070F2", "#FFFFFF"),
    "btp-dashed":  _box_style_def("#0070F2", "#EBF8FF", "dashed=1;dashPattern=8 4;"),
    "btp-dotted":  _box_style_def("#0070F2", "#EBF8FF", "dashed=1;dashPattern=1 4;"),

    # Non-SAP grey family
    "non-sap-filled":  _box_style_def("#475E75", "#F5F6F7"),
    "non-sap-outline": _box_style_def("#475E75", "#FFFFFF"),
    "non-sap-dashed":  _box_style_def("#475E75", "#F5F6F7", "dashed=1;dashPattern=8 4;"),
    "non-sap-dotted":  _box_style_def("#475E75", "#F5F6F7", "dashed=1;dashPattern=1 4;"),

    # Accent teal (highlight new / brand-new component)
    "accent-teal":         _box_style_def("#07838f", "#dafdf5"),
    "accent-teal-outline": _box_style_def("#07838f", "#FFFFFF"),
    "accent-teal-dashed":  _box_style_def("#07838f", "#dafdf5", "dashed=1;dashPattern=8 4;"),

    # Accent purple (AI / GenAI emphasis)
    "accent-purple":         _box_style_def("#5d36ff", "#f1ecff"),
    "accent-purple-outline": _box_style_def("#5d36ff", "#FFFFFF"),
    "accent-purple-dashed":  _box_style_def("#5d36ff", "#f1ecff", "dashed=1;dashPattern=8 4;"),

    # Accent pink (experimental / beta)
    "accent-pink":         _box_style_def("#cc00dc", "#FFF0FA"),
    "accent-pink-outline": _box_style_def("#cc00dc", "#FFFFFF"),
    "accent-pink-dashed":  _box_style_def("#cc00dc", "#FFF0FA", "dashed=1;dashPattern=8 4;"),

    # Semantic: positive / critical / negative (use sparingly)
    "positive": _box_style_def("#188918", "#F5FAE5"),
    "critical": _box_style_def("#C35500", "#FFF8D6"),
    "negative": _box_style_def("#D20A0A", "#FFEAF4"),
}

# Step number circles (sourced from numbers.xml). Each variant yields a
# 30x30 circle with gradient fill + bold white centred number. SAP ships
# 7 colour variants in numbers.xml; we expose them here.
_STEP_KIND_GRADIENT = {
    # (gradientColor, fillColor)
    "default": ("#223548", "#5b738b"),  # dark grey gradient
    "blue":    ("#0040A0", "#0070F2"),
    "purple":  ("#3220BF", "#5D36FF"),
    "pink":    ("#A0008C", "#CC00DC"),
    "green":   ("#0E5C0E", "#188918"),
    "yellow":  ("#9F8500", "#E0B400"),
    "teal":    ("#066068", "#07838F"),
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
    if e.kind == "annotation":
        # Annotation edges: prefer canonical SAP color when the label
        # matches a known pattern (SAML2/OIDC, Group, OIDC, …). Otherwise
        # use the explicit pillColor family.
        canonical = _CANONICAL_PILLS.get(e.label) if e.label else None
        if canonical and canonical.get("stroke"):
            stroke = canonical["stroke"]
        else:
            pill_def = _ANNOTATION_PILL_PALETTE.get(
                e.pillColor, _ANNOTATION_PILL_PALETTE["purple"]
            )
            stroke = pill_def["stroke"]
    else:
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

    # 1. Title — SAP-canonical: bold blue (#0070F2) + "- SAP BTP Solution
    # Diagram" suffix. Convention observed in EVERY official sample
    # (SAP_Task_Center_*.drawio, SAP_Private_Link_Service_L2, etc.).
    # Left-aligned in the top-left corner.
    title_id = _stable_id("title", diagram.title)
    title_w = max(canvas_w - 96, 600)
    diagram_title = diagram.title
    if "Solution Diagram" not in diagram_title:
        diagram_title = f"{diagram_title} - SAP BTP Solution Diagram"
    title_cell = ET.SubElement(
        root,
        "mxCell",
        attrib={
            "id": title_id,
            "value": diagram_title,
            "style": (
                f"text;html=1;align=left;verticalAlign=middle;"
                f"fontColor={PALETTE['btp_border']};fontSize=20;fontStyle=1;"
                f"fontFamily={SAP_FONT_FAMILY};"
            ),
            "vertex": "1",
            "parent": "1",
        },
    )
    ET.SubElement(
        title_cell,
        "mxGeometry",
        attrib={
            "x": "32",
            "y": "12",
            "width": str(title_w),
            "height": "30",
            "as": "geometry",
        },
    )

    # Diagram level caption at bottom-left ("Diagram Level: L2") — SAP
    # convention seen in SAP_Private_Link_Service_L2 and Task Center L2.
    if diagram.level in ("L0", "L1", "L2", "L3"):
        level_cell = ET.SubElement(
            root,
            "mxCell",
            attrib={
                "id": _stable_id("dlevel", diagram.title),
                "value": f"Diagram Level: {diagram.level}",
                "style": (
                    f"text;html=1;align=left;verticalAlign=middle;"
                    f"fontColor={PALETTE['title']};fontSize=11;"
                    f"fontFamily=Helvetica;"
                ),
                "vertex": "1",
                "parent": "1",
            },
        )
        ET.SubElement(
            level_cell,
            "mxGeometry",
            attrib={
                "x": "32",
                "y": str(canvas_h - 28),
                "width": "180",
                "height": "20",
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

        node_cell_id = _stable_id("n", n.id)
        n_cell = ET.SubElement(
            root,
            "mxCell",
            attrib={
                "id": node_cell_id,
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

        # Interface badge — small rounded pill placed at the top-centre of
        # the node, sized 56×16 per annotations_and_interfaces.xml. Two
        # variants:
        #   "sap"     → SAP blue #0070f3 with default fill
        #   "generic" → SAP grey #475f75 with default fill
        # Label text is "Interface" (or whatever Node.interface_label says
        # in future). The pill has parent=node_cell_id so it moves with
        # the node when the user drags it in drawio.
        if n.interface in ("sap", "generic"):
            badge_stroke = "#0070f3" if n.interface == "sap" else "#475f75"
            badge_label = "Interface"
            badge_w, badge_h = 56, 16
            badge = ET.SubElement(
                root,
                "mxCell",
                attrib={
                    "id": _stable_id("if", n.id),
                    "value": badge_label,
                    "style": (
                        f"rounded=1;whiteSpace=wrap;html=1;arcSize=50;"
                        f"strokeColor={badge_stroke};fillColor=default;"
                        f"strokeWidth=1.5;fontColor={badge_stroke};"
                        f"fontStyle=1;fontSize=9;align=center;verticalAlign=middle;"
                    ),
                    "vertex": "1",
                    "parent": node_cell_id,
                    "connectable": "0",
                },
            )
            # Position the badge at the top of the node, slightly above
            # the icon so it overlaps the border (SAP-canonical).
            ET.SubElement(
                badge,
                "mxGeometry",
                attrib={
                    "x": str((w - badge_w) // 2),
                    "y": str(-badge_h // 2),
                    "width": str(badge_w),
                    "height": str(badge_h),
                    "as": "geometry",
                },
            )

        # Step number circle — 30×30 ellipse with gradient fill + bold
        # white digit. Placed at the top-LEFT corner of the node, half
        # off the border (visual badge convention). Sourced from
        # numbers.xml.
        if n.step is not None and 1 <= n.step <= 99:
            grad, fill = _STEP_KIND_GRADIENT.get(
                n.stepKind, _STEP_KIND_GRADIENT["default"]
            )
            step_w, step_h = 30, 30
            step_cell = ET.SubElement(
                root,
                "mxCell",
                attrib={
                    "id": _stable_id("st", n.id),
                    "value": (
                        f"<p style=\"line-height: 100%;\"><b>"
                        f"<font face=\"arial black\" "
                        f"style=\"font-size: 16px;\" color=\"#ffffff\">"
                        f"{n.step}</font></b></p>"
                    ),
                    "style": (
                        f"ellipse;whiteSpace=wrap;html=1;aspect=fixed;"
                        f"gradientColor={grad};strokeColor=none;"
                        f"gradientDirection=east;fillColor={fill};rounded=0;"
                        f"fontFamily=Helvetica;fontSize=12;fontColor=#FFFFFF;"
                        f"align=center;verticalAlign=middle;"
                    ),
                    "vertex": "1",
                    "parent": node_cell_id,
                    "connectable": "0",
                },
            )
            ET.SubElement(
                step_cell,
                "mxGeometry",
                attrib={
                    "x": str(-step_w // 2),
                    "y": str(-step_h // 2),
                    "width": str(step_w),
                    "height": str(step_h),
                    "as": "geometry",
                },
            )

    # 4. Edges — exit/entry anchors + optional waypoints from dot.
    for e in diagram.edges:
        src_geom = node_abs_geom.get(e.source)
        tgt_geom = node_abs_geom.get(e.target)
        edge_id = _stable_id("e", e.id)
        # For pill-rendered kinds (trust, authenticate, authorize,
        # generic_protocol, annotation), the visible label sits in a
        # separate rounded vertex child. drawio does NOT honour arcSize on
        # inline edge labels, so the multi-cell pattern is the only way.
        has_pill = e.kind in _EDGE_KIND_PILL or e.kind == "annotation"
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
            if e.kind == "annotation":
                # 1. Try canonical catalog first — if the label is a known
                #    SAP-canonical pill (SAML2/OIDC, Group, OIDC, ORD, …),
                #    use its exact stroke/fill from the 138 SAP examples.
                canonical = _CANONICAL_PILLS.get(e.label)
                if canonical:
                    pill_def = {
                        "stroke": canonical.get("stroke") or "#475F75",
                        "fill":   canonical.get("fill") or "#F5F6F7",
                        "fontColor": canonical.get("stroke") or "#475F75",
                    }
                else:
                    # 2. Fall back to the user-chosen palette family
                    #    (purple/green/pink/grey/blue/teal).
                    pill_def = _ANNOTATION_PILL_PALETTE.get(
                        e.pillColor, _ANNOTATION_PILL_PALETTE["purple"]
                    )
            else:
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

    # 5. SAP essential presets — embed pre-composed organisms (User and
    # client, Cloud Connector, SAML/OIDC, 3rd party IdP and protocols, …)
    # at the requested coordinates. Uses raw XML from essentials.xml so
    # the visual matches SAP's curated compositions verbatim.
    for preset in diagram.presets:
        _embed_preset(root, preset, shape_index)

    # 6. Legend molecule (bottom-right). Two paths:
    #    - User asked for 'sap' or 'sap-short' preset → embed SAP essential
    #    - Otherwise auto-generate based on actual styles used.
    _emit_legend(root, diagram, canvas_w, canvas_h, shape_index)

    return _serialize(mxfile)


def _embed_preset(root: ET.Element, preset: Preset, shape_index: ShapeIndex) -> None:
    """Embed a SAP essential preset (multi-cell composition) into the
    diagram at (preset.x, preset.y).

    The essentials.xml entries store an mxGraphModel snippet with cells
    using IDs like "0", "1", "2", "3", ... When we embed multiple presets
    in one diagram, those IDs collide. We rewrite them with a stable
    per-preset prefix derived from the slug, and translate coordinates
    by (x, y).
    """
    essential = shape_index.get_essential(preset.slug)
    if not essential:
        return
    raw = essential.get("rawXml") or ""
    if not raw:
        return
    try:
        snippet_root = ET.fromstring(f"<wrap>{raw}</wrap>")
    except ET.ParseError:
        return

    prefix = f"p-{preset.slug}-{abs(hash(preset.slug)) % 1000000:06d}"
    id_map: dict[str, str] = {"0": "0", "1": "1"}  # never rewrite drawio root cells

    # First pass: build ID rewrite map.
    for cell in snippet_root.iter("mxCell"):
        old_id = cell.get("id")
        if old_id and old_id not in id_map:
            id_map[old_id] = f"{prefix}-{old_id}"

    # Second pass: emit each cell with translated geometry + rewritten IDs.
    for cell in snippet_root.iter("mxCell"):
        old_id = cell.get("id", "")
        if old_id in ("0", "1"):
            continue  # skip drawio root cells (we already have ours)
        new_id = id_map.get(old_id, old_id)
        new_parent = id_map.get(cell.get("parent", "1"), "1")
        new_source = id_map.get(cell.get("source", ""), cell.get("source"))
        new_target = id_map.get(cell.get("target", ""), cell.get("target"))

        new_attrs = {
            "id": new_id,
            "value": cell.get("value", ""),
            "style": cell.get("style", ""),
            "parent": new_parent,
        }
        if cell.get("vertex") == "1":
            new_attrs["vertex"] = "1"
        if cell.get("edge") == "1":
            new_attrs["edge"] = "1"
        if new_source:
            new_attrs["source"] = new_source
        if new_target:
            new_attrs["target"] = new_target
        if cell.get("connectable"):
            new_attrs["connectable"] = cell.get("connectable")

        new_cell = ET.SubElement(root, "mxCell", attrib=new_attrs)

        # Translate geometry: only top-level cells (parent='1') get the
        # offset because nested cells use coordinates relative to their
        # parent. The preset's internal nesting structure is preserved.
        for child in cell:
            if child.tag == "mxGeometry":
                geom_attrs = dict(child.attrib)
                if cell.get("parent") == "1":
                    try:
                        geom_attrs["x"] = str(float(geom_attrs.get("x", "0")) + preset.x)
                        geom_attrs["y"] = str(float(geom_attrs.get("y", "0")) + preset.y)
                    except ValueError:
                        pass
                new_geom = ET.SubElement(new_cell, "mxGeometry", attrib=geom_attrs)
                # Copy any inner mxPoint, Array, etc.
                for grandchild in child:
                    new_geom.append(grandchild)
            else:
                # Generic deep copy (mxPoint, Array, etc.)
                new_cell.append(child)


def _emit_legend(
    root: ET.Element,
    diagram: Diagram,
    canvas_w: int,
    canvas_h: int,
    shape_index: ShapeIndex | None = None,
) -> None:
    """Append a SAP-canonical legend molecule at the bottom-right of the
    canvas, listing only the line styles + edge kinds actually used.

    Layout: a rounded container with title "Legend" (font 12 bold blue) +
    one row per element. Each row has a 36×8 sample line on the left and
    a label on the right. Total size adapts to row count.
    """
    used_styles = {e.style for e in diagram.edges}
    used_kinds = {e.kind for e in diagram.edges if e.kind != "default"}

    # Build legend rows in a stable, SAP-canonical order.
    rows: list[tuple[str, str, str]] = []  # (label, sample_style_kind, color)

    # Line-style rows
    if "solid" in used_styles:
        rows.append(("Direct (sync)", "line-solid", PALETTE["non_sap_border"]))
    if "dashed" in used_styles:
        rows.append(("Indirect (async)", "line-dashed", PALETTE["non_sap_border"]))
    if "dotted" in used_styles:
        rows.append(("Optional", "line-dotted", PALETTE["non_sap_border"]))
    if "thick" in used_styles:
        rows.append(("Firewall", "line-thick", PALETTE["non_sap_border"]))

    # Pill-kind rows
    kind_labels = {
        "trust":            ("Trust",            "#CC00DC"),
        "authenticate":     ("Authenticate",     "#188918"),
        "authorize":        ("Authorize",        "#470bed"),
        "generic_protocol": ("Protocol",         "#475F75"),
        "annotation":       ("Annotation",       "#5D36FF"),
        "positive":         ("Positive",         "#188918"),
        "critical":         ("Critical",         "#C35500"),
        "negative":         ("Negative",         "#D20A0A"),
    }
    for kind in ("trust", "authenticate", "authorize", "generic_protocol", "annotation",
                 "positive", "critical", "negative"):
        if kind in used_kinds:
            label, color = kind_labels[kind]
            rows.append((label, f"pill-{kind}", color))

    if not rows:
        return  # nothing to legend

    # Geometry: 220 wide, 32 (title) + 18 per row + 16 padding bottom.
    legend_w = 220
    row_h = 18
    legend_h = 32 + len(rows) * row_h + 12
    legend_x = canvas_w - legend_w - 32
    legend_y = canvas_h - legend_h - 40  # leave room for "Diagram Level" caption

    legend_id = _stable_id("legend", diagram.title)
    legend_box = ET.SubElement(
        root,
        "mxCell",
        attrib={
            "id": legend_id,
            "value": "Legend",
            "style": (
                "rounded=1;whiteSpace=wrap;html=1;arcSize=6;absoluteArcSize=1;"
                "strokeColor=#475F75;fillColor=#FFFFFF;strokeWidth=1.5;"
                "fontColor=#0070F2;fontSize=12;fontStyle=1;"
                "verticalAlign=top;align=left;spacingLeft=10;spacingTop=6;"
            ),
            "vertex": "1",
            "parent": "1",
        },
    )
    ET.SubElement(
        legend_box,
        "mxGeometry",
        attrib={
            "x": str(legend_x),
            "y": str(legend_y),
            "width": str(legend_w),
            "height": str(legend_h),
            "as": "geometry",
        },
    )

    for idx, (label, kind, color) in enumerate(rows):
        row_y = 30 + idx * row_h  # relative to legend box
        # Sample line / pill on left
        if kind.startswith("line-"):
            line_style_map = {
                "line-solid":  "endArrow=none;strokeColor=" + color + ";strokeWidth=1.5;dashed=0;",
                "line-dashed": "endArrow=none;strokeColor=" + color + ";strokeWidth=1.5;dashed=1;dashPattern=8 4;",
                "line-dotted": "endArrow=none;strokeColor=" + color + ";strokeWidth=1.5;dashed=1;dashPattern=1 4;",
                "line-thick":  "endArrow=none;strokeColor=" + color + ";strokeWidth=3;dashed=0;",
            }
            sample = ET.SubElement(
                root,
                "mxCell",
                attrib={
                    "id": _stable_id("legline", f"{diagram.title}-{idx}"),
                    "value": "",
                    "style": line_style_map[kind],
                    "edge": "1",
                    "parent": legend_id,
                },
            )
            geom = ET.SubElement(sample, "mxGeometry", attrib={"relative": "1", "as": "geometry"})
            ET.SubElement(geom, "mxPoint", attrib={"x": "12", "y": str(row_y + 6), "as": "sourcePoint"})
            ET.SubElement(geom, "mxPoint", attrib={"x": "52", "y": str(row_y + 6), "as": "targetPoint"})
        else:
            # Pill sample
            pill_def = (_EDGE_KIND_PILL.get(kind.removeprefix("pill-"))
                        or _ANNOTATION_PILL_PALETTE.get("purple"))
            ET.SubElement(
                root,
                "mxCell",
                attrib={
                    "id": _stable_id("legpill", f"{diagram.title}-{idx}"),
                    "value": "",
                    "style": (
                        f"rounded=1;whiteSpace=wrap;html=1;arcSize=50;"
                        f"strokeColor={pill_def['stroke']};"
                        f"fillColor={pill_def['fill']};strokeWidth=1.5;"
                    ),
                    "vertex": "1",
                    "parent": legend_id,
                    "connectable": "0",
                },
            ).append(ET.Element(
                "mxGeometry",
                attrib={
                    "x": "12",
                    "y": str(row_y),
                    "width": "40",
                    "height": "12",
                    "as": "geometry",
                },
            ))
        # Label text on right
        label_cell = ET.SubElement(
            root,
            "mxCell",
            attrib={
                "id": _stable_id("leglbl", f"{diagram.title}-{idx}"),
                "value": label,
                "style": (
                    f"text;html=1;align=left;verticalAlign=middle;"
                    f"fontColor={PALETTE['title']};fontSize=10;"
                ),
                "vertex": "1",
                "parent": legend_id,
            },
        )
        ET.SubElement(
            label_cell,
            "mxGeometry",
            attrib={
                "x": "62",
                "y": str(row_y - 3),
                "width": "150",
                "height": "16",
                "as": "geometry",
            },
        )


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
