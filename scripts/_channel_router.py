#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""Deterministic **channel router** for sap-diagrams-pro (Task 8).

The skeleton layout (``_skeleton_layout.compute_layout``) positions every box
but returns ``edges: {}`` — edge routing was left to draw.io's defaults, which
produced the tangled centre the user saw (crossing lines, colliding labels,
edges piercing box borders). This module replaces that with a *structural*
router that exploits the fixed skeleton composition instead of solving a
general orthogonal-routing problem.

What this router guarantees TODAY:

* The **gutters** between the left / center / right columns are reserved
  vertical channels; horizontal **corridors** are reserved above and below the
  column content. Every inter-column edge travels through these corridors, so
  its segments are overlap-free *by construction* once each edge gets its own
  parallel **lane** within a channel (offset by a fixed pitch) — clean
  vertical travel plus inter-edge lane separation.
* **Ports** — where an edge attaches to a box — are distributed across each
  box side by barycenter so a fan of connectors leaving one node spreads out
  instead of stacking on the side midpoint.
* Edge **pills** (protocol chips like "SCIM") and **labels** are dropped into a
  per-channel slot grid, scanned to the first collision-free slot — pill and
  label rects are collision-free by construction (``RouteResult.
  slot_fallbacks`` flags the rare edge whose scan window was exhausted, see
  ``_place_in_slots``).
* Every in-gutter lane bundle crosses the NETWORK separator bar (Task 7) once,
  cleanly, kept ``SEP_CLEARANCE`` off it wherever the gutter is wide enough.

What it does NOT yet guarantee: an edge's interior segment can still pierce a
*node* box inside a wide, densely-populated column — obstacle-aware routing
around other nodes within a column (and the crossing-reduction search that
benefits from it) is Task 9's job. Task 9 forks the router at the seam below
(custom ``lane_order`` / ``port_order``), not the internals of ``route()``.

Everything is pure-Python and deterministic: no datetime, no randomness, every
sort carries a stable ``(…, id)`` tie-break, so the same IR yields byte-
identical waypoints.

Coordinate space is draw.io's top-left-origin canvas (x grows right, y grows
down); waypoints are ABSOLUTE canvas coordinates (matching how the pure-Python
preview renderer and draw.io both treat an edge's ``<Array as="points">``).

Public API::

    route(diagram, layout) -> RouteResult

is the composition of three independently callable steps — so a caller (Task
9's lane/port reordering search) can iterate without forking internals::

    plan(diagram, layout) -> plans
    assign_ports_lanes(plans, layout, *, lane_order=None, port_order=None)
        -> (port_fracs, lane_offsets)
    build_waypoints(plans, port_fracs, lane_offsets, layout)
        -> (waypoints, pill_pos, label_pos, crossings, slot_fallbacks)

``layout`` is the dict returned by ``compute_layout`` (needs ``nodes``,
``groups``, ``canvas`` and ``meta`` with ``columns`` + ``networkSeparator``);
thread the SAME ``layout`` through all three calls. ``plans`` (from ``plan``)
behaves like ``list[_Plan]`` (iterate / len()) and also carries ``.channels``
— the exact ``Channel`` objects each plan's ``.channel`` references, reused
(not rebuilt) by the later steps and by ``RouteResult.channels``.
``lane_order`` / ``port_order`` are optional reordering hooks (see
``_allocate_lanes`` / ``_assign_ports``) that default to ``None``, reproducing
today's stable barycenter ordering exactly; ``port_groups(plans)`` exposes the
per-side groups those hooks reorder, read-only, without forking this module.

``RouteResult`` exposes ``waypoints`` (interior bend points per edge — the
renderer prepends the exit port and appends the entry port), ``ports`` /
``port_fracs`` (absolute + fractional attach points), ``pill_pos`` /
``label_pos`` (absolute centres), ``channels`` (the reserved corridors),
``slot_fallbacks`` (edge ids whose pill/label slot scan was exhausted — empty
on both shipped fixtures) and a ``crossings`` count (segment/segment
crossings, the baseline Task 9 reduces).
"""
from __future__ import annotations

import heapq
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Sibling module: the geometry kernel (Rect + overlap/intersection tests) ──
def _load_sibling(name: str):
    """Load a dash-free sibling ``scripts/<name>.py`` once, sharing the module
    with tests' ``conftest.load_script`` via ``sys.modules`` (so Rect identity
    is stable across the router, the emitter and the test process alike)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parent / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod            # register BEFORE exec (see conftest note)
    spec.loader.exec_module(mod)
    return mod


_gc = _load_sibling("_geom_checks")
Rect = _gc.Rect
rects_overlap = _gc.rects_overlap
seg_intersects_rect = _gc.seg_intersects_rect
segments_cross = _gc.segments_cross


# ── Tunables ─────────────────────────────────────────────────────────────────
LANE_PITCH = 12.0        # px between adjacent parallel lanes in a channel
CHANNEL_BASE_W = 24.0    # a channel's reserved base width before lanes are added
PORT_LO, PORT_HI = 0.25, 0.75   # ports spread evenly across [0.25, 0.75] of a side
CORRIDOR_MARGIN = 22.0   # px a horizontal corridor sits clear of all node content
SLOT_PAD = 6.0           # extra px between pill/label slots (pitch = pill_h + this)
SEP_CLEARANCE = 14.0     # px a lane keeps clear of the NETWORK separator bar
CROSS_MAX_SWEEPS = 3     # Task 9A: max greedy bubble sweeps in reduce_crossings
AVOID_CLEARANCE = 8.0    # Task 9B: px an obstacle-avoiding detour keeps clear of a node box
AVOID_TURN_PENALTY = 30.0  # Task 9B: A* per-bend cost so detours prefer few, clean corners

_COLS = ("left", "center", "right")
_SIDE_DIR = {"R": (1.0, 0.0), "L": (-1.0, 0.0), "B": (0.0, 1.0), "T": (0.0, -1.0)}
_STEP_DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))
_STEP_IDX = {d: i for i, d in enumerate(_STEP_DIRS)}


# ── Data model ───────────────────────────────────────────────────────────────
@dataclass
class Channel:
    """A reserved corridor between slots. ``axis`` is ``"v"`` (a vertical gutter
    between two columns) or ``"h"`` (a horizontal corridor above/below rows).
    ``rect`` is the reserved space; ``lanes`` maps each edge id routed through
    the channel to its integer lane index (offset = ``(i-(n-1)/2)*LANE_PITCH``
    from the channel centre-line)."""
    id: str
    axis: str
    rect: Any                       # _geom_checks.Rect
    lanes: dict[str, int] = field(default_factory=dict)

    @property
    def center(self) -> float:
        """The channel's centre-line coordinate (x for ``v``, y for ``h``)."""
        return self.rect.cx if self.axis == "v" else self.rect.cy


@dataclass
class RouteResult:
    waypoints: dict[str, list[tuple[float, float]]]
    ports: dict[str, tuple[tuple[float, float], tuple[float, float]]]
    port_fracs: dict[str, tuple[tuple[float, float], tuple[float, float]]]
    pill_pos: dict[str, tuple[float, float]]
    label_pos: dict[str, tuple[float, float]]
    channels: list[Channel]
    crossings: int
    # Count of (edge segment, non-endpoint node rect) intersecting pairs on the
    # FINAL (post-obstacle-avoidance) paths — Task 9B's metric, driven to ~0.
    # See count_piercings; 0 on both shipped fixtures today.
    piercings: int = 0
    # Edge ids whose pill/label _place_in_slots scan was exhausted (fell back
    # to a possibly-overlapping base position instead of a verified
    # collision-free slot). Empty on both shipped fixtures today; lets Task
    # 12's quality gate distinguish "collision-free" from "fell back" instead
    # of silently accepting either.
    slot_fallbacks: list[str] = field(default_factory=list)


@dataclass
class _Plan:
    """Per-edge routing intent, computed before ports/lanes are resolved."""
    eid: str
    src: Any                        # Rect
    dst: Any                        # Rect
    src_id: str
    dst_id: str
    src_col: int
    dst_col: int
    kind: str                       # "adjacent" | "long" | "intra" | "degenerate"
    exit_side: str                  # "L" | "R" | "T" | "B"
    entry_side: str
    channel: Channel | None = None  # the shared reserved channel (gutter/corridor)
    # perpendicular-axis key used to order ports on a side / lanes in a channel
    src_bary: float = 0.0
    dst_bary: float = 0.0
    # pill/label TEXT, captured once by plan() from the source edge — so
    # build_waypoints (step 3) never needs `diagram`/`edge_by_id` again.
    pill: str | None = None
    label: str | None = None


