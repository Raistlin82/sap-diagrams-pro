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
import re
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

# Normalised lookup table for fuzzy matching: strip whitespace, lowercase.
# So "REST/OData", "REST / OData", "rest/odata" all resolve to the same
# canonical entry.
_CANONICAL_PILLS_NORM: dict[str, dict] = {
    re.sub(r"\s+", "", k).lower(): v for k, v in _CANONICAL_PILLS.items()
}


def _resolve_canonical_pill(label: str | None) -> dict | None:
    """Resolve a pill label to its canonical SAP entry. Tries exact match
    first, then whitespace-insensitive case-insensitive match."""
    if not label:
        return None
    if label in _CANONICAL_PILLS:
        return _CANONICAL_PILLS[label]
    normalised = re.sub(r"\s+", "", label).lower()
    return _CANONICAL_PILLS_NORM.get(normalised)


# `re` was used above; keep at module top so the helpers above don't need
# their own imports. (Already imported at the top of this file.)

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
# SAP solution diagrams use Helvetica/Arial for diagram elements — the "72"
# brand font is for the SAP website, not the diagrams (per the guideline assets,
# every mxGraph style string in the official libraries uses fontFamily=Helvetica).
SAP_FONT_FAMILY = "Helvetica,Arial,sans-serif"

# Greedy-fallback geometry only. Icon sizing in the default (skeleton) path comes
# from `_skeleton_layout.icon_size(level)` (48px for L0/L1, 32px for L2).
CELL_W = CANVAS_W // 3
CELL_H = CANVAS_H // 3
GROUP_PADDING = 24
# Plain (no SAP icon) box sizing — used for users, non-SAP, unresolved services.
NODE_W = 160
NODE_H = 80
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
    parent: str | None = None  # id of parent group; None = top-level
    # Optional layout overrides honoured by the skeleton layout engine:
    flow: str | None = None    # "row" | "col" | "grid" — intra-group packing
    zone: str | None = None    # "left" | "center" | "right" — column override
    nodes: list[str] = field(default_factory=list)
    # ─── IR v2 (Task 4) ─────────────────────────────────────────────────────
    # All v2 fields are optional and default to None so v1 IRs parse
    # unchanged. `type` additionally accepts "subaccount", "governance",
    # "cloud-tier" and "custom-app" (validated by scripts/validate-ir.py, not
    # here — see that script's ALLOWED_GROUP_TYPES). Molecule emission for
    # these is wired in Task 5; this task only adds the fields + parsing.
    #
    # Cloud-tier kind, meaningful when type == "cloud-tier":
    #   "public" | "private" | "any-premise"
    kind: str | None = None
    # Badge collection rendered on subaccount / cloud-tier groups, e.g.:
    #   {"hyperscalers": ["aws", "azure"], "runtimes": ["cloud-foundry"]}
    badges: dict[str, Any] | None = None


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
    # Optional one-line caption under the title in a backend-box molecule
    # (RIGHT-zone systems, e.g. "Mobile or Desktop"). Ignored for bare icons.
    subtitle: str | None = None
    # ─── IR v2 (Task 4) ─────────────────────────────────────────────────────
    # Optional; None default keeps v1 IRs unchanged. Molecule emission (Task
    # 5) and validation (scripts/validate-ir.py) own the actual vocabulary.
    #
    # Node archetype: "product" | "chip" | "db". `product` is a leaf molecule
    # (a box whose `capabilities` are data, not addressable child nodes).
    type: str | None = None
    # Capability list for `type == "product"` nodes, e.g.:
    #   [{"label": "Decision", "icon": "decision"}, {"label": "Actions"}]
    # Each entry is {label: str, icon?: str} — checked by validate-ir.py.
    capabilities: list[dict[str, Any]] | None = None


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
    # ─── IR v2 (Task 4) ─────────────────────────────────────────────────────
    # Optional; None default keeps v1 IRs unchanged.
    #
    # Protocol/annotation text rendered as a pill on the edge (e.g. "SCIM",
    # "SAML2/OIDC", "CTMS"). Distinct from `kind`/`pillColor` (the v1 "SAP
    # canonical pill" mechanism) — positioning is owned by the channel
    # router (Task 8e); this task only carries the text through parsing.
    pill: str | None = None
    # Semantic flow family driving edge colour + dash from the style
    # contract (Task 5): "identity" | "provisioning" | "master-data" |
    # "transport" | "firewall" | "default" (1:1 with the six edge-* molecules).
    # Checked by validate-ir.py when not None.
    flowFamily: str | None = None


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
    # ─── IR v2 (Task 4) ─────────────────────────────────────────────────────
    # All optional; None default keeps v1 IRs unchanged.
    #
    # Mechanical patch vocabulary consumed by the visual rubric (Task 13),
    # e.g. [{"op": "set_group_flow", "group": "btp-core", "value": "row"}].
    # Opaque to this task — carried through parsing only.
    layoutHints: list[dict[str, Any]] | None = None
    # metadata.branding — refs into assets/brand-pack(.local)/, e.g.:
    #   {"customerLogo": "acme", "partnerWatermark": "lutech"}
    # Missing local assets degrade gracefully at emit time (Task 5+); never
    # a hard failure here.
    branding: dict[str, Any] | None = None
    # metadata.badges — same {hyperscalers: [...], runtimes: [...]} shape as
    # Group.badges, but scoped to the whole diagram (e.g. a title-block strip)
    # rather than a single subaccount/cloud-tier group.
    badges: dict[str, Any] | None = None
    # metadata.networkSeparator — draw the vertical NETWORK bar in the
    # center→right gutter (Task 7). Default on; set false to opt out. The
    # skeleton layout reads this to decide whether to emit the separator geometry
    # into its meta block.
    networkSeparator: bool = True


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
        exact techId → safe word-level match. Returns None on miss.

        The fuzzy step requires EVERY query word to appear as a word in the
        canonical name (e.g. "HANA Cloud" → "SAP HANA Cloud"), then picks the
        candidate with the fewest extra words — deterministic and immune to the
        dangerous substring matches the old code allowed (e.g. "AI" matching
        anything containing those letters, or a short query swallowing a wrong
        service). Wrong-icon picks were a root cause of bad "block types".
        """
        if not query:
            return None
        if query in self._by_name:
            return self._by_name[query]
        if query.lower() in self._by_alias:
            return self._by_alias[query.lower()]
        if query in self._by_techid:
            return self._by_techid[query]
        ql = query.strip().lower()
        if len(ql) < 3:
            return None
        q_tokens = set(re.findall(r"[a-z0-9]+", ql))
        if not q_tokens:
            return None
        best, best_extra = None, 1 << 30
        for name, svc in sorted(self._by_name.items()):
            n_tokens = set(re.findall(r"[a-z0-9]+", name.lower()))
            if q_tokens <= n_tokens:  # every query word is a word in the name
                extra = len(n_tokens - q_tokens)
                if extra < best_extra:
                    best, best_extra = svc, extra
        return best


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
            flow=g.get("flow"),
            zone=g.get("zone"),
            nodes=[],
            kind=g.get("kind"),
            badges=g.get("badges"),
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
            subtitle=n.get("subtitle"),
            type=n.get("type"),
            capabilities=n.get("capabilities"),
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
                pill=e.get("pill"),
                flowFamily=e.get("flowFamily"),
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
        layoutHints=payload.get("layoutHints"),
        branding=meta.get("branding"),
        badges=meta.get("badges"),
        networkSeparator=bool(meta.get("networkSeparator", True)),
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
        # Sub-group (lane / "Subaccount" inner frame): white fill so the parent's
        # #EBF8FF reads as the "BTP territory". Canonical inner arc = 16.
        fill = "#FFFFFF"
        font_size = 12
        arc = 16
    else:
        font_size = 14
        # Canonical area radii: the BTP base layer uses 32, other top-level
        # areas 24 (absoluteArcSize) per the SAP shape libraries.
        arc = 32 if g.type == "btp-layer" else 24
    return (
        f"rounded=1;whiteSpace=wrap;html=1;"
        f"strokeColor={border};fillColor={fill};"
        f"arcSize={arc};absoluteArcSize=1;strokeWidth=1.5;"
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
            return _safe_img(gen["drawioStyle"]), True, n.label
    svc = shape_index.resolve(n.service)
    if svc and svc.get("drawioStyle"):
        canonical = svc.get("name") or n.label
        label = n.label if n.label and n.label != n.id else canonical
        return _safe_img(svc["drawioStyle"]), True, label
    # Plain box — pick from the area_shapes-derived catalogue.
    style = _BOX_STYLES.get(n.boxStyle, _BOX_STYLES["btp-outline"])
    return style, False, n.label


# Group types whose member nodes render as the RIGHT-zone "backend box"
# molecule (white rounded box with an icon on the left + title), per the SAP
# big-picture (e.g. "SAP On-Premise Solutions", "3rd Party Applications").
BACKEND_GROUP_TYPES = {"sap-app", "non-sap", "third-party", "external"}


def _safe_img(style: str | None) -> str | None:
    """draw.io style strings are ';'-delimited, so a ``;base64,`` inside an
    image data-URI silently breaks style parsing (the image truncates and the
    shape renders blank — this is why generic icons like the user/database
    glyphs were invisible). draw.io accepts the comma form
    ``data:image/...,<base64>`` (the official SAP service icons use exactly
    that, which is why they rendered). Normalise to the comma form.
    """
    return style.replace(";base64,", ",") if style else style


def _extract_image_uri(style: str | None) -> str | None:
    """Pull the ``image=...`` data-URI out of an icon shape style string."""
    if not style:
        return None
    m = re.search(r"image=([^;]+)", _safe_img(style))
    return m.group(1) if m else None


def _node_icon_uri(n: Node, shape_index: "ShapeIndex") -> str | None:
    """Resolve a node's icon (generic first, then service) to its data-URI."""
    if n.genericIcon:
        g = shape_index.resolve_generic(n.genericIcon)
        if g:
            uri = _extract_image_uri(g.get("drawioStyle"))
            if uri:
                return uri
    if n.service:
        svc = shape_index.resolve(n.service)
        if svc:
            return _extract_image_uri(svc.get("drawioStyle"))
    return None


