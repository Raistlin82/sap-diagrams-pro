#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""Deterministic *skeleton* layout backend for sap-diagrams-pro.

Successor to ``_zone_layout.py``. SAP solution diagrams read along a horizontal
axis — consumers on the LEFT, the SAP BTP platform in the CENTER, backing systems
(cloud tiers, SAP apps, on-prem, 3rd-party) on the RIGHT — with a governance
strip banded across the TOP and a legend/caption band at the BOTTOM. This engine
formalises that composition into a **slot model** and sizes every container to
its contents.

Slots::

    SLOTS = ("branding", "left", "top", "center", "right", "bottom")

Slot assignment for each top-level group (explicit ``zone`` / ``position`` still
override, exactly as the old zone engine did):

  * ``user``                                   → left
  * ``governance``                             → top   (band above the columns)
  * ``btp-layer`` / top-level ``subaccount``   → center
  * ``cloud-tier`` / ``sap-app`` / ``non-sap`` / ``third-party`` / ``external`` /
    ``custom-app``                             → right
  * ``legend``                                 → bottom
  * an **identity** group (its nodes resolve to the Cloud Identity Services
    family): parented to the BTP group ⇒ a bottom band INSIDE that frame;
    top-level ⇒ the center column, placed just below the main center frame.

Flow ordering: ``rank(node)`` is the longest-path depth in the edge DAG (cycles
broken at back-edges by IR order); each lane's siblings are sorted by
``(rank, ir_index)`` with edge-less nodes trailing (kept in IR order). This gives
left→right / top→bottom reading order that follows the actual flow.