class _Plans(list):
    """``plan()``'s return value: a ``list[_Plan]`` (every existing helper
    that does ``for p in plans`` keeps working unchanged) that ALSO carries
    ``.channels`` — the exact gutter + corridor ``Channel`` objects this
    ``plan()`` call built (used or not by any edge). Downstream steps reuse
    these SAME objects (``assign_ports_lanes`` mutates ``channel.lanes`` in
    place; ``route()`` republishes them as ``RouteResult.channels``) instead
    of rebuilding a second, un-mutated, identity-mismatched copy from the
    layout."""
    channels: list[Channel]

    def __init__(self, items: list[_Plan], channels: list[Channel]):
        super().__init__(items)
        self.channels = channels


# ── Geometry helpers ─────────────────────────────────────────────────────────
def _rect(t: tuple[float, float, float, float]):
    return Rect(float(t[0]), float(t[1]), float(t[2]), float(t[3]))


def _obstacle_geo(layout: dict) -> dict[str, Any]:
    """Node rects used for OBSTACLE purposes — piercing avoidance
    (``_avoid_obstacles`` / ``count_piercings``) and the pill/label slot
    obstacle set (``_place_pills_and_labels``). ``layout["node_obstacles"]``
    when the caller supplies it (the emitter does — FIX-1: icon nodes get a
    rect extended DOWNWARD over their caption band, so the router treats
    icon+caption as one box for these purposes), else the same rects
    ``plan()`` uses for ports (``layout["nodes"]``) — the pre-FIX-1 behaviour,
    which every test that hand-builds a bare ``{"nodes": …}`` layout still
    gets. Deliberately NOT used for ports/exit-entry points: those must stay
    on the real (icon-only) drawn geometry so edges anchor exactly where
    draw.io renders the connection (see ``plan()``)."""
    src = layout.get("node_obstacles") or layout.get("nodes", {})
    return {nid: _rect(t) for nid, t in src.items()}


def _side_frac(r, side: str, frac: float) -> tuple[float, float]:
    """Fractional (exitX, exitY)-style anchor for ``side`` at ``frac``."""
    if side == "R":
        return (1.0, frac)
    if side == "L":
        return (0.0, frac)
    if side == "T":
        return (frac, 0.0)
    return (frac, 1.0)                               # "B"


def _abs_port(rect, frac: tuple[float, float]) -> tuple[float, float]:
    """Absolute (x, y) for a fractional ``(fx, fy)`` anchor on ``rect`` — the
    frac→abs conversion ``build_waypoints`` (its own path-building) and
    ``route()`` (``RouteResult.ports``) both need, single-sourced so the two
    call sites can't silently drift apart."""
    return (rect.x + frac[0] * rect.w, rect.y + frac[1] * rect.h)