def _backend_box(n: Node, group_type: str, shape_index: "ShapeIndex") -> tuple[str, str]:
    """Return (style, value) for a RIGHT-zone backend box.

    SAP apps get the BTP-blue border; 3rd-party / non-SAP get the grey border
    (Horizon ``(border, fill=white)`` per component-groups.md). When the node
    resolves to an icon it is embedded on the left via ``shape=label``; the
    title (and optional subtitle) sit to its right. This is the single-cell
    equivalent of the official "On-Premise" / "Cloud solutions" essentials.
    """
    stroke = PALETTE["btp_border"] if group_type == "sap-app" else PALETTE["non_sap_border"]
    base = (
        f"rounded=1;whiteSpace=wrap;html=1;arcSize=16;absoluteArcSize=1;"
        f"strokeColor={stroke};fillColor=#FFFFFF;strokeWidth=1.5;"
        f"fontColor={PALETTE['title']};fontSize=12;verticalAlign=middle;"
    )
    uri = _node_icon_uri(n, shape_index)
    if uri:
        style = (
            base
            + "shape=label;imageAlign=left;imageVerticalAlign=middle;"
            + "imageWidth=28;imageHeight=28;spacingLeft=44;spacingRight=8;align=left;"
            + f"image={uri};"
        )
    else:
        style = base + "align=center;spacingLeft=6;spacingRight=6;"
    if n.subtitle:
        value = (
            f'<b>{n.label}</b>'
            f'<div style="font-size:9px;color:#556B82;line-height:13px;">{n.subtitle}</div>'
        )
    else:
        value = n.label
    return style, value