Footprint-driven sizing (the reconciliation with Task 5's molecules): a molecule
FRAME (subaccount / governance / cloud-tier / custom-app) is sized to
``max(contract-minimum, packed-children + contract insets)`` and that FINAL size
is handed back to the molecule builder so bottom-anchored decorations reflow to
the true edge; leaf molecule nodes (product / db / chip) reserve their real
molecule footprint (a product box is far larger than a bare icon), not an icon
cell. Footprints come from ``_molecules.footprint`` / ``_molecules.frame_insets``
(contract geometry), leaf icon/box footprints from ``_footprint`` below.

Returns the shape the renderer expects (identical to the old zone backend) plus a
``meta`` block consumed by the channel router (Task 8)::

    {"groups": {gid:(x,y,w,h)}, "nodes": {nid:(x,y,w,h)}, "edges": {},
     "canvas": (w,h),
     "meta": {"slots": {slot:[gid…]}, "slot_of": {gid:slot},
              "lanes": {gid:[nid…]}, "ranks": {nid:int}, "identity": [gid…],
              "columns": {"left"|"center"|"right": (x0,x1)}}}

``slot_of`` is the reverse of ``slots`` (every top-level group's own slot).
``lanes`` covers EVERY group — including node-less containers that only nest
other groups, and empty leaf frames — with ``[]`` when it has no direct nodes.
``columns`` is the x-extent of each vertical column (zero-width at the cursor
when a column is empty): the single source of truth for column boundaries
shared with Task 7's NETWORK separator and Task 8's router.

A group whose ``parent`` cycles (A→B→A, or a self-parent) or dangles on an id
that doesn't exist is placed at top level as a fallback (with a stderr
warning) rather than silently dropped — see ``_group_reaches_top_level``.

All coordinates absolute (drawio top-left origin). Pure-Python, no external
process, no datetime/random → byte-identical re-runs.
"""
from __future__ import annotations

import importlib.util as _ilu
import math
import sys
from pathlib import Path
from typing import Any

# ── Sizing atoms (moved UNCHANGED from _zone_layout.py; kept in sync with the
#    renderer via icon_size()) ────────────────────────────────────────────────
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
BAND_GAP = 44         # gap between a top/bottom band and the column block

# Backend box molecule (RIGHT zone: SAP apps / on-prem / 3rd-party)
BOX_MIN_W, BOX_MAX_W = 156, 240
BOX_H, BOX_H_SUB = 60, 74

BACKEND_TYPES = {"sap-app", "non-sap", "third-party", "external"}
# IR v2 molecule types (kept in sync with generate-drawio.py).
MOLECULE_GROUP_TYPES = {"subaccount", "governance", "cloud-tier", "custom-app"}
MOLECULE_NODE_TYPES = {"product", "db", "chip"}

SLOTS = ("branding", "left", "top", "center", "right", "bottom")

# A group is an "identity" group when its nodes resolve to the SAP Cloud Identity
# Services family (Identity Authentication / Provisioning / Authorization & Trust
# / Keystore / …). Detected by the resolved service's techId or its raw name.
_IDENTITY_TECHID_MARKERS = (
    "cloud-identity", "identity-authentication",
    "identity-provisioning",
    "identity-provisoning",  # sic — assets/shape-index.json misspells this
    # techId ("…-sap-identity-provisoning") for BOTH its S/M ("32072-…") and
    # L ("48072-…") entries; kept alongside the correct spelling so the
    # fallback matches the real (typo'd) data instead of silently never
    # firing, without assuming the upstream data typo gets fixed.
    "authorization-and-trust",
)
_IDENTITY_NAME_MARKERS = (
    "identity authentication", "identity provisioning",
    "authorization & trust", "authorization and trust", "cloud identity",
)


# ── Molecule module (footprints from the style contract) ─────────────────────
_MOL = None


def _molecules():
    """Lazily load scripts/_molecules.py (path-based, so it works from the CLI,
    the tests' load_script, and emit()'s importlib load alike). Cached."""
    global _MOL
    if _MOL is None:
        spec = _ilu.spec_from_file_location(
            "_molecules", Path(__file__).resolve().parent / "_molecules.py"
        )
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MOL = mod
    return _MOL


def icon_size(level: str) -> int:
    """Canonical square service/generic icon size for the given level."""
    return 32 if str(level).upper() == "L2" else 48


def _text_w(s: str) -> float:
    return min(TEXT_MAX, max(TEXT_MIN, len(s or "") * CHAR_W + 12))


def _node_is_icon(node, group_type: str, shape_index) -> bool:
    # Must agree with the renderer's _node_style: an icon is drawn only when a
    # genericIcon is set or the service resolves to a shape.
    if getattr(node, "genericIcon", None):
        return True
    svc = (shape_index.resolve(getattr(node, "service", None))
           if (shape_index and getattr(node, "service", None)) else None)
    return bool(svc and svc.get("drawioStyle"))


def _footprint(node, group_type: str, level: str, shape_index) -> tuple[float, float, bool]:
    """Return (width, height, is_icon) a *v1* leaf node occupies in layout space.

    Icon footprints reserve room for the caption underneath; backend boxes are
    sized to hold an icon + title (+ optional subtitle); plain boxes wrap a label.
    (Moved UNCHANGED from _zone_layout.py.)
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
    """Place item (w,h) boxes in 'row' | 'col' | 'grid'. Returns (positions,W,H).

    (Moved UNCHANGED from _zone_layout.py.)"""
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
    """(x, top, bottom) padding for a *v1* group box."""
    if group_type == "user":
        return 8.0, 26.0, 8.0            # frameless-ish: small label reserve
    if group_type == "btp-layer":
        return 18.0, (54.0 if has_children else 44.0), 18.0   # logo badge + lane labels
    return 16.0, 32.0, 14.0


def _pack_mode(group, group_type: str, n: int, *, row_max: int) -> str:
    """'row' | 'col' | 'grid' to pack ``n`` same-tier items inside ``group``.

    Single source of truth for the "row when there are few enough items, else
    grid" packing decision — shared by ``_leaf_mode`` (a childless group's
    direct nodes) and, inside ``measure()``, both a has-children group's OWN
    direct-node pack and its outer children/lane pack. Those three used to
    each inline a copy of this threshold check; the has-children copies had
    silently drifted from ``_leaf_mode`` (they dropped the user/backend-box
    "col" preference below), so a person/backend-box group with nested
    children would pack differently than one without. An explicit
    ``group.flow`` always wins, regardless of caller.
    """
    flow = getattr(group, "flow", None)
    if flow in ("row", "col", "grid"):
        return flow
    if group_type == "user" or group_type in BACKEND_TYPES:
        return "col"                      # stack people / backend boxes vertically
    return "row" if n <= row_max else "grid"


def _leaf_mode(group, group_type: str, n: int) -> str:
    row_max = 2 if group_type in MOLECULE_GROUP_TYPES else 4
    return _pack_mode(group, group_type, n, row_max=row_max)


# ── Flow ranking ─────────────────────────────────────────────────────────────
def _ranks(diagram) -> dict[str, int]:
    """``rank(node)`` = longest-path depth in the full edge DAG.

    All edges define the graph; only genuine **cycles** are broken, at their
    back-edge, chosen deterministically by IR order. A depth-first search that
    visits roots and neighbours in IR order flags an edge ``u→v`` as a back-edge
    exactly when ``v`` is still on the recursion stack (a real cycle). The
    remaining edges form a DAG; relaxing them in the DFS's reverse-finish
    (topological) order yields exact longest-path depths. Deterministic — no set
    iteration escapes into the output order."""
    ir_index = {n.id: i for i, n in enumerate(diagram.nodes)}
    adj: dict[str, list[str]] = {}
    for e in diagram.edges:
        if e.source in ir_index and e.target in ir_index:
            adj.setdefault(e.source, []).append(e.target)
    for k in adj:                         # neighbours in IR order → deterministic
        adj[k].sort(key=lambda t: ir_index[t])

    WHITE, GREY, BLACK = 0, 1, 2
    color = {n.id: WHITE for n in diagram.nodes}
    topo: list[str] = []
    back: set[tuple[str, str]] = set()
    for root in sorted((n.id for n in diagram.nodes), key=lambda x: ir_index[x]):
        if color[root] != WHITE:
            continue
        color[root] = GREY
        stack = [(root, iter(adj.get(root, [])))]
        while stack:
            u, it = stack[-1]
            v = next(it, None)
            while v is not None and color[v] != WHITE:
                if color[v] == GREY:      # edge into an ancestor → cycle back-edge
                    back.add((u, v))
                v = next(it, None)
            if v is None:
                color[u] = BLACK
                topo.append(u)
                stack.pop()
            else:
                color[v] = GREY
                stack.append((v, iter(adj.get(v, []))))
    topo.reverse()

    rank = {n.id: 0 for n in diagram.nodes}
    for u in topo:
        for v in adj.get(u, []):
            if (u, v) in back:
                continue
            if rank[v] < rank[u] + 1:
                rank[v] = rank[u] + 1
    return rank


def _has_any_edge(diagram) -> set[str]:
    touched: set[str] = set()
    for e in diagram.edges:
        touched.add(e.source)
        touched.add(e.target)
    return touched


# ── Group-parent cycle detection ─────────────────────────────────────────────
def _group_reaches_top_level(gid: str, groups_by_id: dict) -> bool:
    """True when walking ``gid``'s ``.parent`` chain terminates at a genuine
    top-level group (``parent`` is ``None``).

    False when the chain cycles back on itself (a self-parent, or a longer
    cycle like A→B→A) or dangles on a parent id that isn't a real group.
    ``compute_layout`` only measures/places groups reachable from a top-level
    ancestor by construction (each group is nested via its parent's own
    measure() recursion); a group stuck in either failure mode would
    otherwise never be reachable from anywhere and — together with its nodes
    — silently vanish from the emitted layout instead of surfacing the
    malformed input.
    """
    seen: set[str] = set()
    cur = gid
    while True:
        if cur in seen:
            return False                  # cycle (self-parent included)
        seen.add(cur)
        g = groups_by_id.get(cur)
        if g is None:
            return False                  # dangling parent reference
        if not g.parent:
            return True                   # reached a genuine top-level root
        cur = g.parent


# ── Identity detection ───────────────────────────────────────────────────────
def _is_identity_group(group, nodes_by_group, shape_index) -> bool:
    nodes = nodes_by_group.get(group.id, [])
    if not nodes:
        return False
    hits = 0
    for n in nodes:
        svc = getattr(n, "service", None)
        hit = bool(svc) and any(m in svc.lower() for m in _IDENTITY_NAME_MARKERS)
        if not hit and shape_index and svc:
            r = shape_index.resolve(svc)
            if r:
                tech = (r.get("techId") or "").lower()
                hit = any(m in tech for m in _IDENTITY_TECHID_MARKERS)
        if hit:
            hits += 1
    # A dedicated identity group: every member is an identity-family service.
    return hits >= 1 and hits == len(nodes)


class _Meas:
    __slots__ = ("gid", "w", "h", "node_rel", "child_rel")

    def __init__(self, gid, w, h, node_rel, child_rel):
        self.gid = gid                    # group id (or None for synthetic)
        self.w, self.h = w, h
        self.node_rel = node_rel          # [(node, rx, ry, w, h)]
        self.child_rel = child_rel        # [(_Meas, rx, ry)]


def compute_layout(diagram, shape_index) -> dict[str, Any]:
    level = diagram.level
    M = _molecules()
    contract = M.load_contract()

    groups_by_id = {g.id: g for g in diagram.groups}

    # A group whose .parent chain cycles (A→B→A, self-parent) or dangles on a
    # non-existent id never reaches a parent=None root, so it would never be
    # visited by the top-level-only measure/place pass below — silently
    # dropping it AND its nodes. Detect those up front, warn, and fall back to
    # placing each one at top level (see _group_reaches_top_level).
    stuck_ids = {g.id for g in diagram.groups
                 if not _group_reaches_top_level(g.id, groups_by_id)}
    for gid in stuck_ids:
        print(f"WARNING: group {gid!r} has a cyclic/unreachable parent; "
              "placing at top level", file=sys.stderr)

    children_by_parent: dict[str, list] = {}
    for g in diagram.groups:
        # A stuck group is placed at top level instead (below), so it must NOT
        # also be measured as a nested child via its cyclic/dangling parent —
        # that would recurse forever (A contains B contains A…).
        if g.parent and g.id not in stuck_ids:
            children_by_parent.setdefault(g.parent, []).append(g)
    nodes_by_group: dict[str, list] = {}
    orphans: list = []
    for n in diagram.nodes:
        if n.group and n.group in groups_by_id:
            nodes_by_group.setdefault(n.group, []).append(n)
        else:
            orphans.append(n)

    # Flow ranking → per-lane node order.
    ranks = _ranks(diagram)
    edged = _has_any_edge(diagram)
    ir_index = {n.id: i for i, n in enumerate(diagram.nodes)}

    def flow_sorted(nodes: list) -> list:
        # (edge-less trail, rank, ir_index): flow-connected nodes first, ordered
        # by longest-path depth then IR; edge-less nodes keep IR order after.
        return sorted(
            nodes,
            key=lambda n: (0 if n.id in edged else 1, ranks.get(n.id, 0), ir_index[n.id]),
        )

    identity_ids = {
        g.id for g in diagram.groups
        if _is_identity_group(g, nodes_by_group, shape_index)
    }

    lanes: dict[str, list] = {}

    # ---- measure (bottom-up) ------------------------------------------------
    def measure(group) -> _Meas:
        gtype = group.type
        is_mol = gtype in MOLECULE_GROUP_TYPES
        direct = flow_sorted(nodes_by_group.get(group.id, []))
        if direct:
            lanes[group.id] = [n.id for n in direct]
        children = children_by_parent.get(group.id, [])
        node_fps = [(n, *_node_footprint(n, gtype, level, shape_index, contract, M))
                    for n in direct]

        node_rel: list = []
        child_rel: list = []

        # --- pack this group's content into (content_w, content_h) -----------
        if children:
            # identity sub-groups form a bottom band inside the frame.
            id_kids = [c for c in children if c.id in identity_ids]
            rest_kids = [c for c in children if c.id not in id_kids]
            rest_meas = [measure(c) for c in rest_kids]
            id_meas = [measure(c) for c in id_kids]

            blocks: list[tuple[float, float]] = []
            direct_pack = None
            if node_fps:
                dmode = _leaf_mode(group, gtype, len(node_fps))
                dpos, dW, dH = _pack([(w, h) for _, w, h in node_fps], dmode, NODE_GAP)
                direct_pack = (dpos, dW, dH)
                blocks.append((dW, dH))
            blocks += [(cm.w, cm.h) for cm in rest_meas]
            bmode = _pack_mode(group, gtype, len(blocks), row_max=3)
            bpos, mainW, mainH = _pack(blocks, bmode, LANE_GAP)

            # optional identity bottom band (stacked under the main content)
            if id_meas:
                ipos, idW, idH = _pack([(cm.w, cm.h) for cm in id_meas], "row", LANE_GAP)
                content_w = max(mainW, idW)
                content_h = mainH + LANE_GAP + idH
                main_dx = (content_w - mainW) / 2.0
                id_dy = mainH + LANE_GAP
                id_dx = (content_w - idW) / 2.0
            else:
                content_w, content_h = mainW, mainH
                main_dx = id_dy = id_dx = 0.0

            pad_x, pad_top, pad_bot = _insets(group, gtype, is_mol, bool(children), M, contract)
            box_w, box_h = _frame_size(group, gtype, is_mol, content_w, content_h,
                                       pad_x, pad_top, pad_bot, M, contract)
            # widen the inset origin so packed content is centred in the frame
            extra_x = (box_w - (content_w + 2 * pad_x)) / 2.0
            base_x = pad_x + max(0.0, extra_x) + main_dx

            idx = 0
            if direct_pack:
                bx, by = bpos[0]
                dpos, _, _ = direct_pack
                for (n, w, h), (rx, ry) in zip(node_fps, dpos):
                    node_rel.append((n, base_x + bx + rx, pad_top + by + ry, w, h))
                idx = 1
            for cm, (cx, cy) in zip(rest_meas, bpos[idx:]):
                child_rel.append((cm, base_x + cx, pad_top + cy))
            if id_meas:
                idbase_x = pad_x + max(0.0, extra_x) + id_dx
                for cm, (cx, cy) in zip(id_meas, ipos):
                    child_rel.append((cm, idbase_x + cx, pad_top + id_dy + cy))
        else:
            mode = _leaf_mode(group, gtype, len(node_fps))
            positions, content_w, content_h = _pack([(w, h) for _, w, h in node_fps],
                                                    mode, NODE_GAP)
            pad_x, pad_top, pad_bot = _insets(group, gtype, is_mol, False, M, contract)
            box_w, box_h = _frame_size(group, gtype, is_mol, content_w, content_h,
                                       pad_x, pad_top, pad_bot, M, contract)
            extra_x = (box_w - (content_w + 2 * pad_x)) / 2.0
            base_x = pad_x + max(0.0, extra_x)
            for (n, w, h), (rx, ry) in zip(node_fps, positions):
                node_rel.append((n, base_x + rx, pad_top + ry, w, h))

        return _Meas(group.id, box_w, box_h, node_rel, child_rel)

    top_level = [g for g in diagram.groups if not g.parent or g.id in stuck_ids]
    measures = {g.id: measure(g) for g in top_level}

    # ---- assign slots -------------------------------------------------------
    slot_of = {g.id: _slot(g, g.id in identity_ids) for g in top_level}
    slots: dict[str, list] = {s: [] for s in SLOTS}
    for g in top_level:
        slots[slot_of[g.id]].append(g.id)

    cols: dict[str, list] = {"left": [], "center": [], "right": []}
    for g in top_level:
        s = slot_of[g.id]
        if s in cols:
            cols[s].append(g)
    for c in cols:
        cols[c].sort(key=lambda g: (_band(g, g.id in identity_ids), top_level.index(g)))

    top_groups = [groups_by_id[i] for i in slots["top"]]
    bottom_groups = [groups_by_id[i] for i in slots["bottom"]]

    # ---- place columns ------------------------------------------------------
    col_w = {c: (max((measures[g.id].w for g in gs), default=0.0)) for c, gs in cols.items()}
    col_h = {c: (sum(measures[g.id].h for g in gs) + max(0, len(gs) - 1) * ZONE_VGAP)
             for c, gs in cols.items()}
    max_h = max(col_h.values()) if any(col_h.values()) else 0.0

    top_band_h = max((measures[g.id].h for g in top_groups), default=0.0)
    content_top = TOP_BAND + (top_band_h + BAND_GAP if top_groups else 0.0)

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

    col_center_x: dict[str, float] = {}
    # x-extent (x0, x1) of each column, regardless of whether it holds any
    # content — a zero-width (cursor, cursor) slice when empty — so Task 7's
    # NETWORK separator (between "center" x1 and "right" x0) and Task 8's
    # router share this engine's own column geometry instead of recomputing
    # it (see meta["columns"] below).
    col_x_extent: dict[str, tuple[float, float]] = {}
    cursor = float(MARGIN)
    for c in ("left", "center", "right"):
        gs = cols[c]
        x0 = cursor
        if not gs:
            col_x_extent[c] = (x0, x0)
            continue
        cw = col_w[c]
        col_center_x[c] = cursor + cw / 2.0
        y = content_top + (max_h - col_h[c]) / 2.0
        for g in gs:
            m = measures[g.id]
            place(m, cursor + (cw - m.w) / 2.0, y)
            y += m.h + ZONE_VGAP
        col_x_extent[c] = (x0, x0 + cw)
        cursor += cw + ZONE_HGAP

    columns_right = cursor - ZONE_HGAP if cursor > MARGIN else MARGIN
    columns_bottom = content_top + max_h

    # ---- place the TOP band (governance), centred over the center column ----
    def _center_x_fallback() -> float:
        return col_center_x.get("center") or ((MARGIN + columns_right) / 2.0)

    band_right = columns_right
    if top_groups:
        widths = [measures[g.id].w for g in top_groups]
        total = sum(widths) + max(0, len(top_groups) - 1) * ZONE_HGAP
        start = _center_x_fallback() - total / 2.0
        start = max(float(MARGIN), start)
        x = start
        for g in top_groups:
            m = measures[g.id]
            place(m, x, TOP_BAND + (top_band_h - m.h) / 2.0)
            x += m.w + ZONE_HGAP
        band_right = max(band_right, x - ZONE_HGAP)

    # ---- place the BOTTOM band (legend/caption) -----------------------------
    bottom = columns_bottom
    if bottom_groups:
        widths = [measures[g.id].w for g in bottom_groups]
        total = sum(widths) + max(0, len(bottom_groups) - 1) * ZONE_HGAP
        start = max(float(MARGIN), _center_x_fallback() - total / 2.0)
        x = start
        by = columns_bottom + BAND_GAP
        band_h = max(measures[g.id].h for g in bottom_groups)
        for g in bottom_groups:
            m = measures[g.id]
            place(m, x, by + (band_h - m.h) / 2.0)
            x += m.w + ZONE_HGAP
        band_right = max(band_right, x - ZONE_HGAP)
        bottom = by + band_h

    canvas_w = int(round(max(columns_right, band_right) + MARGIN))
    canvas_h = int(round(max(content_top + max_h, bottom) + BOTTOM_BAND))

    # ---- orphan nodes (no group) --------------------------------------------
    if orphans:
        fps = [(_footprint(n, "default", level, shape_index)[:2]) for n in orphans]
        pos, W, H = _pack([(w, h) for w, h in fps], "row" if len(orphans) <= 5 else "grid", NODE_GAP)
        ox = max(MARGIN, (canvas_w - W) / 2.0)
        oy = float(columns_bottom + 12)
        for n, (px, py), (w, h) in zip(orphans, pos, fps):
            out_nodes[n.id] = (int(round(ox + px)), int(round(oy + py)),
                               int(round(w)), int(round(h)))
        canvas_h = max(canvas_h, int(oy + H + BOTTOM_BAND))

    return {
        "groups": out_groups,
        "nodes": out_nodes,
        "edges": {},                      # routing is Task 8's job
        "canvas": (canvas_w, canvas_h),
        "meta": {
            "slots": {s: list(slots[s]) for s in SLOTS},
            # Reverse of "slots": the slot each top-level group landed in.
            "slot_of": dict(slot_of),
            # Every group gets a lanes entry, not just ones with direct
            # nodes — a node-less CONTAINER group (e.g. a "btp" group that
            # only nests subaccounts) previously had no key at all here,
            # which a router walking "every group's lane" would trip on.
            # Empty list covers both pure containers and genuinely-empty
            # leaf frames alike.
            "lanes": {gid: list(lanes.get(gid, [])) for gid in groups_by_id},
            "ranks": {nid: ranks[nid] for nid in ir_index},
            "identity": sorted(identity_ids),
            # x-extent (x0, x1) of each vertical column, in canvas
            # coordinates — the ONE source of truth for column boundaries
            # (Task 7's NETWORK separator sits between "center"[1] and
            # "right"[0]; Task 8's router shares the same numbers instead of
            # re-deriving them).
            "columns": {c: (int(round(col_x_extent[c][0])), int(round(col_x_extent[c][1])))
                        for c in ("left", "center", "right")},
        },
    }


# ── Footprint / inset helpers (bridge to the contract-driven molecule sizing) ─
def _node_footprint(node, group_type, level, shape_index, contract, M) -> tuple[float, float]:
    """(w, h) a leaf node reserves. IR v2 molecule nodes (product/db/chip) get
    their real molecule footprint from the contract; everything else uses the v1
    ``_footprint`` (icon + caption / backend box / labelled box)."""
    if getattr(node, "type", None) in MOLECULE_NODE_TYPES:
        return M.footprint(node, contract)
    return _footprint(node, group_type, level, shape_index)[:2]


def _insets(group, gtype, is_mol, has_children, M, contract) -> tuple[float, float, float]:
    """(pad_x, pad_top, pad_bot) for a group frame. Molecule frames source their
    insets from the contract (via _molecules.frame_insets, kept 1:1 with where
    the builders draw their decorations); v1 groups use _padding."""
    if is_mol:
        return M.frame_insets(group, contract)
    return _padding(gtype, has_children)


def _frame_size(group, gtype, is_mol, content_w, content_h,
                pad_x, pad_top, pad_bot, M, contract) -> tuple[float, float]:
    """Final (w, h) of a group frame: molecule frames clamp to
    ``max(contract-minimum, content + insets)`` (so the frame contains its
    children AND its own decorations); v1 groups are just content + padding."""
    if is_mol:
        return M.footprint(group, contract, (content_w, content_h))
    return content_w + 2 * pad_x, content_h + pad_top + pad_bot


# ── Slot / band assignment ───────────────────────────────────────────────────
def _type_slot(gtype: str) -> str:
    if gtype == "user":
        return "left"
    if gtype in ("btp-layer", "subaccount"):
        return "center"
    if gtype in ("cloud-tier", "custom-app", *BACKEND_TYPES):
        return "right"
    if gtype == "governance":
        return "top"
    if gtype == "legend":
        return "bottom"
    return "center"


def _slot(g, is_identity: bool) -> str:
    """Slot for a top-level group. Explicit ``zone``/``position`` override the
    type-based default (as in the old zone engine); identity groups land in the
    center column (placed below the main center frame via _band)."""
    if is_identity:
        return "center"
    if g.type == "governance":
        return "top"
    if g.type == "legend":
        return "bottom"
    z = (getattr(g, "zone", None) or "").lower()
    if z in ("left", "center", "right"):
        return z
    p = (g.position or "").lower()
    if "left" in p:
        return "left"
    if "right" in p:
        return "right"
    if p in ("top", "top-center", "bottom", "bottom-center", "middle"):
        return "center"
    # default / explicit "center" → fall back to the type-based slot so v2 types
    # (cloud-tier / backends / custom-app) land right and subaccount/btp center.
    return _type_slot(g.type)


def _band(g, is_identity: bool) -> int:
    """Vertical band within a column: 0=top, 1=middle, 2=bottom, 3=identity
    (always below everything else in the center column)."""
    if is_identity:
        return 3
    p = (g.position or "").lower()
    if p.startswith("top"):
        return 0
    if p.startswith("bottom"):
        return 2
    return 1