def _dedupe(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Drop consecutive near-duplicate points (< 0.5px apart on both axes)."""
    out: list[tuple[float, float]] = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) > 0.5 or abs(p[1] - out[-1][1]) > 0.5:
            out.append(p)
    return out


def _column_of(r, cols: list[tuple[str, float, float]]) -> int:
    """Index (into ``cols``) of the present column whose x-extent contains the
    rect centre, else the nearest by horizontal distance. Ties break to the
    lower index (deterministic)."""
    cx = r.cx
    best_i, best_d = 0, None
    for i, (_name, x0, x1) in enumerate(cols):
        if x0 <= cx <= x1:
            return i
        d = x0 - cx if cx < x0 else cx - x1
        if best_d is None or d < best_d:
            best_i, best_d = i, d
    return best_i


# ── Region graph (columns linked by gutter channels) ─────────────────────────
def _bfs_columns(adj: dict[int, list[int]], start: int, goal: int) -> list[int]:
    """Shortest region path start→goal over the column adjacency graph. The
    graph is a simple left-to-right chain, but a real BFS keeps the model
    honest (and correct if a column is ever missing)."""
    if start == goal:
        return [start]
    from collections import deque
    prev: dict[int, int] = {start: start}
    q = deque([start])
    while q:
        u = q.popleft()
        for v in adj.get(u, []):
            if v not in prev:
                prev[v] = u
                if v == goal:
                    path = [goal]
                    while path[-1] != start:
                        path.append(prev[path[-1]])
                    path.reverse()
                    return path
                q.append(v)
    return [start, goal]                              # disconnected fallback


# ── Planning ─────────────────────────────────────────────────────────────────
def _plan_edge(eid, src, dst, src_id, dst_id, src_col, dst_col, region_path,
               gutters, corridors) -> _Plan:
    """Classify one edge and pick its exit/entry sides + shared channel.

    ``region_path`` is the BFS shortest path over the column region graph from
    the source column to the target column; its hop count (``len-1``) — the
    number of gutters the edge must traverse — drives the three structural
    cases that exploit the skeleton:
      * ``adjacent`` (one hop) — an H-V-H route whose vertical segment sits in
        the shared gutter.
      * ``long`` (two+ hops) — a route out to a horizontal corridor, one
        horizontal segment across BOTH gutters, then back in.
      * ``intra`` (zero hops, same column) — an orthogonal L/Z jog inside the
        column, dominant axis first.
    """
    dcol = dst_col - src_col
    hops = max(0, len(region_path) - 1)
    if hops == 1 and gutters:
        gi = min(src_col, dst_col)                    # gutter left of the higher col
        ch = gutters.get(gi)
        if ch is not None:
            if dcol > 0:                              # target to the right
                return _Plan(eid, src, dst, src_id, dst_id, src_col, dst_col,
                             "adjacent", "R", "L", ch, src.cy, dst.cy)
            return _Plan(eid, src, dst, src_id, dst_id, src_col, dst_col,
                         "adjacent", "L", "R", ch, src.cy, dst.cy)

    if hops >= 2 and corridors:
        # Pick the corridor (top/bottom) closest to the edge's mid-height.
        mid_y = (src.cy + dst.cy) / 2.0
        top, bot = corridors.get("top"), corridors.get("bottom")
        use = top
        if top is not None and bot is not None:
            use = top if (mid_y - top.center) <= (bot.center - mid_y) else bot
        elif bot is not None:
            use = bot
        side = "T" if (use is not None and use.center <= src.cy) else "B"
        # bary along the corridor axis (x) orders lanes/ports across it
        return _Plan(eid, src, dst, src_id, dst_id, src_col, dst_col, "long",
                     side, side, use, src.cx, dst.cx)

    # Same column (or no channels): intra-column orthogonal jog.
    dx = dst.cx - src.cx
    dy = dst.cy - src.cy
    if abs(dx) >= abs(dy):                             # horizontal dominant → H-V-H
        exit_s = "R" if dx >= 0 else "L"
        entry_s = "L" if dx >= 0 else "R"
        return _Plan(eid, src, dst, src_id, dst_id, src_col, dst_col, "intra",
                     exit_s, entry_s, None, src.cy, dst.cy)
    exit_s = "B" if dy >= 0 else "T"                  # vertical dominant → V-H-V
    entry_s = "T" if dy >= 0 else "B"
    return _Plan(eid, src, dst, src_id, dst_id, src_col, dst_col, "intra",
                 exit_s, entry_s, None, src.cx, dst.cx)


# ── Ports (8c) ───────────────────────────────────────────────────────────────
def _port_bary(other, side: str) -> float:
    """Ordering key for a port on ``side``: the OTHER endpoint's coordinate
    perpendicular to that side (its y for a vertical L/R side, its x for a
    horizontal T/B side). Sorting a side's ports by this spreads a fan without
    the connectors crossing each other at the box face."""
    return other.cy if side in ("L", "R") else other.cx


def _spread(n: int, i: int) -> float:
    """Fraction i of n evenly across [PORT_LO, PORT_HI] (0.5 when alone)."""
    if n <= 1:
        return 0.5
    return PORT_LO + i * (PORT_HI - PORT_LO) / (n - 1)


def _group_ports(plans: list[_Plan]
                 ) -> tuple[dict[tuple[str, str], list[_Plan]],
                            dict[tuple[str, str], list[_Plan]]]:
    """``(exit_groups, entry_groups)``: plans bucketed by ``(node_id, side)``
    and sorted by the default barycenter key — the exact grouping
    ``_assign_ports`` distributes fractions over. Factored out so there is
    ONE grouping implementation, shared by ``_assign_ports`` and the public
    ``port_groups`` introspection helper below."""
    from collections import defaultdict
    exit_groups: dict[tuple[str, str], list[_Plan]] = defaultdict(list)
    entry_groups: dict[tuple[str, str], list[_Plan]] = defaultdict(list)
    for p in plans:
        exit_groups[(p.src_id, p.exit_side)].append(p)
        entry_groups[(p.dst_id, p.entry_side)].append(p)
    for (_nid, side), group in exit_groups.items():
        group.sort(key=lambda p: (_port_bary(p.dst, side), p.eid))
    for (_nid, side), group in entry_groups.items():
        group.sort(key=lambda p: (_port_bary(p.src, side), p.eid))
    return dict(exit_groups), dict(entry_groups)


def port_groups(plans: list[_Plan]
               ) -> tuple[dict[tuple[str, str], list[_Plan]],
                          dict[tuple[str, str], list[_Plan]]]:
    """Public, read-only introspection: ``(exit_groups, entry_groups)`` — see
    ``_group_ports``. Lets a caller (Task 9's crossing-reduction search)
    inspect the default per-(node, side) port ordering before building a
    custom ``port_order`` hook for ``assign_ports_lanes``, without forking
    this module."""
    return _group_ports(plans)


def _assign_ports(plans: list[_Plan], *, port_order=None
                  ) -> dict[str, tuple[tuple, tuple]]:
    """Fractional (exit, entry) anchor per edge, distributed per box side.

    Edges leaving the SAME side of the SAME box are grouped, ordered by the
    barycenter of their far endpoint, and handed fractions spread evenly across
    ``[0.25, 0.75]`` of that side — so a fan of connectors diverges instead of
    stacking on the side midpoint. Entry ports are distributed the same way on
    the target side. Deterministic: every group is sorted with an ``eid``
    tie-break.

    ``port_order``, if given, is called as ``port_order(group)`` for every
    per-(node, side) group AFTER the default barycenter sort (every plan in
    ``group`` shares that one (node, side)); it must return a permutation of
    ``group`` — Task 9's crossing-reduction search reorders ports here. The
    default (``None``) skips the call entirely, reproducing today's output
    exactly."""
    exit_groups, entry_groups = _group_ports(plans)

    exit_frac: dict[str, tuple[float, float]] = {}
    entry_frac: dict[str, tuple[float, float]] = {}
    for (_nid, side), group in exit_groups.items():
        if port_order is not None:
            group = port_order(group)
        for i, p in enumerate(group):
            exit_frac[p.eid] = _side_frac(p.src, side, _spread(len(group), i))
    for (_nid, side), group in entry_groups.items():
        if port_order is not None:
            group = port_order(group)
        for i, p in enumerate(group):
            entry_frac[p.eid] = _side_frac(p.dst, side, _spread(len(group), i))

    return {p.eid: (exit_frac[p.eid], entry_frac[p.eid]) for p in plans}


# ── Lanes (8b) ───────────────────────────────────────────────────────────────
def _allocate_lanes(channels: list[Channel], plans: list[_Plan],
                    net_sep: dict | None = None, *, lane_order=None
                    ) -> dict[str, float]:
    """Per-edge perpendicular offset from its channel centre-line.

    Every edge sharing a channel gets its own parallel lane, so their in-channel
    segments are overlap-free *by construction*. Within a channel the edges are
    sorted by ``(src_bary, dst_bary, eid)`` (the "(src.y, dst.y, id)" rule of
    the plan, projected onto the channel's relevant axis) and handed lane
    indices ``0..n-1``; the offset is ``(i-(n-1)/2)*pitch``, centring the bundle
    on the centre-line. The pitch is ``LANE_PITCH`` (12px) but is reduced to fit
    a narrow vertical gutter — never below 10px, so the parallel lanes always
    stay a legible distance apart. ``channel.lanes`` records each edge's index
    (in final lane order — the "per-channel ordered edge list").

    ``lane_order``, if given, is called as ``lane_order(group)`` for every
    channel's default-sorted group and must return a permutation of it — Task
    9's crossing-reduction search reorders lanes here. The default (``None``)
    skips the call entirely, reproducing today's output exactly.
    """
    by_channel: dict[int, list[_Plan]] = {}
    for p in plans:
        if p.channel is not None:
            by_channel.setdefault(id(p.channel), []).append(p)

    sep_x = float(net_sep["x"]) if net_sep else None

    offsets: dict[str, float] = {}
    for ch in channels:
        group = by_channel.get(id(ch), [])
        if not group:
            continue
        group.sort(key=lambda p: (p.src_bary, p.dst_bary, p.eid))
        if lane_order is not None:
            group = lane_order(group)
        n = len(group)
        pitch = LANE_PITCH
        if ch.axis == "v" and n > 1:                  # keep the bundle in-gutter
            usable = max(1.0, ch.rect.w - 16.0)
            if (n - 1) * pitch > usable:
                pitch = max(10.0, usable / (n - 1))

        # The NETWORK separator bar sits on this gutter's centre-line: shift the
        # whole lane bundle to one side so no lane HUGS the bar (edges instead
        # cross it once, cleanly, on their horizontal exit/entry segment).
        base = 0.0
        if (ch.axis == "v" and sep_x is not None
                and ch.rect.x <= sep_x <= ch.rect.right):
            centered_min = ch.center - (n - 1) / 2.0 * pitch
            want_min = sep_x + SEP_CLEARANCE                    # left-most lane
            base = want_min - centered_min
            max_x = ch.center + (n - 1) / 2.0 * pitch + base
            if max_x > ch.rect.right - 4.0:                     # clamp in-gutter
                base -= (max_x - (ch.rect.right - 4.0))
                min_x = ch.center - (n - 1) / 2.0 * pitch + base
                if min_x < sep_x:
                    # The clamp above traded away SEP_CLEARANCE to keep the
                    # bundle inside the gutter; a dense-enough bundle can
                    # still cross the separator itself. Try the cheap fix
                    # first — a further shift, which preserves lane SPACING
                    # exactly and is all either shipped fixture needs.
                    fixed_base = base + (sep_x - min_x)
                    fixed_max = ch.center + (n - 1) / 2.0 * pitch + fixed_base
                    if fixed_max <= ch.rect.right - 4.0:
                        base = fixed_base
                    else:
                        # A shift alone can't satisfy both ends at once: the
                        # bundle is wider than the space between the
                        # separator and the gutter's far edge (e.g. enough
                        # direct edges through one gutter — not exercised by
                        # either shipped fixture). Shrink the pitch to what's
                        # actually available there instead, never below the
                        # 10px legibility floor, and hug the gutter's far
                        # edge — the HARD invariant: never spill a lane into
                        # the next column's node content, even if that means
                        # giving up the rest of SEP_CLEARANCE (soft).
                        avail_lo, avail_hi = sep_x, ch.rect.right - 4.0
                        if n > 1:
                            pitch = max(10.0, (avail_hi - avail_lo) / (n - 1))
                        span = (n - 1) * pitch
                        left_edge = min(avail_lo, avail_hi - span)
                        base = left_edge - (ch.center - (n - 1) / 2.0 * pitch)

        for i, p in enumerate(group):
            ch.lanes[p.eid] = i
            offsets[p.eid] = (i - (n - 1) / 2.0) * pitch + base
    return offsets


# ── Waypoint construction ────────────────────────────────────────────────────
def _build_waypoints(p: _Plan, exit_pt, entry_pt, lane_off: float
                     ) -> list[tuple[float, float]]:
    """Interior bend points for one edge, given its resolved exit/entry points
    and its lane offset within the shared channel. Segments are orthogonal by
    construction (matching corners)."""
    ex, ey = exit_pt
    nx, ny = entry_pt
    if p.kind == "adjacent" and p.channel is not None:
        lane_x = p.channel.center + lane_off
        pts = [(lane_x, ey), (lane_x, ny)]
    elif p.kind == "long" and p.channel is not None:
        lane_y = p.channel.center + lane_off
        pts = [(ex, lane_y), (nx, lane_y)]
    else:                                             # intra-column L/Z jog
        if p.exit_side in ("L", "R"):                 # H-V-H
            mid_x = (ex + nx) / 2.0 + lane_off
            pts = [(mid_x, ey), (mid_x, ny)]
        else:                                         # V-H-V
            mid_y = (ey + ny) / 2.0 + lane_off
            pts = [(ex, mid_y), (nx, mid_y)]
    pts = _dedupe(pts)
    if not pts:                                       # guarantee >= 1 waypoint
        pts = [((ex + nx) / 2.0, (ey + ny) / 2.0)]
    return pts


def _segments(path: list[tuple[float, float]]):
    return list(zip(path, path[1:]))


# ── Pill / label slot placement (8e) ─────────────────────────────────────────
_PILL_CHAR_W = 6.4       # px per char (bold ~10px) for a pill/label width estimate
PILL_H = 18.0            # protocol-pill rect height used for slots + emission
LABEL_H = 18.0           # edge-label rect height


def pill_dims(text: str | None) -> tuple[float, float]:
    """Rect (w, h) a protocol pill occupies — width adapts to the text so the
    chip fits it (and the collision test, the emitter and the render all agree
    on one size). Single-sourced here on purpose."""
    t = text or ""
    return (max(36.0, len(t) * _PILL_CHAR_W + 16.0), PILL_H)


def label_dims(text: str | None) -> tuple[float, float]:
    """Rect (w, h) an edge label occupies (its white background box)."""
    t = text or ""
    return (max(30.0, len(t) * _PILL_CHAR_W + 12.0), LABEL_H)


def _longest_segment(path: list[tuple[float, float]]):
    """The (a, b) segment of ``path`` with the greatest length (ties → earliest,
    deterministic)."""
    best, best_len = None, -1.0
    for a, b in _segments(path):
        d = abs(a[0] - b[0]) + abs(a[1] - b[1])       # manhattan (orthogonal segs)
        if d > best_len:
            best, best_len = (a, b), d
    if best is None:                                  # single-point path
        return path[0], path[0]
    return best


def _slot_free(rect, obstacles, foreign_segs) -> bool:
    """A slot is free when its rect overlaps no obstacle rect (node / box /
    already-placed pill-or-label) AND no foreign edge segment cuts through it.
    Uses the _geom_checks kernel directly — strict ``rects_overlap`` (touching
    is allowed, real 2-D penetration is not) and inclusive
    ``seg_intersects_rect`` — so the emitter and the tests apply the exact same
    predicates the placement guaranteed."""
    for o in obstacles:
        if rects_overlap(rect, o, pad=0.0):
            return False
    for p, q in foreign_segs:
        if seg_intersects_rect(p, q, rect):
            return False
    return True


def _place_in_slots(seg, dims, obstacles, foreign_segs):
    """Return ``((cx, cy), rect, placed_ok)`` for a ``dims`` rect, starting at
    the segment midpoint and scanning a grid: first ALONG the segment (the
    pill/label rides its edge), then stepping perpendicular off it if the
    segment is congested. Slot pitch = rect height + SLOT_PAD.

    ``placed_ok`` is ``True`` when the scan found a genuinely collision-free
    slot, ``False`` when the whole scan window (15 perpendicular steps, each
    with ``along_max`` steps along the segment, both directions) was
    exhausted — in which case ``(cx, cy)``/``rect`` silently fall back to the
    unchecked segment-midpoint position (still returned, so callers keep a
    position to render) but MAY overlap another rect. Making this observable
    (rather than a silent fallback) is what lets ``RouteResult.
    slot_fallbacks`` — and Task 12's quality gate — distinguish "collision-
    free" from "fell back"."""
    (ax, ay), (bx, by) = seg
    base = ((ax + bx) / 2.0, (ay + by) / 2.0)
    seg_len = abs(bx - ax) + abs(by - ay)
    if abs(bx - ax) >= abs(by - ay):                  # horizontal segment
        along, perp = (1.0, 0.0), (0.0, 1.0)
    else:                                             # vertical segment
        along, perp = (0.0, 1.0), (1.0, 0.0)
    w, h = dims
    pitch = h + SLOT_PAD
    along_max = int(seg_len / (2.0 * pitch)) + 2
    for perp_i in range(0, 15):
        for perp_s in ([0] if perp_i == 0 else [perp_i, -perp_i]):
            for along_i in range(0, along_max + 1):
                for along_s in ([0] if along_i == 0 else [along_i, -along_i]):
                    cx = base[0] + along_s * pitch * along[0] + perp_s * pitch * perp[0]
                    cy = base[1] + along_s * pitch * along[1] + perp_s * pitch * perp[1]
                    rect = Rect(cx - w / 2, cy - h / 2, w, h)
                    if _slot_free(rect, obstacles, foreign_segs):
                        return (cx, cy), rect, True
    return base, Rect(base[0] - w / 2, base[1] - h / 2, w, h), False


def _place_pills_and_labels(plans, paths, node_geo):
    """Drop each edge's protocol pill and label into a collision-free slot on
    its longest segment. Processed in IR order; every placed rect becomes an
    obstacle for later ones, so results are overlap-free by construction and
    deterministic. Pill/label TEXT comes from ``_Plan.pill``/``_Plan.label``
    (captured once, in ``plan()``, from the source diagram's edges) — this
    function needs no ``diagram``/``edge_by_id`` of its own.

    Returns ``(pill_pos, label_pos, slot_fallbacks)`` — ``slot_fallbacks`` is
    the list of edge ids (order of first fallback: pill before label) whose
    ``_place_in_slots`` scan was exhausted (see there); empty when every slot
    was placed collision-free."""
    node_rects = list(node_geo.values())
    placed: list = []                                 # pill + label rects so far
    all_segs = {p.eid: _segments(paths[p.eid]) for p in plans}
    pill_pos: dict[str, tuple[float, float]] = {}
    label_pos: dict[str, tuple[float, float]] = {}
    slot_fallbacks: list[str] = []
    for p in plans:
        seg = _longest_segment(paths[p.eid])
        foreign = [s for eid, segs in all_segs.items() if eid != p.eid for s in segs]
        if p.pill:
            center, rect, ok = _place_in_slots(seg, pill_dims(p.pill),
                                               node_rects + placed, foreign)
            pill_pos[p.eid] = center
            placed.append(rect)
            if not ok:
                slot_fallbacks.append(p.eid)
        if p.label:
            center, rect, ok = _place_in_slots(seg, label_dims(p.label),
                                               node_rects + placed, foreign)
            label_pos[p.eid] = center
            placed.append(rect)
            if not ok:
                slot_fallbacks.append(p.eid)
    return pill_pos, label_pos, slot_fallbacks


def _count_crossings(paths: dict[str, list[tuple[float, float]]]) -> int:
    """Proper segment/segment crossings between DISTINCT edges (via
    ``_geom_checks.segments_cross`` — shared endpoints / collinear overlap do
    NOT count). The baseline Task 9's crossing-reduction improves on."""
    ids = list(paths.keys())
    total = 0
    for i in range(len(ids)):
        segs_i = _segments(paths[ids[i]])
        for j in range(i + 1, len(ids)):
            segs_j = _segments(paths[ids[j]])
            for a, b in segs_i:
                for c, d in segs_j:
                    if segments_cross(a, b, c, d):
                        total += 1
    return total


# ── Obstacle-aware routing (Task 9B) ─────────────────────────────────────────
def _seg_pierces(a, b, node_geo, skip) -> int:
    """Number of node rects (except the endpoint ids in ``skip``) that segment
    a→b intersects, via the inclusive ``seg_intersects_rect`` kernel."""
    n = 0
    for nid, r in node_geo.items():
        if nid in skip:
            continue
        if seg_intersects_rect(a, b, r):
            n += 1
    return n


def count_piercings(paths: dict[str, list[tuple[float, float]]],
                    node_geo: dict[str, Any],
                    endpoints: dict[str, tuple[str, str]]) -> int:
    """Total (edge segment, non-endpoint node rect) intersecting pairs across
    ``paths`` — the metric Task 9B drives toward 0. ``paths`` is
    ``{eid: [pt…]}`` (full path incl. exit/entry ports), ``node_geo`` is
    ``{nid: Rect}`` and ``endpoints`` maps each eid to its ``(src_id, dst_id)``
    (whose rects are skipped: an edge legitimately touches its own endpoints).
    Inclusive on purpose — a segment that only grazes a box boundary still
    counts (see ``_geom_checks`` point 2), so this never under-reports a box
    the user would see the line touch."""
    total = 0
    for eid, path in paths.items():
        skip = endpoints.get(eid, ())
        for a, b in _segments(path):
            total += _seg_pierces(a, b, node_geo, skip)
    return total


def _simplify_path(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Drop consecutive duplicates AND redundant MONOTONE-collinear interior
    points (a straight run of grid vertices collapses to its two ends). Keeps
    the first and last point. Geometry is unchanged: a point is removed only
    when it lies strictly BETWEEN its neighbours on the shared axis — an
    overshoot / U-turn on the same line (a→b→c where b is past c) is a real
    bend and is KEPT, so a path that runs out along a line and doubles back is
    never silently straightened into one that cuts the corner."""
    pts = _dedupe(pts)
    if len(pts) <= 2:
        return pts
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        ax, ay = out[-1]
        bx, by = pts[i]
        cx, cy = pts[i + 1]
        redundant = (
            (abs(ax - bx) < 0.5 and abs(bx - cx) < 0.5 and (ay - by) * (by - cy) > 0)
            or (abs(ay - by) < 0.5 and abs(by - cy) < 0.5 and (ax - bx) * (bx - cx) > 0)
        )
        if not redundant:
            out.append(pts[i])
    out.append(pts[-1])
    return out


def _clear_anchor(pt, side, own_rect, cl):
    """Step ``pt`` outward along ``side``'s outward normal by ``cl`` — and, if
    ``own_rect`` (the endpoint's OWN obstacle rect, which is also IN the
    caller's general obstacle list) reaches further out than that in the same
    direction, all the way clear of it too.

    Needed since FIX-1: an icon node's OBSTACLE rect can be taller than the
    rect its port sits on (extended downward over the caption band, while the
    port itself still sits on the real rendered icon edge — see
    ``_obstacle_geo``). Without this, a "B"-side exit port stepped only ``cl``
    past the icon's real bottom edge could land INSIDE that same node's own
    (taller) obstacle rect in the general obstacles list — trapping the A*
    start/goal state with no legal first move (every direction blocked by its
    own box) and silently returning ``None`` (naive path kept, piercing not
    fixed) instead of finding the real detour."""
    dx, dy = _SIDE_DIR[side]
    x, y = pt[0] + dx * cl, pt[1] + dy * cl
    if own_rect is not None:
        if dx > 0:
            x = max(x, own_rect.right + cl)
        elif dx < 0:
            x = min(x, own_rect.x - cl)
        elif dy > 0:
            y = max(y, own_rect.bottom + cl)
        elif dy < 0:
            y = min(y, own_rect.y - cl)
    return (x, y)


def _route_around(exit_pt, entry_pt, exit_side, entry_side, obstacles, net_sep,
                  src_rect=None, dst_rect=None):
    """Deterministic orthogonal A* over the Hanan grid of obstacle-box edges,
    each offset ``AVOID_CLEARANCE`` outward.

    Rather than route port→port (which would let a path sneak THROUGH the
    endpoint boxes to reach a face from the wrong side), it routes between
    **outward anchors**: each port stepped ``AVOID_CLEARANCE`` out along its
    side's outward normal (and clear of its OWN endpoint's obstacle rect —
    see ``_clear_anchor``, ``src_rect``/``dst_rect``), into free space. The
    two short port stubs (``exit_pt``→``exit_anchor`` and
    ``entry_anchor``→``entry_pt``) fix the exit/entry SIDES geometrically, so
    the ports the caller already assigned (and emitted as exitX/entryX) stay
    valid and the final approach always meets the face from outside — no
    in-search direction constraint, and no arriving through the box.

    A vertical grid segment is blocked when it runs within ``AVOID_CLEARANCE``
    (horizontally) of any obstacle whose y-range it overlaps, a horizontal one
    symmetrically; both use strict bounds so a lane exactly ``AVOID_CLEARANCE``
    off a box is allowed. The NETWORK separator (Task 7) is honoured exactly as
    ``_allocate_lanes`` does: no vertical segment runs within ``SEP_CLEARANCE``
    of it inside its y-span.

    Ties are broken by the A* priority tuple ``(f, g, ix, iy, dir)`` — pure
    coordinates/direction indices, no insertion counter — so the same inputs
    yield byte-identical paths. Returns the interior bend points (ports
    excluded, collinear vertices collapsed) or ``None`` when no clear
    orthogonal path exists on the grid (the caller then keeps the naive path).
    """
    cl = AVOID_CLEARANCE
    exit_anchor = _clear_anchor(exit_pt, exit_side, src_rect, cl)
    entry_anchor = _clear_anchor(entry_pt, entry_side, dst_rect, cl)

    xs = {exit_anchor[0], entry_anchor[0]}
    ys = {exit_anchor[1], entry_anchor[1]}
    for r in obstacles:
        xs.add(r.x - cl)
        xs.add(r.right + cl)
        ys.add(r.y - cl)
        ys.add(r.bottom + cl)
    xs = sorted(xs)
    ys = sorted(ys)
    xi = {v: i for i, v in enumerate(xs)}
    yi = {v: i for i, v in enumerate(ys)}
    sx, sy = xi[exit_anchor[0]], yi[exit_anchor[1]]
    gx, gy = xi[entry_anchor[0]], yi[entry_anchor[1]]
    sep_x = float(net_sep["x"]) if net_sep else None
    sep_y0 = float(net_sep["y0"]) if net_sep else 0.0
    sep_y1 = float(net_sep["y1"]) if net_sep else 0.0

    def vblocked(X, ylo, yhi):
        for r in obstacles:
            if (r.x - cl) < X < (r.right + cl) and ylo < r.bottom and r.y < yhi:
                return True
        if sep_x is not None and abs(X - sep_x) < SEP_CLEARANCE and ylo < sep_y1 and sep_y0 < yhi:
            return True
        return False

    def hblocked(Y, xlo, xhi):
        for r in obstacles:
            if (r.y - cl) < Y < (r.bottom + cl) and xlo < r.right and r.x < xhi:
                return True
        return False

    def heur(ix, iy):
        return abs(xs[ix] - xs[gx]) + abs(ys[iy] - ys[gy])

    # state = (ix, iy, dir); dir = index of the move that ARRIVED, -1 at start.
    # No first/last-move direction constraint — the port stubs handle the sides.
    pq = [(heur(sx, sy), 0.0, sx, sy, -1)]
    gscore = {(sx, sy, -1): 0.0}
    parent: dict = {}
    goal = None
    while pq:
        f, g, ix, iy, di = heapq.heappop(pq)
        state = (ix, iy, di)
        if g > gscore.get(state, 1e18):
            continue
        if (ix, iy) == (gx, gy):
            goal = state
            break
        for dx, dy in _STEP_DIRS:
            nx, ny = ix + dx, iy + dy
            if not (0 <= nx < len(xs) and 0 <= ny < len(ys)):
                continue
            x0, y0 = xs[ix], ys[iy]
            x1, y1 = xs[nx], ys[ny]
            if dx:
                if hblocked(y0, min(x0, x1), max(x0, x1)):
                    continue
            else:
                if vblocked(x0, min(y0, y1), max(y0, y1)):
                    continue
            ndi = _STEP_IDX[(dx, dy)]
            step = abs(x1 - x0) + abs(y1 - y0)
            turn = AVOID_TURN_PENALTY if (di != -1 and ndi != di) else 0.0
            ng = g + step + turn
            nstate = (nx, ny, ndi)
            if ng < gscore.get(nstate, 1e18) - 1e-9:
                gscore[nstate] = ng
                parent[nstate] = state
                heapq.heappush(pq, (ng + heur(nx, ny), ng, nx, ny, ndi))
    if goal is None:
        return None
    seq = []
    st = goal
    while st is not None:
        seq.append((xs[st[0]], ys[st[1]]))
        st = parent.get(st)
    seq.reverse()
    # exit_pt -> [exit_anchor .. entry_anchor] -> entry_pt, minus redundant vertices
    return _simplify_path([exit_pt] + seq + [entry_pt])[1:-1]


AVOID_OBSTACLE_CAP = 60  # FIX-3: node count above which _route_around's
                         # obstacle set is restricted (see _nearby_obstacles)


def _nearby_obstacles(exit_pt, entry_pt, node_geo):
    """FIX-3 scaling guard for ``_route_around``'s obstacle set.

    ``_avoid_obstacles`` hands ALL N diagram nodes to ``_route_around`` for
    EVERY rerouted edge (~O(E·N³) — a real cost on a dense diagram like
    SSAM's). A geometric bounding-box pre-filter was tried first (drop node
    boxes far from the edge's exit/entry span) but had to be abandoned: the
    Hanan grid's x/y coordinates are the UNION of every kept obstacle's edges
    ± clearance, so removing even a box nowhere near the edge's own path can
    shift which coordinate VALUES exist in the grid — and hence, via A*'s
    ``(f, g, ix, iy, dir)`` tie-break (grid INDICES, not raw coordinates),
    which of several equal-cost paths wins. Confirmed on nova-L1 itself: a
    300px-margin bbox filter changed 3 edges' bend points by a few px
    (functionally equivalent detours, but NOT byte-identical) — exactly the
    regression this task said not to risk. So instead of a filter that can
    silently perturb output on ANY layout, this is a CAP that provably never
    engages on either shipped fixture: nova-L1 has 27 nodes, ir-v2 has 12,
    both far under ``AVOID_OBSTACLE_CAP`` — below the cap this returns
    ``list(node_geo.values())`` unchanged (the exact pre-FIX-3 expression),
    so both fixtures are byte-identical by CONSTRUCTION, not by empirical
    verification of a heuristic. Only a diagram denser than the cap (SSAM-
    scale) has its obstacle set truncated, to the ``AVOID_OBSTACLE_CAP``
    boxes nearest the edge's exit/entry midpoint — bounding the A* grid size
    there too, with no byte-identical guarantee (there is no smaller-N
    baseline to match) but a reasonable one (favouring the obstacles most
    likely to actually lie on the detour)."""
    obstacles = list(node_geo.values())
    if len(obstacles) <= AVOID_OBSTACLE_CAP:
        return obstacles
    cx = (exit_pt[0] + entry_pt[0]) / 2.0
    cy = (exit_pt[1] + entry_pt[1]) / 2.0

    def _dist2(r):
        dx = max(r.x - cx, 0.0, cx - r.right)
        dy = max(r.y - cy, 0.0, cy - r.bottom)
        return dx * dx + dy * dy

    obstacles.sort(key=_dist2)
    return obstacles[:AVOID_OBSTACLE_CAP]


def _avoid_obstacles(plans, ports, waypoints, layout):
    """Reroute every edge whose naive path pierces a non-endpoint node box so
    it travels AROUND the box(es) instead of through them (Task 9B — the user's
    original 'edges piercing box borders' complaint). Clean edges (0 piercings)
    are left byte-identical, so Task 8's collision-free lane invariants for the
    gutter/corridor bundles are untouched. A reroute is adopted only when
    ``_route_around`` returns a non-empty interior path that STRICTLY lowers
    that edge's piercing count — never a regression, and the ≥1-waypoint
    guarantee is preserved. Ports are held fixed, so port distribution and the
    emitted exit/entry anchors stay valid. Deterministic (edges processed in
    IR/plan order; ``_route_around`` itself is deterministic)."""
    node_geo = _obstacle_geo(layout)
    net_sep = (layout.get("meta") or {}).get("networkSeparator")
    out: dict[str, list[tuple[float, float]]] = {}
    for p in plans:
        wps = waypoints[p.eid]
        exit_pt, entry_pt = ports[p.eid]
        skip = (p.src_id, p.dst_id)
        path = [exit_pt] + wps + [entry_pt]
        before = sum(_seg_pierces(a, b, node_geo, skip) for a, b in _segments(path))
        if before == 0:
            out[p.eid] = wps
            continue
        # ALL node boxes are obstacles — including this edge's own src/dst, so
        # the detour can't shortcut THROUGH an endpoint box to reach its face
        # from the wrong side. The outward port anchors (see _route_around) sit
        # on the endpoint boxes' clearance boundary, so the ports stay
        # reachable even though the boxes themselves are blocked. FIX-3:
        # _nearby_obstacles caps the obstacle count on dense diagrams (see its
        # docstring) — below the cap (both shipped fixtures) this is
        # ``list(node_geo.values())`` unchanged.
        obstacles = _nearby_obstacles(exit_pt, entry_pt, node_geo)
        interior = _route_around(exit_pt, entry_pt, p.exit_side, p.entry_side,
                                 obstacles, net_sep,
                                 src_rect=node_geo.get(p.src_id),
                                 dst_rect=node_geo.get(p.dst_id))
        if not interior:
            out[p.eid] = wps
            continue
        new_path = [exit_pt] + interior + [entry_pt]
        after = sum(_seg_pierces(a, b, node_geo, skip) for a, b in _segments(new_path))
        out[p.eid] = interior if after < before else wps
    return out


# ── Crossing reduction (Task 9A) ─────────────────────────────────────────────
def _perm_hook(perm: dict):
    """Build a ``lane_order`` / ``port_order`` callable from a
    ``{frozenset(eids): [eid…]}`` map. For a group whose id-set is a key it
    returns the group reordered to match; otherwise the group is returned
    unchanged. Always a TRUE permutation (it reorders the SAME ``_Plan``
    objects — never drops or invents one, never leaves an id at a 0-offset),
    so it is safe against the ``assign_ports_lanes`` hook footguns. Returns
    ``None`` for an empty ``perm`` (i.e. the default barycenter order)."""
    if not perm:
        return None

    def hook(group):
        order = perm.get(frozenset(p.eid for p in group))
        if order is None:
            return group
        rank = {eid: i for i, eid in enumerate(order)}
        return sorted(group, key=lambda p: rank.get(p.eid, 0))

    return hook


def _naive_crossings(plans, layout, lane_perm, port_perm) -> int:
    """Crossing count of the NAIVE (pre-obstacle-avoidance) paths for one
    candidate ``(lane_perm, port_perm)`` — cheap (no A* reroute), so the greedy
    can evaluate many candidates. The naive count is the right search signal
    for the reorderable channel bundles (gutters/corridors) whose edges are
    NOT rerouted; the final accept/reject step below re-checks the true
    post-avoidance cost so the search can never make the shipped diagram
    worse."""
    port_fracs, lane_offsets = assign_ports_lanes(
        plans, layout, lane_order=_perm_hook(lane_perm), port_order=_perm_hook(port_perm))
    paths = {}
    for p in plans:
        efr, nfr = port_fracs[p.eid]
        exit_pt = _abs_port(p.src, efr)
        entry_pt = _abs_port(p.dst, nfr)
        paths[p.eid] = ([exit_pt]
                        + _build_waypoints(p, exit_pt, entry_pt, lane_offsets.get(p.eid, 0.0))
                        + [entry_pt])
    return _count_crossings(paths)


def _route_cost(plans, layout, lane_order, port_order) -> tuple[int, int]:
    """Lexicographic cost ``(slot_fallbacks, crossings)`` of a candidate
    ordering, on the naive channel paths. Ordering the objective this way lets
    the accept/reject step reject any reorder that would buy a crossing at the
    price of a pill/label slot that can't be placed collision-free — so Task
    8's collision-free pill/label invariant survives the reshuffle. (Measured
    on the naive paths, not the obstacle-avoided ones: crossing reduction is a
    channel-routing concern; a reorder that helps the naive channel layout
    without breaking a slot is always safe to keep, and one that doesn't is
    declined — cheap, and independent of the Task 9B reroute.)"""
    node_geo = _obstacle_geo(layout)
    port_fracs, lane_offsets = assign_ports_lanes(
        plans, layout, lane_order=lane_order, port_order=port_order)
    paths = {}
    for p in plans:
        efr, nfr = port_fracs[p.eid]
        exit_pt = _abs_port(p.src, efr)
        entry_pt = _abs_port(p.dst, nfr)
        paths[p.eid] = ([exit_pt]
                        + _build_waypoints(p, exit_pt, entry_pt, lane_offsets.get(p.eid, 0.0))
                        + [entry_pt])
    crossings = _count_crossings(paths)
    _pill, _label, slot_fallbacks = _place_pills_and_labels(plans, paths, node_geo)
    return (len(slot_fallbacks), crossings)


def reduce_crossings(plans, layout, *, max_sweeps: int = CROSS_MAX_SWEEPS):
    """STEP 1.5 (Task 9A) — deterministic greedy crossing reduction over the
    seam's lane/port ordering hooks.

    Seeds each channel's lane group and each per-(node, side) port group with
    its default barycenter order, then bubbles adjacent pairs, keeping any swap
    that lowers the GLOBAL NAIVE crossing count (cheap; ties keep the earlier
    order — stable, no randomness; groups are visited lanes-then-ports in
    seed-insertion order, so the same input yields the same candidate
    byte-for-byte). Repeats until a whole sweep makes no improvement, the count
    hits 0, or ``max_sweeps`` is reached.

    The winning candidate is then ACCEPTED only if it lowers ``_route_cost``
    — lexicographic ``(slot_fallbacks, crossings)`` on the NAIVE channel
    paths (FIX-4: ``_route_cost`` builds these with the same single-edge
    ``_build_waypoints`` this search already uses, deliberately BEFORE
    ``_avoid_obstacles`` — see its own docstring; crossing reduction is a
    channel-routing concern, independent of the Task 9B reroute, so the
    final post-avoidance geometry is never computed just to evaluate a
    candidate here) — versus the default order; otherwise the default
    (barycenter) order is kept. This guard is what makes the lever safe: on
    a diagram like nova-L1, where obstacle avoidance dominates and a naive-
    crossing-optimal port reshuffle would only shove an edge's label into a
    slot that can't be placed, the reorder is simply declined. On a
    reorderable bundle (the crafted 4-edge crossing case) it is a clear win.

    Returns ``(lane_order, port_order)`` — hook callables for
    ``assign_ports_lanes``, or ``(None, None)`` when the default order is kept.
    FOOTGUN: ``assign_ports_lanes`` mutates ``channel.lanes`` in place on EVERY
    evaluation, so the caller MUST re-run ``assign_ports_lanes`` with the
    returned hooks before reading ``.lanes`` / emitting (``route()`` does)."""
    by_channel: dict[str, list[_Plan]] = {}
    for p in plans:
        if p.channel is not None:
            by_channel.setdefault(p.channel.id, []).append(p)
    lane_perm: dict = {}
    for grp in by_channel.values():
        ordered = sorted(grp, key=lambda p: (p.src_bary, p.dst_bary, p.eid))
        lane_perm[frozenset(p.eid for p in ordered)] = [p.eid for p in ordered]

    exit_groups, entry_groups = port_groups(plans)
    port_perm: dict = {}
    for grp in list(exit_groups.values()) + list(entry_groups.values()):
        port_perm[frozenset(p.eid for p in grp)] = [p.eid for p in grp]

    best = _naive_crossings(plans, layout, lane_perm, port_perm)
    improved = True
    sweeps = 0
    while improved and sweeps < max_sweeps and best > 0:
        improved = False
        sweeps += 1
        for perm in (lane_perm, port_perm):
            for fs in list(perm.keys()):
                order = perm[fs]
                for i in range(len(order) - 1):
                    cand = list(order)
                    cand[i], cand[i + 1] = cand[i + 1], cand[i]
                    saved = perm[fs]
                    perm[fs] = cand
                    trial = _naive_crossings(plans, layout, lane_perm, port_perm)
                    if trial < best:
                        best, order, improved = trial, cand, True
                    else:
                        perm[fs] = saved

    lane_order = _perm_hook(lane_perm)
    port_order = _perm_hook(port_perm)
    # Accept the reorder only if it beats the default on the FINAL geometry.
    if _route_cost(plans, layout, lane_order, port_order) < _route_cost(plans, layout, None, None):
        return lane_order, port_order
    return None, None


# ── Channel construction from the layout meta ────────────────────────────────
def _build_channels(layout: dict) -> tuple[list[tuple[str, float, float]],
                                            dict[int, Channel], dict[str, Channel],
                                            list[Channel]]:
    """Return (present columns L→R, gutter channels by left-column index,
    corridor channels by "top"/"bottom", all channels)."""
    meta = layout.get("meta", {})
    columns = meta.get("columns", {})
    canvas = layout.get("canvas", (0, 0))

    cols: list[tuple[str, float, float]] = []
    for name in _COLS:
        ext = columns.get(name)
        if ext and ext[1] > ext[0]:
            cols.append((name, float(ext[0]), float(ext[1])))

    # content bbox → corridor y-levels clear of all nodes
    nodes = layout.get("nodes", {})
    if nodes:
        tops = [v[1] for v in nodes.values()]
        bots = [v[1] + v[3] for v in nodes.values()]
        content_top, content_bot = min(tops), max(bots)
        lefts = [v[0] for v in nodes.values()]
        rights = [v[0] + v[2] for v in nodes.values()]
        content_l, content_r = min(lefts), max(rights)
    else:
        content_top = content_bot = 0.0
        content_l, content_r = 0.0, float(canvas[0])

    col_y0 = content_top
    col_h = max(1.0, content_bot - content_top)

    channels: list[Channel] = []
    gutters: dict[int, Channel] = {}
    for i in range(len(cols) - 1):
        x0 = cols[i][2]
        x1 = cols[i + 1][1]
        if x1 <= x0:                                  # touching columns: thin gutter
            x0, x1 = x0 - 1.0, x0 + 1.0
        ch = Channel(id=f"V{i}", axis="v",
                     rect=Rect(x0, col_y0, x1 - x0, col_h))
        gutters[i] = ch
        channels.append(ch)

    corridors: dict[str, Channel] = {}
    if len(cols) >= 2:
        cw = max(1.0, content_r - content_l)
        top_y = content_top - CORRIDOR_MARGIN
        bot_y = content_bot + CORRIDOR_MARGIN
        ct = Channel(id="Htop", axis="h",
                     rect=Rect(content_l, top_y - CHANNEL_BASE_W / 2,
                               cw, CHANNEL_BASE_W))
        cb = Channel(id="Hbot", axis="h",
                     rect=Rect(content_l, bot_y - CHANNEL_BASE_W / 2,
                               cw, CHANNEL_BASE_W))
        corridors["top"], corridors["bottom"] = ct, cb
        channels.append(ct)
        channels.append(cb)

    return cols, gutters, corridors, channels


# ── Public entry point (composable pipeline — see module docstring) ─────────
def plan(diagram, layout: dict) -> _Plans:
    """STEP 1 — region graph + per-edge channel-segment classification.

    Pure function of ``(diagram, layout)``; edges are processed in IR order
    and every tie-break carries the edge id, so re-running it on the same
    input reproduces identical plans. Returns a ``_Plans`` (use it exactly
    like ``list[_Plan]``; it also carries ``.channels`` — see ``_Plans``).
    """
    node_geo = {nid: _rect(t) for nid, t in layout.get("nodes", {}).items()}
    cols, gutters, corridors, channels = _build_channels(layout)

    # region adjacency (left↔center↔right chain)
    adj: dict[int, list[int]] = {}
    for i in range(len(cols)):
        nb = []
        if i - 1 >= 0:
            nb.append(i - 1)
        if i + 1 < len(cols):
            nb.append(i + 1)
        adj[i] = nb

    items: list[_Plan] = []
    for e in diagram.edges:
        src = node_geo.get(e.source)
        dst = node_geo.get(e.target)
        if src is None or dst is None:
            continue
        sc = _column_of(src, cols) if cols else 0
        dc = _column_of(dst, cols) if cols else 0
        # BFS the region graph → the sequence of columns (hence gutters) this
        # edge crosses; its hop count drives the adjacent/long/intra choice.
        region_path = _bfs_columns(adj, sc, dc)
        p = _plan_edge(e.id, src, dst, e.source, e.target, sc, dc,
                       region_path, gutters, corridors)
        p.pill = getattr(e, "pill", None)
        p.label = getattr(e, "label", None)
        items.append(p)
    return _Plans(items, channels)


def assign_ports_lanes(plans: list[_Plan], layout: dict, *,
                       lane_order=None, port_order=None
                       ) -> tuple[dict[str, tuple[tuple, tuple]], dict[str, float]]:
    """STEP 2 — fractional port assignment (8c) + per-edge lane offsets (8b).

    Returns ``(port_fracs, lane_offsets)``:
      * ``port_fracs``    ``{eid: ((exitX,exitY), (entryX,entryY))}`` —
        fractional anchors on the box side, barycenter-distributed.
      * ``lane_offsets``  ``{eid: float}`` — perpendicular offset from the
        edge's shared channel centre-line (0.0 for edges with no channel,
        e.g. ``"intra"`` — same meaning as it always has).

    ``lane_order`` / ``port_order`` are optional reordering hooks forwarded
    to ``_allocate_lanes`` / ``_assign_ports`` (see their docstrings) for
    Task 9's crossing-reduction search; both default to ``None``, which
    reproduces today's stable barycenter ordering exactly — the default path
    is byte-identical to the pre-seam ``route()``. Each hook MUST return a
    true permutation of the group it's given (same edge ids, none dropped or
    duplicated) — ``_assign_ports`` fails loudly (``KeyError``) on a dropped
    id, but a lane_order that drops one is NOT caught here: the missing
    edge's ``lane_offsets`` entry is simply absent, and ``build_waypoints``
    silently substitutes ``0.0`` for it. A search implementation should only
    ever permute, never filter, the group it receives.

    Works with any ``list[_Plan]``, not just a ``_Plans`` from ``plan()``: if
    ``plans`` has no ``.channels`` (e.g. a hand-built list in a test), the
    channels actually referenced by ``plans`` are used instead — every
    channel with zero plans routed through it contributes nothing to lane
    allocation anyway (see ``_allocate_lanes``), so this is equivalent to the
    full list for this function's purpose.

    NOTE for a search loop trying several candidates on the same ``plans``:
    ``_allocate_lanes`` mutates ``channel.lanes`` (and hence ``plans.
    channels[*].lanes``) in place on EVERY call, so after comparing several
    ``lane_order`` candidates it reflects only whichever call ran last, not
    necessarily the winner. Re-run ``assign_ports_lanes`` with the winning
    ``lane_order`` immediately before reading ``.lanes`` (or before the final
    ``build_waypoints`` call that produces the diagram to keep).
    """
    channels = getattr(plans, "channels", None)
    if channels is None:
        seen: dict[str, Channel] = {}
        for p in plans:
            if p.channel is not None:
                seen[p.channel.id] = p.channel
        channels = list(seen.values())

    net_sep = (layout.get("meta") or {}).get("networkSeparator")
    port_fracs = _assign_ports(plans, port_order=port_order)
    lane_offsets = _allocate_lanes(channels, plans, net_sep, lane_order=lane_order)
    return port_fracs, lane_offsets


def build_waypoints(plans: list[_Plan],
                    port_fracs: dict[str, tuple[tuple, tuple]],
                    lane_offsets: dict[str, float],
                    layout: dict
                    ) -> tuple[dict[str, list[tuple[float, float]]],
                               dict[str, tuple[float, float]],
                               dict[str, tuple[float, float]],
                               int, int, list[str]]:
    """STEP 3 — absolute ports, interior waypoints (with the Task 9B
    obstacle-avoidance reroute folded in), pill/label slots and the crossing +
    piercing counts, given ``plans`` (from ``plan()``) and the
    ``(port_fracs, lane_offsets)`` ``assign_ports_lanes()`` computed.

    Returns ``(waypoints, pill_pos, label_pos, crossings, piercings,
    slot_fallbacks)``. ``layout`` supplies node geometry for both the
    obstacle-avoidance obstacle set and the pill/label obstacle set — pass the
    SAME ``layout`` used for ``plan()``/``assign_ports_lanes()``.

    The naive channel waypoints are built first, then ``_avoid_obstacles``
    reroutes any edge that would pierce a non-endpoint node box AROUND it
    (Task 9B); crossings, piercings and pill/label slots are all computed on
    the FINAL post-avoidance paths. This is the step Task 9's crossing search
    re-runs after reordering lanes/ports: same ``plans``, a different
    ``(port_fracs, lane_offsets)`` in, different (still deterministic)
    waypoints out — the routing decisions of steps 1-2 are mechanically
    realised here, no re-planning.
    """
    node_geo = _obstacle_geo(layout)

    ports: dict[str, tuple[tuple, tuple]] = {}
    waypoints: dict[str, list[tuple[float, float]]] = {}
    for p in plans:
        efr, nfr = port_fracs[p.eid]
        exit_pt = _abs_port(p.src, efr)
        entry_pt = _abs_port(p.dst, nfr)
        ports[p.eid] = (exit_pt, entry_pt)
        waypoints[p.eid] = _build_waypoints(p, exit_pt, entry_pt,
                                            lane_offsets.get(p.eid, 0.0))

    # ── obstacle-aware reroute (9B): route piercing edges AROUND node boxes ──
    waypoints = _avoid_obstacles(plans, ports, waypoints, layout)

    # ── full paths (exit + waypoints + entry) for the crossing/piercing counts
    paths = {p.eid: [ports[p.eid][0]] + waypoints[p.eid] + [ports[p.eid][1]]
             for p in plans}
    crossings = _count_crossings(paths)
    piercings = count_piercings(paths, node_geo,
                                {p.eid: (p.src_id, p.dst_id) for p in plans})

    # ── pill & label slots (8e) ─────────────────────────────────────────────
    pill_pos, label_pos, slot_fallbacks = _place_pills_and_labels(plans, paths, node_geo)

    return waypoints, pill_pos, label_pos, crossings, piercings, slot_fallbacks


def route(diagram, layout: dict) -> RouteResult:
    """Route every edge of ``diagram`` through the reserved channels of
    ``layout``. See the module docstring for the model. Deterministic: edges
    are processed in IR order and every tie-break carries the edge id.

    ``route()`` is the composition of the pipeline steps above:
    ``plan`` → ``reduce_crossings`` (Task 9A — picks the lane/port ordering
    that minimises crossings) → ``assign_ports_lanes`` (with those winners) →
    ``build_waypoints`` (which now also folds in the Task 9B obstacle-avoidance
    reroute), plus the absolute-ports arithmetic ``RouteResult.ports`` needs.
    A caller that wants to intervene between steps (e.g. Task 12/13) calls the
    same functions directly against the SAME ``plans`` — the seam is intact.

    NOTE for Task 13: this hardcodes ``reduce_crossings`` as the ONLY
    ``(lane_order, port_order)`` source between ``plan`` and
    ``assign_ports_lanes``. Task 13's rubric hooks (``channel_prefer`` /
    ``order_override``) will need a composition path that applies BOTH a
    rubric override AND crossing reduction (e.g. seed the greedy sweep from
    the rubric's order instead of the default barycenter one, or run the
    rubric override first and let ``reduce_crossings`` only accept a further
    swap that doesn't undo it) — not designed here, just flagged so it isn't
    a surprise.
    """
    plans = plan(diagram, layout)
    lane_order, port_order = reduce_crossings(plans, layout)
    port_fracs, lane_offsets = assign_ports_lanes(
        plans, layout, lane_order=lane_order, port_order=port_order)
    waypoints, pill_pos, label_pos, crossings, piercings, slot_fallbacks = build_waypoints(
        plans, port_fracs, lane_offsets, layout)

    ports: dict[str, tuple[tuple, tuple]] = {}
    for p in plans:
        efr, nfr = port_fracs[p.eid]
        ports[p.eid] = (_abs_port(p.src, efr), _abs_port(p.dst, nfr))

    return RouteResult(
        waypoints=waypoints,
        ports=ports,
        port_fracs=port_fracs,
        pill_pos=pill_pos,
        label_pos=label_pos,
        channels=plans.channels,
        crossings=crossings,
        piercings=piercings,
        slot_fallbacks=slot_fallbacks,
    )