def _emit_sap_btp_badge(root: ET.Element, parent_cell_id: str) -> None:
    """Small 'SAP BTP' logo chip at the container's top-left (gold standard)."""
    badge = ET.SubElement(
        root,
        "mxCell",
        attrib={
            "id": _stable_id("btpbadge", parent_cell_id),
            "value": "SAP BTP",
            "style": (
                "rounded=1;whiteSpace=wrap;html=1;arcSize=40;absoluteArcSize=1;"
                "fillColor=#0070F2;strokeColor=none;fontColor=#FFFFFF;fontStyle=1;"
                "fontSize=11;align=center;verticalAlign=middle;"
            ),
            "vertex": "1",
            "parent": parent_cell_id,
            "connectable": "0",
        },
    )
    ET.SubElement(
        badge,
        "mxGeometry",
        attrib={"x": "12", "y": "8", "width": "60", "height": "22", "as": "geometry"},
    )


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
    exit_a: tuple[float, float] | None = None,
    entry_a: tuple[float, float] | None = None,
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
        canonical = _resolve_canonical_pill(e.label)
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

    # Distributed exit/entry anchors (computed once across all edges so that
    # connectors sharing a node side fan out instead of stacking on the midpoint).
    if exit_a and entry_a:
        style += (
            f"exitX={exit_a[0]};exitY={exit_a[1]};exitDx=0;exitDy=0;"
            f"entryX={entry_a[0]};entryY={entry_a[1]};entryDx=0;entryDy=0;"
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


def _distribute_anchors(
    edges: list[Edge],
    geom: dict[str, tuple[int, int, int, int]],
) -> dict[str, tuple[tuple[float, float] | None, tuple[float, float] | None]]:
    """Spread edges that share the same node side across that side so parallel
    connectors fan out instead of stacking on one midpoint (the cause of the
    "arrows pile up" look). Anchor side is chosen by dominant axis between
    centres (same rule as ``_compute_anchors``); within a side the edges are
    ordered by the *other* endpoint's position to minimise crossings.

    Engine-level behaviour: applies to every generated diagram.
    """
    from collections import defaultdict

    def sides(src, tgt) -> tuple[str, str]:
        sx, sy, sw, sh = src
        tx, ty, tw, th = tgt
        dx = (tx + tw / 2) - (sx + sw / 2)
        dy = (ty + th / 2) - (sy + sh / 2)
        if abs(dx) >= abs(dy):
            return ("R", "L") if dx >= 0 else ("L", "R")
        return ("B", "T") if dy >= 0 else ("T", "B")

    def center(g, axis: str) -> float:
        x, y, w, h = g
        return x + w / 2 if axis == "x" else y + h / 2

    def coord(side: str, frac: float) -> tuple[float, float]:
        return {"R": (1.0, frac), "L": (0.0, frac),
                "T": (frac, 0.0), "B": (frac, 1.0)}[side]

    info: dict[str, tuple[str, str, tuple, tuple]] = {}
    src_groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    tgt_groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for e in edges:
        sg, tg = geom.get(e.source), geom.get(e.target)
        if not sg or not tg:
            continue
        ss, ts = sides(sg, tg)
        info[e.id] = (ss, ts, sg, tg)
        src_groups[(e.source, ss)].append(e.id)
        tgt_groups[(e.target, ts)].append(e.id)

    exit_a: dict[str, tuple[float, float]] = {}
    entry_a: dict[str, tuple[float, float]] = {}
    for (_node, side), eids in src_groups.items():
        axis = "y" if side in ("L", "R") else "x"
        ordered = sorted(eids, key=lambda eid: center(info[eid][3], axis))
        n = len(ordered)
        for i, eid in enumerate(ordered):
            exit_a[eid] = coord(side, round((i + 1) / (n + 1), 3))
    for (_node, side), eids in tgt_groups.items():
        axis = "y" if side in ("L", "R") else "x"
        ordered = sorted(eids, key=lambda eid: center(info[eid][2], axis))
        n = len(ordered)
        for i, eid in enumerate(ordered):
            entry_a[eid] = coord(side, round((i + 1) / (n + 1), 3))

    return {e.id: (exit_a.get(e.id), entry_a.get(e.id)) for e in edges if e.id in info}


# ─────────────────────────────────────────────────────────────────────────────
# Molecule emission (IR v2) — styles sourced from assets/style-contract.json via
# scripts/_molecules.py. The NEW group/node/edge types route through the contract
# here; the existing v1 hardcoded paths above are untouched.
# ─────────────────────────────────────────────────────────────────────────────
_MOLECULES_MOD = None

# IR v2 group types that render as contract-driven molecule frames.
MOLECULE_GROUP_TYPES = {"subaccount", "governance", "cloud-tier", "custom-app"}
# IR v2 leaf node archetypes that render as contract-driven molecules.
MOLECULE_NODE_TYPES = {"product", "db", "chip"}


def _molecules_module():
    """Lazily import scripts/_molecules.py (same path-based technique emit uses
    for _skeleton_layout). Cached for the process."""
    global _MOLECULES_MOD
    if _MOLECULES_MOD is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "_molecules", Path(__file__).resolve().parent / "_molecules.py"
        )
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _MOLECULES_MOD = _mod
    return _MOLECULES_MOD


_CHANNEL_ROUTER_MOD = None


def _channel_router_module():
    """Lazily import scripts/_channel_router.py (Task 8 — the edge router).
    Cached for the process, loaded the same path-based way as _molecules.

    Checks ``sys.modules`` FIRST — the same guarded pattern
    ``_channel_router.py``'s own ``_load_sibling`` and tests'
    ``conftest.load_script`` already use — instead of unconditionally
    exec'ing a fresh copy and overwriting ``sys.modules["_channel_router"]``.
    Skipping the check would silently leave two live copies of the module
    (this one and whichever the test process loaded first) with distinct
    ``Channel``/``RouteResult`` classes, breaking any future ``isinstance``
    or dataclass-identity check across the two."""
    global _CHANNEL_ROUTER_MOD
    if _CHANNEL_ROUTER_MOD is None:
        if "_channel_router" in sys.modules:
            _CHANNEL_ROUTER_MOD = sys.modules["_channel_router"]
        else:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location(
                "_channel_router", Path(__file__).resolve().parent / "_channel_router.py"
            )
            _mod = _ilu.module_from_spec(_spec)
            # Register BEFORE exec: _channel_router uses `from __future__ import
            # annotations` + @dataclass, so dataclass creation needs
            # sys.modules["_channel_router"] populated to resolve string annotations
            # (KW_ONLY/ClassVar lookups). See tests/conftest.load_script's note.
            sys.modules["_channel_router"] = _mod
            _spec.loader.exec_module(_mod)
            _CHANNEL_ROUTER_MOD = _mod
    return _CHANNEL_ROUTER_MOD


def _num(v: float) -> str:
    """Serialise a molecule coordinate to a rounded-int drawio string."""
    return str(int(round(float(v))))


def _place_molecule(
    root: ET.Element,
    cells: list[dict],
    *,
    anchor_id: str,
    anchor_parent: str,
    off_x: float,
    off_y: float,
    contract: dict,
    brand_packs: dict,
    icon_resolver,
    warnings: list[str],
    anchor_size: tuple[float, float] | None = None,
) -> str:
    """Serialise a molecule's cell dicts to ``mxCell`` XML.

    ``cells[0]`` is the anchor (``parent is None``): it takes ``anchor_id`` +
    ``anchor_parent`` and its (x,y) is offset by (off_x, off_y). Child cells
    (``parent`` = an in-molecule id) keep their parent-relative coords. Image
    placeholders are resolved (dataUri) or degrade to the text-badge fallback.
    ``anchor_size`` overrides the anchor's (w,h) — used to grow a group frame to
    the layout-computed footprint while keeping the contract style/children.
    """
    M = _molecules_module()
    idmap: dict[str, str] = {}
    for i, c in enumerate(cells):
        idmap[c["id"]] = anchor_id if i == 0 else f"{anchor_id}-{c['id']}"
    for i, c in enumerate(cells):
        eid = idmap[c["id"]]
        if c.get("parent") is None:
            parent = anchor_parent
            x, y = c["x"] + off_x, c["y"] + off_y
        else:
            parent = idmap.get(c["parent"], anchor_id)
            x, y = c["x"], c["y"]
        w, h = c["w"], c["h"]
        if i == 0 and anchor_size is not None:
            w, h = anchor_size
        rc = M.resolve_cell(c, brand_packs, contract, icon_resolver, warnings)
        attrib = {
            "id": eid,
            "value": rc.get("value", "") or "",
            "style": rc.get("style", "") or "",
            "vertex": "1",
            "parent": parent,
        }
        if c.get("connectable") is False:
            attrib["connectable"] = "0"
        cell = ET.SubElement(root, "mxCell", attrib=attrib)
        ET.SubElement(
            cell,
            "mxGeometry",
            attrib={"x": _num(x), "y": _num(y), "width": _num(w), "height": _num(h), "as": "geometry"},
        )
    return anchor_id


