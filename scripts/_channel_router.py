#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""Deterministic **channel router** for sap-diagrams-pro (Task 8).

The skeleton layout (``_skeleton_layout.compute_layout``) positions every box
but returns ``edges: {}`` — edge routing was left to draw.io's defaults, which
produced the tangled centre the user saw (crossing lines, colliding labels,
edges piercing box borders). This module replaces that with a *structural*
router that exploits the fixed skeleton composition instead of solving a
general orthogonal-routing problem:

* The **gutters** between the left / center / right columns are reserved
  vertical channels; horizontal **corridors** are reserved above and below the
  column content. Every inter-column edge travels through these corridors, so
  its segments are overlap-free *by construction* once each edge gets its own
  parallel **lane** within a channel (offset by a fixed pitch).
* **Ports** — where an edge attaches to a box — are distributed across each
  box side by barycenter so a fan of connectors leaving one node spreads out
  instead of stacking on the side midpoint.
* Edge **pills** (protocol chips like "SCIM") and **labels** are dropped into a
  per-channel slot grid, scanned to the first collision-free slot.

Everything is pure-Python and deterministic: no datetime, no randomness, every
sort carries a stable ``(…, id)`` tie-break, so the same IR yields byte-
identical waypoints.

Coordinate space is draw.io's top-left-origin canvas (x grows right, y grows
down); waypoints are ABSOLUTE canvas coordinates (matching how the pure-Python
preview renderer and draw.io both treat an edge's ``<Array as="points">``).

Public API::

    route(diagram, layout) -> RouteResult

``layout`` is the dict returned by ``compute_layout`` (needs ``nodes``,
``groups``, ``canvas`` and ``meta`` with ``columns`` + ``networkSeparator``).
``RouteResult`` exposes ``waypoints`` (interior bend points per edge — the
renderer prepends the exit port and appends the entry port), ``ports`` /
``port_fracs`` (absolute + fractional attach points), ``pill_pos`` /
``label_pos`` (absolute centres), ``channels`` (the reserved corridors) and a
``crossings`` count (segment/segment crossings, the baseline Task 9 reduces).
"""
from __future__ import annotations

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

_COLS = ("left", "center", "right")


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


# ── Geometry helpers ─────────────────────────────────────────────────────────
def _rect(t: tuple[float, float, float, float]):
    return Rect(float(t[0]), float(t[1]), float(t[2]), float(t[3]))


def _side_point(r, side: str, frac: float) -> tuple[float, float]:
    """Absolute point on ``side`` of rect ``r`` at fraction ``frac`` along it."""
    if side == "R":
        return (r.right, r.y + frac * r.h)
    if side == "L":
        return (r.x, r.y + frac * r.h)
    if side == "T":
        return (r.x + frac * r.w, r.y)
    return (r.x + frac * r.w, r.bottom)              # "B"


def _side_frac(r, side: str, frac: float) -> tuple[float, float]:
    """Fractional (exitX, exitY)-style anchor for ``side`` at ``frac``."""
    if side == "R":
        return (1.0, frac)
    if side == "L":
        return (0.0, frac)
    if side == "T":
        return (frac, 0.0)
    return (frac, 1.0)                               # "B"


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
def _plan_edge(eid, src, dst, src_id, dst_id, src_col, dst_col, gutters,
               corridors) -> _Plan:
    """Classify one edge and pick its exit/entry sides + shared channel.

    Three structural cases exploit the skeleton:
      * ``adjacent`` (columns differ by one) — an H-V-H route whose vertical
        segment sits in the shared gutter.
      * ``long`` (columns two apart) — a route out to a horizontal corridor,
        one horizontal segment across BOTH gutters, then back in.
      * ``intra`` (same column) — an orthogonal L/Z jog inside the column,
        dominant axis first.
    """
    dcol = dst_col - src_col
    if abs(dcol) == 1 and gutters:
        gi = min(src_col, dst_col)                    # gutter left of the higher col
        ch = gutters.get(gi)
        if ch is not None:
            if dcol > 0:                              # target to the right
                return _Plan(eid, src, dst, src_id, dst_id, src_col, dst_col,
                             "adjacent", "R", "L", ch, src.cy, dst.cy)
            return _Plan(eid, src, dst, src_id, dst_id, src_col, dst_col,
                         "adjacent", "L", "R", ch, src.cy, dst.cy)

    if abs(dcol) >= 2 and corridors:
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


def _assign_ports(plans: list[_Plan]) -> dict[str, tuple[tuple, tuple]]:
    """Fractional (exit, entry) anchor per edge, distributed per box side.

    Edges leaving the SAME side of the SAME box are grouped, ordered by the
    barycenter of their far endpoint, and handed fractions spread evenly across
    ``[0.25, 0.75]`` of that side — so a fan of connectors diverges instead of
    stacking on the side midpoint. Entry ports are distributed the same way on
    the target side. Deterministic: every group is sorted with an ``eid``
    tie-break."""
    from collections import defaultdict
    exit_groups: dict[tuple[str, str], list[_Plan]] = defaultdict(list)
    entry_groups: dict[tuple[str, str], list[_Plan]] = defaultdict(list)
    for p in plans:
        exit_groups[(p.src_id, p.exit_side)].append(p)
        entry_groups[(p.dst_id, p.entry_side)].append(p)

    exit_frac: dict[str, tuple[float, float]] = {}
    entry_frac: dict[str, tuple[float, float]] = {}
    for (_nid, side), group in exit_groups.items():
        group.sort(key=lambda p: (_port_bary(p.dst, side), p.eid))
        for i, p in enumerate(group):
            exit_frac[p.eid] = _side_frac(p.src, side, _spread(len(group), i))
    for (_nid, side), group in entry_groups.items():
        group.sort(key=lambda p: (_port_bary(p.src, side), p.eid))
        for i, p in enumerate(group):
            entry_frac[p.eid] = _side_frac(p.dst, side, _spread(len(group), i))

    return {p.eid: (exit_frac[p.eid], entry_frac[p.eid]) for p in plans}


# ── Lanes (8b) ───────────────────────────────────────────────────────────────
def _allocate_lanes(channels: list[Channel], plans: list[_Plan],
                    net_sep: dict | None = None) -> dict[str, float]:
    """Per-edge perpendicular offset from its channel centre-line.

    Every edge sharing a channel gets its own parallel lane, so their in-channel
    segments are overlap-free *by construction*. Within a channel the edges are
    sorted by ``(src_bary, dst_bary, eid)`` (the "(src.y, dst.y, id)" rule of
    the plan, projected onto the channel's relevant axis) and handed lane
    indices ``0..n-1``; the offset is ``(i-(n-1)/2)*pitch``, centring the bundle
    on the centre-line. The pitch is ``LANE_PITCH`` (12px) but is reduced to fit
    a narrow vertical gutter — never below 10px, so the parallel lanes always
    stay a legible distance apart. ``channel.lanes`` records each edge's index.
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


# ── Public entry point ───────────────────────────────────────────────────────
def route(diagram, layout: dict) -> RouteResult:
    """Route every edge of ``diagram`` through the reserved channels of
    ``layout``. See the module docstring for the model. Deterministic: edges
    are processed in IR order and every tie-break carries the edge id."""
    node_geo = {nid: _rect(t) for nid, t in layout.get("nodes", {}).items()}
    meta = layout.get("meta", {})
    net_sep = meta.get("networkSeparator")

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

    # ── plan every edge (IR order) ──────────────────────────────────────────
    plans: list[_Plan] = []
    for e in diagram.edges:
        src = node_geo.get(e.source)
        dst = node_geo.get(e.target)
        if src is None or dst is None:
            continue
        sc = _column_of(src, cols) if cols else 0
        dc = _column_of(dst, cols) if cols else 0
        # walk the region graph so multi-column hops are explicit (Task 9 hook)
        _ = _bfs_columns(adj, sc, dc)
        plans.append(_plan_edge(e.id, src, dst, e.source, e.target, sc, dc,
                                gutters, corridors))

    # ── ports (8c), lanes (8b) ──────────────────────────────────────────────
    port_fracs = _assign_ports(plans)
    lane_off = _allocate_lanes(channels, plans, net_sep)

    # ── absolute ports + waypoints ──────────────────────────────────────────
    ports: dict[str, tuple[tuple, tuple]] = {}
    waypoints: dict[str, list[tuple[float, float]]] = {}
    for p in plans:
        efr, nfr = port_fracs[p.eid]
        exit_pt = (p.src.x + efr[0] * p.src.w, p.src.y + efr[1] * p.src.h)
        entry_pt = (p.dst.x + nfr[0] * p.dst.w, p.dst.y + nfr[1] * p.dst.h)
        ports[p.eid] = (exit_pt, entry_pt)
        waypoints[p.eid] = _build_waypoints(p, exit_pt, entry_pt,
                                            lane_off.get(p.eid, 0.0))

    # ── full paths (exit + waypoints + entry) for crossing count ────────────
    paths = {p.eid: [ports[p.eid][0]] + waypoints[p.eid] + [ports[p.eid][1]]
             for p in plans}
    crossings = _count_crossings(paths)

    return RouteResult(
        waypoints=waypoints,
        ports=ports,
        port_fracs=port_fracs,
        pill_pos={},
        label_pos={},
        channels=channels,
        crossings=crossings,
    )