def _group_molecule_cells(
    g: Group, contract: dict, size: tuple[float, float] | None = None,
    show_chip: bool = True,
) -> list[dict] | None:
    """Contract-driven frame cells for an IR v2 group type (or None for v1).

    ``size`` is the FINAL frame size the skeleton layout computed (footprint of
    the packed children clamped to the contract minimum). Passing it lets each
    builder draw its decorations relative to the real frame edge — so a bottom-
    anchored tier-box badge row reflows instead of floating at the contract's
    reference height (Task 6 reflow). ``show_chip`` suppresses the redundant
    "SAP BTP" chip on a nested subaccount (FIX-B); it matches the value the
    skeleton layout reserved space for."""
    M = _molecules_module()
    if g.type == "subaccount":
        return M.subaccount_frame(g, contract, size, show_chip)
    if g.type == "governance":
        return M.governance_strip(g, contract, size)
    if g.type == "cloud-tier":
        return M.tier_box(g, contract, size)
    if g.type == "custom-app":
        return M.custom_app_box(g, contract, size)
    return None


def _flow_family_edge_style(
    e: Edge,
    contract: dict,
    exit_a: tuple[float, float] | None = None,
    entry_a: tuple[float, float] | None = None,
) -> str:
    """Edge style for a semantic flow family, sourced 1:1 from the style
    contract (edge-identity / edge-provisioning / …). Distribution anchors and a
    white label background are appended (keeping the contract style as the
    verbatim prefix)."""
    M = _molecules_module()
    style = M.flow_family_style(e.flowFamily, contract)
    style += "labelBackgroundColor=#FFFFFF;labelBorderColor=none;verticalAlign=middle;align=center;"
    if exit_a and entry_a:
        style += (
            f"exitX={exit_a[0]};exitY={exit_a[1]};exitDx=0;exitDy=0;"
            f"entryX={entry_a[0]};entryY={entry_a[1]};entryDx=0;entryDy=0;"
        )
    return style


def _watermark_geometry(
    w: float, h: float, canvas_w: int, canvas_h: int, max_frac: float = 0.4
) -> tuple[float, float, float, float]:
    """Scale a partner watermark to at most ``max_frac`` of the canvas width
    (keeping aspect) and centre it on the canvas (FIX-C). The watermark should
    read as a faint background mark like the SSAM/Brandart exemplars, never a
    foreground element that covers the diagram."""
    if w > max_frac * canvas_w:
        scale = (max_frac * canvas_w) / w
        w, h = w * scale, h * scale
    return (canvas_w - w) / 2.0, (canvas_h - h) / 2.0, w, h


def _emit_watermark(root: ET.Element, cell: dict, canvas_w: int, canvas_h: int) -> None:
    """Place a resolved (image) partner watermark, scaled + centred, BEHIND
    everything. The caller emits this first (document order == z-order) so it
    sits under the whole diagram; opacity comes verbatim from the contract
    ``watermark`` molecule style (a faint ~10–15%)."""
    x, y, w, h = _watermark_geometry(float(cell["w"]), float(cell["h"]), canvas_w, canvas_h)
    c = ET.SubElement(
        root, "mxCell",
        attrib={
            "id": _stable_id("brand", "watermark"),
            "value": "",
            "style": cell.get("style", "") or "",
            "vertex": "1", "parent": "1", "connectable": "0",
        },
    )
    ET.SubElement(
        c, "mxGeometry",
        attrib={"x": _num(x), "y": _num(y), "width": _num(w), "height": _num(h), "as": "geometry"},
    )


def _emit_customer_logo(root: ET.Element, cell: dict, x: float = 32.0, y: float = 10.0) -> float:
    """Place the customer logo at the canvas TOP-LEFT (in the ``branding`` slot)
    and return the x the diagram title should start at (just to its right). The
    logo is clamped to a header-sized box; an unresolved asset is the text-badge
    fallback (e.g. "ACME"), which still occupies the slot."""
    w = min(float(cell["w"]), 160.0)
    h = min(float(cell["h"]), 44.0)
    c = ET.SubElement(
        root, "mxCell",
        attrib={
            "id": _stable_id("brand", "customer-logo"),
            "value": cell.get("value", "") or "",
            "style": cell.get("style", "") or "",
            "vertex": "1", "parent": "1", "connectable": "0",
        },
    )
    ET.SubElement(
        c, "mxGeometry",
        attrib={"x": _num(x), "y": _num(y), "width": _num(w), "height": _num(h), "as": "geometry"},
    )
    return x + w + 12.0


def _emit_network_separator(root: ET.Element, sep: dict, contract: dict) -> None:
    """Emit the NETWORK separator geometry the skeleton layout placed in
    ``meta["networkSeparator"]``: the grey jump-gap bar (a standalone edge cell
    with explicit source/target points, like the gold standard) + its "NETWORK"
    caption. ``sep`` is ``{x, y0, y1}``."""
    M = _molecules_module()
    for c in M.network_separator(sep["x"], sep["y0"], sep["y1"], contract):
        if c.get("edge"):
            e_cell = ET.SubElement(
                root, "mxCell",
                attrib={
                    "id": _stable_id("netsep", c["id"]),
                    "value": c.get("value", "") or "",
                    "style": c.get("style", "") or "",
                    "edge": "1", "parent": "1", "connectable": "0",
                },
            )
            geom = ET.SubElement(e_cell, "mxGeometry", attrib={"relative": "1", "as": "geometry"})
            pts = c.get("points") or []
            (sx, sy), (tx, ty) = pts[0], pts[-1]
            # Endpoints as source/target points — the gold-standard floating-edge
            # form draw.io renders. Mirror them into the waypoint Array too so the
            # pure-Python preview renderer (which builds a floating edge's path
            # from Array waypoints, not source/target mxPoints) draws the bar
            # identically; the coincident endpoints are invisible in draw.io.
            ET.SubElement(geom, "mxPoint", attrib={"x": _num(sx), "y": _num(sy), "as": "sourcePoint"})
            ET.SubElement(geom, "mxPoint", attrib={"x": _num(tx), "y": _num(ty), "as": "targetPoint"})
            arr = ET.SubElement(geom, "Array", attrib={"as": "points"})
            for px, py in pts:
                ET.SubElement(arr, "mxPoint", attrib={"x": _num(px), "y": _num(py)})
        else:
            v_cell = ET.SubElement(
                root, "mxCell",
                attrib={
                    "id": _stable_id("netsep", c["id"]),
                    "value": c.get("value", "") or "",
                    "style": c.get("style", "") or "",
                    "vertex": "1", "parent": "1", "connectable": "0",
                },
            )
            ET.SubElement(
                v_cell, "mxGeometry",
                attrib={"x": _num(c["x"]), "y": _num(c["y"]),
                        "width": _num(c["w"]), "height": _num(c["h"]), "as": "geometry"},
            )


def _emit_diagram_badge_strip(
    root: ET.Element,
    diagram: Diagram,
    canvas_w: int,
    contract: dict,
    brand_packs: dict,
    icon_resolver,
    warnings: list[str],
) -> None:
    """Emit the diagram-level hyperscaler/runtime badge strip (top-right). Badges
    degrade to text-badges when the (usually .local) brand assets are absent —
    with ``icon_resolver``/``warnings`` threaded through so each degradation runs
    the same shape-index resolution leg and de-duplicated preflight WARNING as
    the group-badge path (``_place_molecule``)."""
    M = _molecules_module()
    if diagram.badges:
        x = canvas_w - 40
        y = 48.0
        for kind, coll in (("hyperscaler", "hyperscalers"), ("runtime", "runtimes")):
            for name in (diagram.badges.get(coll) or []):
                b = M.badge(kind, str(name), contract, brand_packs, icon_resolver, warnings)
                if not (b.get("value") or "").strip() and "shape=image" not in b.get("style", ""):
                    continue
                w, h = b["w"], b["h"]
                x -= w
                cell = ET.SubElement(
                    root, "mxCell",
                    attrib={
                        "id": _stable_id("dbadge", f"{kind}-{name}"),
                        "value": b.get("value", "") or "",
                        "style": b.get("style", "") or "",
                        "vertex": "1", "parent": "1", "connectable": "0",
                    },
                )
                ET.SubElement(
                    cell, "mxGeometry",
                    attrib={"x": _num(x), "y": _num(y), "width": _num(w), "height": _num(h), "as": "geometry"},
                )
                x -= 8


def _emit_slot_cell(root, cid, value, style, center, w, h):
    """Emit a top-level (parent="1") vertex centred on absolute ``center`` — the
    channel router's collision-free slot for an edge pill/label. Absolute (not
    edge-relative) placement is what makes the slot overlap-free by construction
    (Task 8e); the router guarantees no foreign edge crosses the rect, so
    z-order against the connectors is a non-issue."""
    cx, cy = center
    c = ET.SubElement(
        root, "mxCell",
        attrib={"id": cid, "value": value, "style": style,
                "vertex": "1", "parent": "1", "connectable": "0"},
    )
    ET.SubElement(
        c, "mxGeometry",
        attrib={"x": _num(cx - w / 2.0), "y": _num(cy - h / 2.0),
                "width": _num(w), "height": _num(h), "as": "geometry"},
    )


def _emit_channels_metadata(root: ET.Element, channels: list) -> None:
    """Task 12: serialize the router's reserved channel rects (gutters +
    corridors) as ONE invisible metadata vertex, ``id="sapdp:channels"``, its
    ``value`` a compact JSON array of ``{id, axis, rect:[x,y,w,h]}``.

    check-composition.py (Task 12's geometric gate) runs on the plain .drawio
    XML alone — no draw.io, no re-import of ``_channel_router`` — so it can't
    recompute ``RouteResult.channels`` itself; this cell is the only way it
    can see where the router intended edges to travel, for CHANNEL_DISCIPLINE.
    Zero footprint (1×1, ``visible=0``, no style outline) — never rendered,
    never collides with anything a GROUP_OVERLAP/PIERCING/TEXT_OVERLAP check
    would look at. Emitted once per diagram; absent entirely when routing
    isn't active (``layout == "greedy"``), which the gate must tolerate."""
    payload = [
        {"id": ch.id, "axis": ch.axis,
         "rect": [ch.rect.x, ch.rect.y, ch.rect.w, ch.rect.h]}
        for ch in channels
    ]
    cell = ET.SubElement(
        root, "mxCell",
        attrib={
            "id": "sapdp:channels",
            "value": json.dumps(payload, separators=(",", ":")),
            "style": "text;html=0;",
            "vertex": "1",
            "parent": "1",
            "visible": "0",
        },
    )
    ET.SubElement(
        cell, "mxGeometry",
        attrib={"x": "0", "y": "0", "width": "1", "height": "1", "as": "geometry"},
    )


def emit(
    diagram: Diagram,
    shape_index: "ShapeIndex | None" = None,
    layout: str = "auto",
) -> str:
    """Render the diagram to .drawio XML.

    ``layout`` selects the positioning backend:
      - ``"auto"`` / ``"zone"`` — the deterministic skeleton slot engine
        (``_skeleton_layout.py``); the default, no external dependency.
      - ``"greedy"`` — the legacy built-in 3×3 grid (debug only).
    """
    if shape_index is None:
        shape_index = ShapeIndex.load()
    nodes_by_id = {n.id: n for n in diagram.nodes}

    # Give user-zone nodes a default person icon when they declared neither a
    # service nor a generic icon — done BEFORE layout so the zone footprints
    # match what the renderer draws (both then agree the node is an icon).
    _group_type = {g.id: g.type for g in diagram.groups}
    for _n in diagram.nodes:
        if (
            _group_type.get(_n.group) == "user"
            and not _n.genericIcon
            and not (_n.service and shape_index.resolve(_n.service))
        ):
            _n.genericIcon = "user"

    # Layout backend. Default: the deterministic skeleton slot engine
    # (`_skeleton_layout.py`, no external dependency) — slot assignment (left /
    # top / center / right / bottom), footprint-driven molecule frame sizing and
    # flow-rank lane ordering. `--layout greedy` forces the legacy 3×3 grid
    # (debug only). "auto"/"zone"/"dot" all route to the skeleton engine now.
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "_skeleton_layout", Path(__file__).resolve().parent / "_skeleton_layout.py"
    )
    _sl = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_sl)
    icon_dim = _sl.icon_size(diagram.level)
    # Caption band the zone layout reserves under an icon node (see
    # _skeleton_layout._footprint: icon footprint height = icon + LABEL_H).
    # Router FIX-1: icon nodes are re-squared to icon_dim x icon_dim for
    # RENDERING (below), which drops this band from node_abs_geom — making the
    # caption invisible to the router's piercing check and label-slot obstacle
    # set. caption_reserve lets us build a SEPARATE, taller obstacle rect
    # (node_obstacle_geom) for the router while keeping node_abs_geom exactly
    # what's drawn (so ports/edges still anchor to the real icon border).
    caption_reserve = getattr(_sl, "LABEL_H", 24)

    if layout == "greedy":
        group_geo = layout_groups(diagram.groups)
        node_geo = {}
        edge_waypoints: dict[str, list[tuple[float, float]]] = {}
        canvas_w, canvas_h = CANVAS_W, CANVAS_H
    else:
        layout_result = _sl.compute_layout(diagram, shape_index)
        group_geo = layout_result["groups"]
        node_geo = layout_result["nodes"]
        edge_waypoints = layout_result["edges"]
        canvas_w, canvas_h = layout_result["canvas"]
        # layout_result["meta"] (slots / lanes / ranks) is the channel router's
        # input — consumed when routing is wired in (Task 8).

    # Molecule emission context (IR v2): the style contract + brand packs + an
    # icon resolver reusing the ShapeIndex path. Molecule cell styles come only
    # from the contract; missing brand assets degrade to text-badges (warnings
    # collected here, flushed to stderr at the end — never a hard failure).
    _M = _molecules_module()
    contract = _M.load_contract()
    brand_packs = _M.load_brand_packs()
    mol_warnings: list[str] = []

    def icon_resolver(name: str | None) -> str | None:
        if not name:
            return None
        svc = shape_index.resolve(name) or shape_index.resolve_generic(name)
        return _extract_image_uri(svc.get("drawioStyle")) if svc else None

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

    # 0. Customer branding (Task 7). Resolve the branding block ONCE (this also
    # records the preflight WARNING for any missing logo/watermark asset). The
    # partner watermark is emitted FIRST so it sits BEHIND everything — a faint,
    # centred, contract-opacity background mark (FIX-C); the customer logo goes
    # TOP-LEFT and the diagram title shifts to its right.
    brand_by_id: dict[str, dict] = {}
    if diagram.branding:
        brand_by_id = {
            c.get("id"): c
            for c in _M.branding_block(
                {"branding": diagram.branding, "title": diagram.title},
                contract, brand_packs, icon_resolver, mol_warnings,
            )
        }
    wm = brand_by_id.get("watermark")
    if wm is not None and "shape=image" in wm.get("style", ""):
        # A text fallback would just be noise over the canvas — only a genuinely
        # resolved image watermark is drawn.
        _emit_watermark(root, wm, canvas_w, canvas_h)

    # 1. Title — SAP-canonical: bold blue (#0070F2) + "- SAP BTP Solution
    # Diagram" suffix. Convention observed in EVERY official sample
    # (SAP_Task_Center_*.drawio, SAP_Private_Link_Service_L2, etc.).
    # Top-left, shifted right of the customer logo when one is present.
    title_x = 32.0
    logo = brand_by_id.get("customer-logo")
    if logo is not None:
        title_x = _emit_customer_logo(root, logo)
    title_id = _stable_id("title", diagram.title)
    title_w = max(int(round(canvas_w - title_x - 64)), 400)
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
                f"fontColor={PALETTE['btp_border']};fontSize=16;fontStyle=1;"
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
            "x": _num(title_x),
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
        # Users render as free icon+label molecules (no frame) per the SAP gold
        # standard — skip the box; their nodes parent to the root at absolute
        # coordinates (group_cell_ids stays without an entry for this group).
        if g.type == "user":
            continue
        x, y, w, h = group_geo[g.id]
        cell_id = _stable_id("g", g.id)
        group_cell_ids[g.id] = cell_id
        # IR v2 group types → contract-driven molecule frames (grown to the
        # layout footprint, but never smaller than the contract frame).
        if g.type in MOLECULE_GROUP_TYPES:
            # The layout already sized the frame (footprint ≥ contract min); the
            # builder reflows its decorations to that final size.
            show_chip = _M.subaccount_shows_chip(g.type, _group_type.get(g.parent))
            cells = _group_molecule_cells(g, contract, size=(float(w), float(h)),
                                          show_chip=show_chip)
            _place_molecule(
                root, cells, anchor_id=cell_id, anchor_parent="1",
                off_x=x, off_y=y, contract=contract, brand_packs=brand_packs,
                icon_resolver=icon_resolver, warnings=mol_warnings,
                anchor_size=(float(w), float(h)),
            )
            continue
        # The BTP layer carries a "SAP BTP" logo chip instead of a text label.
        g_value = "" if g.type == "btp-layer" else g.label
        g_cell = ET.SubElement(
            root,
            "mxCell",
            attrib={
                "id": cell_id,
                "value": g_value,
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
        if g.type == "btp-layer":
            _emit_sap_btp_badge(root, cell_id)

    for g in nested_groups:
        if g.id not in group_geo or g.parent not in group_geo:
            continue
        x, y, w, h = group_geo[g.id]
        px, py, _, _ = group_geo[g.parent]
        rel_x, rel_y = x - px, y - py
        cell_id = _stable_id("g", g.id)
        group_cell_ids[g.id] = cell_id
        parent_cell_id = group_cell_ids.get(g.parent, "1")
        # IR v2 group types → contract-driven molecule frames (nested: relative
        # to the parent cell's origin).
        if g.type in MOLECULE_GROUP_TYPES:
            show_chip = _M.subaccount_shows_chip(g.type, _group_type.get(g.parent))
            cells = _group_molecule_cells(g, contract, size=(float(w), float(h)),
                                          show_chip=show_chip)
            _place_molecule(
                root, cells, anchor_id=cell_id, anchor_parent=parent_cell_id,
                off_x=rel_x, off_y=rel_y, contract=contract, brand_packs=brand_packs,
                icon_resolver=icon_resolver, warnings=mol_warnings,
                anchor_size=(float(w), float(h)),
            )
            continue
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
    # Router FIX-1: obstacle geometry fed to the channel router for piercing
    # avoidance + pill/label slot placement. Identical to node_abs_geom for
    # every node EXCEPT icon nodes, whose rect is extended DOWNWARD by
    # caption_reserve so the router treats icon+caption as one obstacle box
    # (node_abs_geom itself stays icon-only — it's what's actually drawn, and
    # what ports/edges anchor to).
    node_obstacle_geom: dict[str, tuple[float, float, float, float]] = {}

    for n in diagram.nodes:
        # IR v2 leaf archetypes → contract-driven molecules. Position origin
        # comes from the existing layout (Task 6 fixes the footprint); the
        # molecule owns the cell(s) + style. Anchor id == the node's stable id so
        # edges connect to it exactly as for v1 nodes.
        if n.type in MOLECULE_NODE_TYPES:
            if n.id in node_geo:
                nx, ny, _, _ = node_geo[n.id]
            else:
                nx, ny = node_xy.get(n.id, (0, 0))
            node_cell_id = _stable_id("n", n.id)
            parent_cell_id, off_x, off_y = "1", float(nx), float(ny)
            if n.group and n.group in group_cell_ids and n.group in group_geo:
                parent_cell_id = group_cell_ids[n.group]
                gx, gy, _, _ = group_geo[n.group]
                off_x, off_y = float(nx - gx), float(ny - gy)
            if n.type == "product":
                cells = _M.product_box(n, contract, icon_resolver)
            elif n.type == "db":
                cells = [_M.db_cell(n, contract)]
            else:  # chip
                cells = [_M.chip_cell(n, contract)]
            _place_molecule(
                root, cells, anchor_id=node_cell_id, anchor_parent=parent_cell_id,
                off_x=off_x, off_y=off_y, contract=contract, brand_packs=brand_packs,
                icon_resolver=icon_resolver, warnings=mol_warnings,
            )
            node_abs_geom[n.id] = (nx, ny, cells[0]["w"], cells[0]["h"])
            # Molecule frames have no floating caption below them (any title/
            # subtitle is drawn INSIDE the frame) — obstacle == drawn rect.
            node_obstacle_geom[n.id] = node_abs_geom[n.id]
            continue

        gtype = _group_type.get(n.group)
        if gtype in BACKEND_GROUP_TYPES:
            # RIGHT-zone backend system → white box with icon-left + title.
            style, label = _backend_box(n, gtype, shape_index)
            is_icon = False
        else:
            style, is_icon, label = _node_style(n, shape_index)
        # The skeleton engine fills node_geo with exact footprint cells. Icon nodes
        # are re-squared to the level icon size (icon_dim) and top-centred (the
        # caption floats below); box/plain nodes keep their footprint. The
        # greedy fallback path uses node_xy + default sizes.
        if n.id in node_geo:
            x, y, w, h = node_geo[n.id]
            if is_icon:
                # Square icon at the top-centre of its footprint cell; the
                # caption (verticalLabelPosition=bottom in the icon style)
                # floats below it, in the space the zone layout reserved.
                footprint_h = h                            # icon + caption band
                x = x + (w - icon_dim) / 2
                w = h = icon_dim
            # else: box / plain node → keep the footprint size from zone layout.
        else:
            x, y = node_xy.get(n.id, (0, 0))
            if is_icon:
                footprint_h = icon_dim + caption_reserve
                w = h = icon_dim
            else:
                w, h = NODE_W, NODE_H
        node_abs_geom[n.id] = (x, y, w, h)
        # Router FIX-1: for icon nodes, the obstacle rect extends over the
        # caption band the zone layout reserved below the icon (footprint_h,
        # which is >= icon_dim + caption_reserve by construction) — so the
        # router's piercing check and label-slot obstacle set see icon+caption
        # as one box. Box/plain nodes have no floating caption: obstacle ==
        # drawn rect.
        node_obstacle_geom[n.id] = (
            (x, y, w, max(h, footprint_h)) if is_icon else (x, y, w, h)
        )

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

        # Step number circle — 28×28 ellipse with gradient fill + bold white
        # digit (canonical numbers.xml is 30×30; 28 reads cleanly on 48px
        # icons). Sits half-outside the node's top-left corner.
        if n.step is not None and 1 <= n.step <= 99:
            grad, fill = _STEP_KIND_GRADIENT.get(
                n.stepKind, _STEP_KIND_GRADIENT["default"]
            )
            step_w, step_h = 28, 28
            step_cell = ET.SubElement(
                root,
                "mxCell",
                attrib={
                    "id": _stable_id("st", n.id),
                    "value": (
                        f"<p style=\"line-height: 100%;\"><b>"
                        f"<font face=\"arial black\" "
                        f"style=\"font-size: 13px;\" color=\"#ffffff\">"
                        f"{n.step}</font></b></p>"
                    ),
                    "style": (
                        f"ellipse;whiteSpace=wrap;html=1;aspect=fixed;"
                        f"gradientColor={grad};strokeColor=none;"
                        f"gradientDirection=east;fillColor={fill};rounded=0;"
                        f"fontFamily=Helvetica;fontSize=10;fontColor=#FFFFFF;"
                        f"align=center;verticalAlign=middle;"
                    ),
                    "vertex": "1",
                    "parent": node_cell_id,
                    "connectable": "0",
                },
            )
            # Half-outside the node's top-left corner (centred on the corner).
            ET.SubElement(
                step_cell,
                "mxGeometry",
                attrib={
                    "x": "-14",
                    "y": "-14",
                    "width": str(step_w),
                    "height": str(step_h),
                    "as": "geometry",
                },
            )

    # 4. Edges — the channel router (Task 8) computes overlap-free waypoints
    #    through reserved corridors + barycenter-distributed exit/entry ports,
    #    plus collision-free pill/label slots. It routes against the ACTUAL
    #    drawn node geometry (node_abs_geom), not the footprint cells, so ports
    #    hug the real icon/box edges. The greedy debug layout keeps the legacy
    #    side-midpoint distribution and draw.io default routing.
    route_result = None
    _crmod = _channel_router_module()
    if layout != "greedy":
        router_layout = dict(layout_result)
        router_layout["nodes"] = dict(node_abs_geom)
        # FIX-1: caption-aware obstacle rects (icon+caption for icon nodes) —
        # kept SEPARATE from "nodes" so ports/exit-entry points still anchor
        # to the real (icon-only) drawn geometry; only the piercing check and
        # pill/label slot obstacle set see the taller box.
        router_layout["node_obstacles"] = dict(node_obstacle_geom)
        route_result = _crmod.route(diagram, router_layout)
        edge_anchors = dict(route_result.port_fracs)
        edge_waypoints = dict(route_result.waypoints)
        # Task 12: publish the channels the router reserved so the geometric
        # gate can verify CHANNEL_DISCIPLINE without re-running the router.
        _emit_channels_metadata(root, route_result.channels)
    else:
        edge_anchors = _distribute_anchors(diagram.edges, node_abs_geom)
    for e in diagram.edges:
        edge_id = _stable_id("e", e.id)
        # For pill-rendered kinds (trust, authenticate, authorize,
        # generic_protocol, annotation), the visible label sits in a
        # separate rounded vertex child. drawio does NOT honour arcSize on
        # inline edge labels, so the multi-cell pattern is the only way.
        has_pill = e.kind in _EDGE_KIND_PILL or e.kind == "annotation"
        # The channel router (8e) drops labels + protocol pills into
        # collision-free slots, emitted as ABSOLUTE cells; so when routing is
        # active the edge itself carries no inline label. The greedy debug
        # path keeps the legacy inline label / edge-child pills.
        pill_center = route_result.pill_pos.get(e.id) if route_result else None
        label_center = route_result.label_pos.get(e.id) if route_result else None
        inline_value = e.label if (route_result is None and not has_pill) else ""
        # IR v2 flow family → contract edge molecule (edge-identity / -provisioning
        # / -master-data / -transport / -firewall / -default); else the v1 style.
        if e.flowFamily:
            edge_style = _flow_family_edge_style(
                e, contract, *edge_anchors.get(e.id, (None, None))
            )
        else:
            edge_style = _edge_style(e, *edge_anchors.get(e.id, (None, None)))
        e_cell = ET.SubElement(
            root,
            "mxCell",
            attrib={
                "id": edge_id,
                "value": inline_value,
                "style": edge_style,
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

        # IR v2 protocol pill (e.g. "SCIM", "SAML2/OIDC"): a contract-styled
        # vertex. With routing active it's placed at the router's collision-free
        # absolute slot (pill_center); the greedy path keeps the legacy
        # edge-child at (0,0).
        if e.pill:
            pcell = _M.pill(e, contract)
            if pill_center is not None:
                pw, ph = _crmod.pill_dims(e.pill)
                _emit_slot_cell(root, _stable_id("pp", e.id), pcell["value"],
                                pcell["style"], pill_center, pw, ph)
            else:
                p_cell = ET.SubElement(
                    root,
                    "mxCell",
                    attrib={
                        "id": _stable_id("pp", e.id),
                        "value": pcell["value"],
                        "style": pcell["style"],
                        "vertex": "1",
                        "parent": edge_id,
                        "connectable": "0",
                    },
                )
                ET.SubElement(
                    p_cell,
                    "mxGeometry",
                    attrib={
                        "x": "0", "y": "0",
                        "width": _num(pcell["w"]), "height": _num(pcell["h"]),
                        "relative": "1", "as": "geometry",
                    },
                )

        # Edge label. With routing active the router (8e) gives a collision-free
        # absolute slot (label_center): SAP-canonical pill kinds keep their
        # rounded coloured chip; plain / flowFamily labels get a white-backed
        # text box (hiding the connector behind the text). The greedy path keeps
        # the legacy edge-child pill for pill kinds (plain labels stay inline).
        #
        # ONE block: `pill_def` is computed and consumed right here (a future
        # reorder can't strand a read of `pill_def` past where it's set), and
        # `label_dims` is computed exactly once, up front, for whichever style
        # below ends up using it.
        if e.label and (has_pill or label_center is not None):
            lw, lh = _crmod.label_dims(e.label)
            if has_pill:
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
                lstyle = (
                    f"rounded=1;whiteSpace=wrap;html=1;arcSize=50;"
                    f"strokeColor={pill_def['stroke']};"
                    f"fillColor={pill_def['fill']};"
                    f"fontColor={pill_def['fontColor']};"
                    f"fontStyle=1;strokeWidth=1.5;fontSize=10;"
                    f"align=center;verticalAlign=middle;"
                )
                if label_center is not None:
                    _emit_slot_cell(root, _stable_id("p", e.id), e.label, lstyle,
                                    label_center, lw, lh)
                else:
                    # greedy fallback: edge-child pill centred on the edge midpoint
                    pill_w = max(56, min(168, len(e.label) * 6 + 18))
                    pill = ET.SubElement(
                        root,
                        "mxCell",
                        attrib={
                            "id": _stable_id("p", e.id),
                            "value": e.label,
                            "style": lstyle,
                            "vertex": "1",
                            "parent": edge_id,
                            "connectable": "0",
                        },
                    )
                    pill_geom = ET.SubElement(
                        pill,
                        "mxGeometry",
                        attrib={"width": str(pill_w), "height": "22", "relative": "1", "as": "geometry"},
                    )
                    ET.SubElement(
                        pill_geom,
                        "mxPoint",
                        attrib={"x": str(-pill_w // 2), "y": "-11", "as": "offset"},
                    )
            else:
                # plain / flowFamily label: white-backed text box (routing active)
                lstyle = (
                    f"text;html=1;whiteSpace=wrap;rounded=0;strokeColor=none;"
                    f"fillColor=#FFFFFF;fontColor={PALETTE['text']};fontSize=10;"
                    f"align=center;verticalAlign=middle;"
                )
                _emit_slot_cell(root, _stable_id("p", e.id), e.label, lstyle,
                                label_center, lw, lh)

    # 5. SAP essential presets — embed pre-composed organisms (User and
    # client, Cloud Connector, SAML/OIDC, 3rd party IdP and protocols, …)
    # at the requested coordinates. Uses raw XML from essentials.xml so
    # the visual matches SAP's curated compositions verbatim.
    for preset in diagram.presets:
        _embed_preset(root, preset, shape_index)

    # 5b. NETWORK separator — the vertical bar in the center→right gutter,
    # spanning the right stack (Task 7). The skeleton layout computed its
    # geometry (or None when there's no right stack / it's opted out).
    if layout != "greedy":
        sep = layout_result["meta"].get("networkSeparator")
        if sep:
            _emit_network_separator(root, sep, contract)

    # 6. Legend molecule (bottom-right). Two paths:
    #    - User asked for 'sap' or 'sap-short' preset → embed SAP essential
    #    - Otherwise auto-generate based on actual styles used.
    _emit_legend(root, diagram, canvas_w, canvas_h, shape_index)

    # 7. IR v2 metadata: the diagram-level hyperscaler/runtime badge strip
    # (customer branding + watermark are emitted up front, in step 0/1).
    _emit_diagram_badge_strip(
        root, diagram, canvas_w, contract, brand_packs, icon_resolver, mol_warnings
    )

    # Flush molecule preflight warnings (missing brand assets → text fallback)
    # to stderr, de-duplicated. Never a hard failure.
    for w in dict.fromkeys(mol_warnings):
        print(f"WARNING: {w}", file=sys.stderr)

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
        choices=("auto", "zone", "greedy"),
        default="auto",
        help=(
            "Layout backend. 'auto'/'zone' use the deterministic "
            "skeleton slot engine (default). 'greedy' forces the legacy "
            "3x3 grid (debug only)."
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
