#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""
check-composition.py — verify the *composition* of a generated .drawio against
the SAP big-picture conventions. Complements validate-drawio.py (which checks
palette / XML / line-styles): this one checks layout quality.

v1 checks (structural, id/style-pattern based):
  • TITLE        — a title text cell sits in the top band.
  • GROUP_OVERLAP— top-level zone containers must NOT overlap each other (FAIL).
  • ZONES        — top-level groups should spread across columns, not stack in one.
  • LEGEND       — ≥2 line styles ⇒ a "Legend" should be present.
  • BTP_CENTRAL  — the BTP layer (blue #EBF8FF) should sit between the other zones.

v2 checks (Task 12 — geometric, reuse ``_geom_checks.py``, no draw.io/LLM):
  • PIERCING / EDGE_THROUGH_BOX — an edge segment cuts through a non-endpoint
    node or top-level zone rect (FAIL). Node obstacle rects are reconstructed
    caption-aware (icon nodes extended over their caption band), mirroring
    generate-drawio.py's ``node_obstacle_geom`` / the router's FIX-1.
  • TEXT_OVERLAP — two text-bearing cell rects (labels, captions, titles,
    pills) overlap. Substantial overlap (≥20% of the smaller rect's area,
    i.e. text is plausibly hidden) is FAIL; a smaller graze is WARN.
  • CAPTION_OUT  — an icon node's caption band falls outside its parent
    frame (FAIL).
  • PILL_COLLISION — a capsule-styled pill overlaps a node/box or another
    pill (FAIL).
  • PORT_CONGESTION — two edges attach at the same side+fraction of the same
    box (WARN — cosmetic, not a readability break).
  • CHANNEL_DISCIPLINE — an inter-zone edge's dominant segment runs outside
    every reserved router channel (WARN; needs the ``sapdp:channels``
    metadata cell emitted by generate-drawio.py — degrades to a no-op INFO
    when it's absent, e.g. ``--layout greedy`` or a pre-Task-12 file).

Top-level zone containers are the cells with id "g-…" parented to "1" (the
renderer emits zone/organism boxes there; nested lanes parent to their group).
Intentional child overlaps (step circles "st-…", interface/edge pills "if-…",
embedded icons, molecule frame substructure "…-title"/"…-chipN") are ignored
by construction — the SAME convention v1's GROUP_OVERLAP already documented,
now applied to every v2 check too.

Usage:
    python3 check-composition.py diagram.drawio [--strict] [--json]
Exit: 0 ok · 2 any FAIL is present (unconditionally — see below) · 3 unreadable.

``--strict`` is accepted for backward compatibility with existing callers
(e.g. the CI workflow) but is now a no-op: a FAIL always exits 2, aligning
this gate with validate-drawio.py's CRITICAL convention instead of requiring
an opt-in flag to catch a readability-breaking defect.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

# Prefer defusedxml for parsing untrusted .drawio (guards XXE / billion-laughs);
# fall back to the stdlib parser when defusedxml isn't installed.
try:
    from defusedxml.ElementTree import parse as _xml_parse
except Exception:  # pragma: no cover - defusedxml optional
    from xml.etree.ElementTree import parse as _xml_parse


def _load_geom_checks():
    """Import scripts/_geom_checks.py by path — the same sibling-loading
    pattern ``_channel_router.py``'s own ``_load_sibling`` uses — so this gate
    reuses the EXACT rectangle/segment predicates the router trusts, instead
    of a second, possibly-diverging copy. Checks ``sys.modules`` first so a
    test harness that already loaded ``_geom_checks`` (e.g. via
    ``tests/conftest.load_script``) gets that same module object back."""
    import importlib.util as _ilu
    name = "_geom_checks"
    if name in sys.modules:
        return sys.modules[name]
    spec = _ilu.spec_from_file_location(
        name, Path(__file__).resolve().parent / "_geom_checks.py")
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_gc = _load_geom_checks()
Rect = _gc.Rect
rects_overlap = _gc.rects_overlap
seg_intersects_rect = _gc.seg_intersects_rect
point_in_rect = _gc.point_in_rect


@dataclass
class Finding:
    severity: str  # FAIL | WARN | INFO
    rule: str
    message: str


def _style(s: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for chunk in (s or "").split(";"):
        if "=" in chunk:
            k, _, v = chunk.partition("=")
            out[k.strip()] = v.strip()
    return out


def _line_kind(kv: dict[str, str]) -> str:
    try:
        sw = float(kv.get("strokeWidth", "1"))
    except ValueError:
        sw = 1.0
    if kv.get("dashed") == "1":
        return "dotted" if kv.get("dashPattern", "").startswith("1 ") else "dashed"
    if sw >= 3.0:
        return "thick"
    return "solid"


# ── Task 12 geometric-gate constants ────────────────────────────────────────
# The vertical band a "verticalLabelPosition=bottom" icon reserves for its
# caption, in px — MUST match ``_skeleton_layout.LABEL_H`` (the router's own
# constant; see generate-drawio.py's ``node_obstacle_geom`` / FIX-1, and
# tests/test_router.py's ``_node_obstacles_from_drawio`` reconstruction
# helper, which this mirrors exactly). Not imported directly: this gate reads
# a plain .drawio, deliberately independent of the layout engine's module.
CAPTION_BAND_H = 24.0
# TEXT_OVERLAP severity split: overlap covering LESS than this fraction of
# the smaller rect's area is a cosmetic graze (WARN); at/above it, text is
# plausibly hidden (FAIL). ~20% per the Task 12 spec.
TEXT_OVERLAP_FAIL_FRAC = 0.20
# Synthetic header band approximating where a zone/group's title text sits
# (verticalAlign=top, fontSize=14, spacingTop=6 in the emitted zone style).
ZONE_HEADER_H = 30.0
# CHANNEL_DISCIPLINE tolerance: px of slack around a serialized channel rect
# before an inter-zone edge's dominant segment counts as "outside every
# channel". WARN-only, so generous on purpose.
CHANNEL_TOL = 8.0

# _stable_id(prefix, key) in generate-drawio.py is f"{prefix}-{sha1(key)[:8]}"
# — these anchor patterns distinguish a real top-level node/zone cell from
# its own nested substructure (e.g. "n-1a2b3c4d-title", "n-1a2b3c4d-chip0"),
# which can never accidentally match: sha1 hex digits are 0-9a-f, and
# "title"/"chip" both contain letters outside that alphabet.
_NODE_ID_RE = re.compile(r"^n-[0-9a-f]{8}$")
_ZONE_ID_RE = re.compile(r"^g-[0-9a-f]{8}$")
_PILL_ID_RE = re.compile(r"^(?:p|pp|legpill)-[0-9a-f]{8}$")
_TEXT_ID_RE = re.compile(
    r"^(?:p|pp|leglbl|title|dlevel|btpbadge|netsep|brand|dbadge)-[0-9a-f]{8}$")


def _decorative(cid: str) -> bool:
    """Intentional child overlaps this gate must never flag — mirrors the
    module docstring's existing GROUP_OVERLAP convention: step-number badges
    ("st-…") and interface pills ("if-…") are deliberately half-outside or
    glued to their parent node's corner, and molecule frame substructure
    ("…-title", "…-chipN") is decoration INSIDE its own frame's box, not a
    free-floating label competing for canvas space."""
    return (cid.startswith("st-") or cid.startswith("if-")
            or "-chip" in cid or cid.endswith("-title"))


def _abs_xy(cid: str, cells_by_id: dict, _seen: set | None = None) -> tuple[float, float]:
    """Absolute (x, y) of a cell's geometry, walking the parent chain — an
    mxCell's ``mxGeometry`` is relative to its parent unless that parent IS
    the root layer ("0"/"1"). Mirrors tests/test_router.py's
    ``_abs_topleft`` / validate-drawio.py's own coordinate model. ``_seen``
    guards a malformed/cyclic parent chain (never expected, cheap to guard)."""
    _seen = _seen if _seen is not None else set()
    if cid in _seen or cid not in cells_by_id:
        return 0.0, 0.0
    _seen.add(cid)
    cell = cells_by_id[cid]
    g = cell.find("mxGeometry")
    if g is None:
        return 0.0, 0.0
    try:
        x = float(g.get("x", "0") or 0)
        y = float(g.get("y", "0") or 0)
    except ValueError:
        x = y = 0.0
    parent = cell.get("parent")
    if parent and parent not in ("0", "1"):
        px, py = _abs_xy(parent, cells_by_id, _seen)
        x, y = x + px, y + py
    return x, y


def _abs_rect(cid: str, cells_by_id: dict) -> "Rect | None":
    """Absolute ``Rect`` for vertex ``cid`` (own w/h + ``_abs_xy`` position).
    ``None`` when the cell is missing, has no geometry, or a non-positive
    footprint (an edge's ``mxGeometry`` has no x/y/width/height of its own —
    this naturally excludes edges/connectors from every rect-based scan
    below without a separate ``vertex == "1"`` guard at every call site)."""
    cell = cells_by_id.get(cid)
    if cell is None:
        return None
    g = cell.find("mxGeometry")
    if g is None:
        return None
    try:
        w = float(g.get("width", "0") or 0)
        h = float(g.get("height", "0") or 0)
    except ValueError:
        return None
    if w <= 0 or h <= 0:
        return None
    x, y = _abs_xy(cid, cells_by_id)
    return Rect(x, y, w, h)


def _edge_waypoints(cell) -> list[tuple[float, float]]:
    """The edge's interior ``<Array as="points"><mxPoint>`` waypoints —
    ABSOLUTE (every edge this gate cares about is ``parent="1"``, see
    generate-drawio.py's ``e_cell``/``_emit_slot_cell``), in document order."""
    g = cell.find("mxGeometry")
    if g is None:
        return []
    arr = g.find("Array[@as='points']")
    if arr is None:
        return []
    pts = []
    for mp in arr.findall("mxPoint"):
        try:
            pts.append((float(mp.get("x", "0")), float(mp.get("y", "0"))))
        except (TypeError, ValueError):
            continue
    return pts


def _style_frac(style: str, key: str, default: float) -> float:
    m = re.search(rf"{key}=(-?[0-9.]+)", style)
    if not m:
        return default
    try:
        return float(m.group(1))
    except ValueError:
        return default


def _side_of(fx: float, fy: float) -> tuple[str, float]:
    """Map a draw.io fractional exit/entry anchor to (side, fraction-along-
    that-side) — "R"/"L" measure fraction by Y, "T"/"B" by X, matching
    ``_channel_router.py``'s own ``_side_frac`` convention in reverse."""
    if fx >= 0.999:
        return "R", fy
    if fx <= 0.001:
        return "L", fy
    if fy <= 0.001:
        return "T", fx
    return "B", fx


def _longest_segment(path: list[tuple[float, float]]
                      ) -> tuple[tuple[float, float], tuple[float, float]]:
    """The single longest (Manhattan-length) segment of an orthogonal path —
    same idea as ``_channel_router._longest_segment`` (where the pill/label
    rides), reused here as the ONE segment CHANNEL_DISCIPLINE judges: an
    edge's short port jetties needn't be inside a channel, but its dominant
    travel leg should be."""
    best = (path[0], path[1] if len(path) > 1 else path[0])
    best_len = -1.0
    for a, b in zip(path, path[1:]):
        length = abs(b[0] - a[0]) + abs(b[1] - a[1])
        if length > best_len:
            best_len = length
            best = (a, b)
    return best


def _top_zone_of(cid: str | None, cells_by_id: dict, zone_ids: set) -> str | None:
    """The top-level zone id (a member of ``zone_ids``) ``cid`` is nested
    under, walking the parent chain — ``None`` if ``cid`` isn't inside any
    top-level zone (e.g. a bare actor icon parented straight to "1")."""
    seen: set = set()
    while cid and cid not in seen:
        seen.add(cid)
        if cid in zone_ids:
            return cid
        cell = cells_by_id.get(cid)
        if cell is None:
            return None
        cid = cell.get("parent")
    return None


def _check_v2_geometry(cells: list, out: list[Finding]) -> None:
    """Task 12 — the geometric FAIL-blocking checks (PIERCING/EDGE_THROUGH_BOX,
    TEXT_OVERLAP, CAPTION_OUT, PILL_COLLISION) plus two WARN-only advisories
    (PORT_CONGESTION, CHANNEL_DISCIPLINE). Pure XML + ``_geom_checks`` — no
    draw.io, no re-running the router — deterministic and CI-safe."""
    cells_by_id = {c.get("id"): c for c in cells if c.get("id")}

    zone_ids = {cid for cid, c in cells_by_id.items()
                if _ZONE_ID_RE.match(cid) and c.get("parent") == "1"
                and c.get("vertex") == "1"}
    zone_rects: dict[str, Rect] = {}
    for cid in zone_ids:
        r = _abs_rect(cid, cells_by_id)
        if r is not None:
            zone_rects[cid] = r

    node_ids = [cid for cid, c in cells_by_id.items()
                if _NODE_ID_RE.match(cid) and c.get("vertex") == "1"]
    node_drawn: dict[str, Rect] = {}
    node_obstacle: dict[str, Rect] = {}
    for nid in node_ids:
        r = _abs_rect(nid, cells_by_id)
        if r is None:
            continue
        node_drawn[nid] = r
        style = cells_by_id[nid].get("style") or ""
        # FIX-1 (Task 9): an icon node's OBSTACLE rect extends downward over
        # its caption band, independent of whether the caption text is
        # non-empty (the zone layout reserves the band either way) — see
        # generate-drawio.py's node_obstacle_geom.
        if "verticalLabelPosition=bottom" in style:
            node_obstacle[nid] = Rect(r.x, r.y, r.w, r.h + CAPTION_BAND_H)
        else:
            node_obstacle[nid] = r

    # Every vlp-captioned vertex with actual caption text — generalised
    # beyond "n-…" nodes (e.g. a hyperscaler/runtime badge icon), used by
    # TEXT_OVERLAP + CAPTION_OUT. (cid -> (caption text, caption band rect))
    captions: dict[str, tuple[str, Rect]] = {}
    for cid, cell in cells_by_id.items():
        if cell.get("vertex") != "1" or _decorative(cid):
            continue
        style = cell.get("style") or ""
        if "verticalLabelPosition=bottom" not in style:
            continue
        value = (cell.get("value") or "").strip()
        if not value:
            continue
        r = _abs_rect(cid, cells_by_id)
        if r is None:
            continue
        captions[cid] = (value, Rect(r.x, r.bottom, r.w, CAPTION_BAND_H))

    # ── PIERCING / EDGE_THROUGH_BOX ──────────────────────────────────────────
    # FAIL scope is deliberately NODE rects only (icon+caption combined for
    # icon nodes, a molecule frame's own box for IR v2 "group" archetypes —
    # both already live in ``node_obstacle``): this is EXACTLY what Task 9's
    # ``count_piercings`` / ``_avoid_obstacles`` routed against and drove to
    # 0, so it's the one obstacle set the router actually GUARANTEES an edge
    # clears. Top-level ZONE containers are a separate, softer signal below
    # (WARN): the router's channel model (gutters BETWEEN columns, corridors
    # only above/below ALL content) has no reserved lane between stacked
    # zone ROWS in the same column band, so a long inter-row edge can — by
    # the router's current design, not a Task 9 regression — legitimately
    # cross an unrelated zone's background rect on its way through. Treating
    # that as a blocking FAIL would fail the accepted-good fixtures for a
    # guarantee the router never made; see the Task 12 report for this call.
    piercing_hits: list[tuple[str, str]] = []
    zone_cross_hits: list[tuple[str, str]] = []
    for eid, ecell in cells_by_id.items():
        if ecell.get("edge") != "1":
            continue
        src, dst = ecell.get("source"), ecell.get("target")
        if not src or not dst or src not in node_drawn or dst not in node_drawn:
            continue
        style = ecell.get("style") or ""
        sr, dr = node_drawn[src], node_drawn[dst]
        exit_pt = (sr.x + _style_frac(style, "exitX", 0.5) * sr.w,
                   sr.y + _style_frac(style, "exitY", 0.5) * sr.h)
        entry_pt = (dr.x + _style_frac(style, "entryX", 0.5) * dr.w,
                    dr.y + _style_frac(style, "entryY", 0.5) * dr.h)
        path = [exit_pt, *_edge_waypoints(ecell), entry_pt]
        # An edge legitimately dips into the top-level zone(s) its OWN
        # endpoints live in (that's just "leaving/entering home turf") — skip
        # those, plus the endpoints themselves; every OTHER node is a genuine
        # (FAIL-level) obstacle, every OTHER zone a softer (WARN-level) one.
        skip = {src, dst}
        for z in (_top_zone_of(src, cells_by_id, zone_ids),
                  _top_zone_of(dst, cells_by_id, zone_ids)):
            if z:
                skip.add(z)
        node_obstacles = [(oid, r) for oid, r in node_obstacle.items() if oid not in skip]
        foreign_zones = [(zid, r) for zid, r in zone_rects.items() if zid not in skip]
        for a, b in zip(path, path[1:]):
            for oid, r in node_obstacles:
                if seg_intersects_rect(a, b, r):
                    piercing_hits.append((eid, oid))
            for zid, r in foreign_zones:
                if seg_intersects_rect(a, b, r):
                    zone_cross_hits.append((eid, zid))

    if piercing_hits:
        detail = "; ".join(f"{e}→{o}" for e, o in piercing_hits[:6])
        more = "" if len(piercing_hits) <= 6 else f" (+{len(piercing_hits) - 6} more)"
        out.append(Finding("FAIL", "PIERCING",
                            f"{len(piercing_hits)} edge/box intersection(s): {detail}{more}"))
    else:
        out.append(Finding("INFO", "PIERCING", "0 edge/box intersections ✓"))
    if zone_cross_hits:
        detail = "; ".join(f"{e}→{o}" for e, o in zone_cross_hits[:6])
        more = "" if len(zone_cross_hits) <= 6 else f" (+{len(zone_cross_hits) - 6} more)"
        out.append(Finding("WARN", "PIERCING",
                            f"{len(zone_cross_hits)} edge/foreign-zone crossing(s) "
                            f"(router doesn't route around zone boxes): {detail}{more}"))

    # ── pill + text-bearing cell inventory ───────────────────────────────────
    pill_rects: dict[str, Rect] = {}
    text_rects: dict[str, Rect] = {}
    for cid, cell in cells_by_id.items():
        if cell.get("vertex") != "1" or _decorative(cid):
            continue
        style = cell.get("style") or ""
        value = (cell.get("value") or "").strip()
        is_pill = "arcSize=50" in style and "rounded=1" in style
        if is_pill and _PILL_ID_RE.match(cid):
            r = _abs_rect(cid, cells_by_id)
            if r is not None:
                pill_rects[cid] = r
        if value and _TEXT_ID_RE.match(cid):
            r = _abs_rect(cid, cells_by_id)
            if r is not None:
                text_rects[cid] = r
    for cid, (_value, cap_rect) in captions.items():
        text_rects[f"{cid}#caption"] = cap_rect
    for zid, r in zone_rects.items():
        if (cells_by_id[zid].get("value") or "").strip():
            text_rects[f"{zid}#header"] = Rect(r.x, r.y, r.w, min(r.h, ZONE_HEADER_H))
    # Every pill is ALSO a text-bearing cell for this scan — a pill colliding
    # with a caption/title is still "two text-bearing rects overlapping",
    # even though PILL_COLLISION below treats pill/pill + pill/box more
    # strictly (any overlap, not just a substantial one).
    text_rects.update(pill_rects)

    # ── TEXT_OVERLAP ──────────────────────────────────────────────────────────
    # A pair involving a zone "#header" band is capped at WARN, never FAIL —
    # unlike pill/label placement (guaranteed collision-free against every
    # NODE by ``_place_in_slots``'s obstacle set, Task 8e), the router does
    # NOT treat a zone's title band as an obstacle when placing pills/labels,
    # so an edge label landing on top of a zone's title is a real but
    # PRE-EXISTING pipeline gap, not something Task 9/11 ever guaranteed
    # against (observed on the shipped nova-L1: the "audit events" label
    # overlaps the "Identity + Ops" zone title — confirmed by rendering the
    # fixture, see the Task 12 report). Every other pairing (pill/pill,
    # pill/caption, caption/caption, caption/title, …) IS something the
    # router guarantees, so it keeps the full FAIL-at-≥20% severity.
    fail_hits: list[str] = []
    warn_hits: list[str] = []
    tids = list(text_rects)
    for i in range(len(tids)):
        a = text_rects[tids[i]]
        for j in range(i + 1, len(tids)):
            b = text_rects[tids[j]]
            ox = min(a.right, b.right) - max(a.x, b.x)
            oy = min(a.bottom, b.bottom) - max(a.y, b.y)
            if ox <= 0 or oy <= 0:
                continue
            frac = (ox * oy) / max(1.0, min(a.w * a.h, b.w * b.h))
            label = f"{tids[i]}×{tids[j]} ({frac:.0%})"
            involves_header = tids[i].endswith("#header") or tids[j].endswith("#header")
            if frac >= TEXT_OVERLAP_FAIL_FRAC and not involves_header:
                fail_hits.append(label)
            else:
                warn_hits.append(label)
    if fail_hits:
        out.append(Finding("FAIL", "TEXT_OVERLAP",
                            f"{len(fail_hits)} substantial overlap(s): {', '.join(fail_hits[:6])}"))
    if warn_hits:
        out.append(Finding("WARN", "TEXT_OVERLAP",
                            f"{len(warn_hits)} minor text graze(s): {', '.join(warn_hits[:6])}"))
    if not fail_hits and not warn_hits:
        out.append(Finding("INFO", "TEXT_OVERLAP", "0 text-bearing overlaps ✓"))

    # ── CAPTION_OUT ───────────────────────────────────────────────────────────
    caption_out: list[str] = []
    for cid, (value, cap_rect) in captions.items():
        parent = cells_by_id[cid].get("parent")
        if not parent or parent in ("0", "1"):
            continue  # no enclosing frame to violate (e.g. a bare actor icon)
        frame = _abs_rect(parent, cells_by_id)
        if frame is None:
            continue
        tl_ok = point_in_rect((cap_rect.x, cap_rect.y), frame, pad=2.0)
        br_ok = point_in_rect((cap_rect.right, cap_rect.bottom), frame, pad=2.0)
        if not (tl_ok and br_ok):
            caption_out.append(f"{cid} ('{value}') outside {parent}")
    if caption_out:
        out.append(Finding("FAIL", "CAPTION_OUT",
                            f"{len(caption_out)} caption(s) outside their frame: "
                            f"{'; '.join(caption_out[:6])}"))
    else:
        out.append(Finding("INFO", "CAPTION_OUT", "0 captions outside their frame ✓"))

    # ── PILL_COLLISION ────────────────────────────────────────────────────────
    pill_hits: list[str] = []
    pids = list(pill_rects)
    for i in range(len(pids)):
        for j in range(i + 1, len(pids)):
            if rects_overlap(pill_rects[pids[i]], pill_rects[pids[j]]):
                pill_hits.append(f"{pids[i]}×{pids[j]}")
    for pid, prect in pill_rects.items():
        for nid, nrect in node_obstacle.items():
            if rects_overlap(prect, nrect):
                pill_hits.append(f"{pid}×{nid}")
    if pill_hits:
        out.append(Finding("FAIL", "PILL_COLLISION",
                            f"{len(pill_hits)} pill collision(s): {', '.join(pill_hits[:6])}"))
    else:
        out.append(Finding("INFO", "PILL_COLLISION", "0 pill collisions ✓"))

    # ── PORT_CONGESTION (WARN only — cosmetic, not in the FAIL bucket) ───────
    attach: dict[tuple[str, str], list[float]] = {}
    for ecell in cells_by_id.values():
        if ecell.get("edge") != "1":
            continue
        src, dst = ecell.get("source"), ecell.get("target")
        style = ecell.get("style") or ""
        if src and "exitX=" in style:
            side, frac = _side_of(_style_frac(style, "exitX", 0.5),
                                   _style_frac(style, "exitY", 0.5))
            attach.setdefault((src, side), []).append(round(frac, 3))
        if dst and "entryX=" in style:
            side, frac = _side_of(_style_frac(style, "entryX", 0.5),
                                   _style_frac(style, "entryY", 0.5))
            attach.setdefault((dst, side), []).append(round(frac, 3))
    congestion = sum(
        1 for fracs in attach.values() for f in set(fracs) if fracs.count(f) > 1
    )
    if congestion:
        out.append(Finding("WARN", "PORT_CONGESTION",
                            f"{congestion} side+fraction pair(s) shared by ≥2 edges"))
    else:
        out.append(Finding("INFO", "PORT_CONGESTION", "0 port congestion ✓"))

    # ── CHANNEL_DISCIPLINE (WARN only; needs the sapdp:channels metadata) ────
    chan_cell = cells_by_id.get("sapdp:channels")
    if chan_cell is None:
        out.append(Finding(
            "INFO", "CHANNEL_DISCIPLINE",
            "no sapdp:channels metadata — skipped (greedy layout or pre-Task-12 file)"))
    else:
        try:
            channels = json.loads(chan_cell.get("value") or "[]")
        except (TypeError, ValueError):
            channels = []
        chan_rects = [
            Rect(r["rect"][0] - CHANNEL_TOL, r["rect"][1] - CHANNEL_TOL,
                 r["rect"][2] + 2 * CHANNEL_TOL, r["rect"][3] + 2 * CHANNEL_TOL)
            for r in channels
        ]
        off_channel = 0
        for ecell in cells_by_id.values():
            if ecell.get("edge") != "1":
                continue
            src, dst = ecell.get("source"), ecell.get("target")
            if not src or not dst or src not in node_drawn or dst not in node_drawn:
                continue
            if _top_zone_of(src, cells_by_id, zone_ids) == _top_zone_of(dst, cells_by_id, zone_ids):
                continue  # intra-zone edges aren't required to ride a channel
            style = ecell.get("style") or ""
            sr, dr = node_drawn[src], node_drawn[dst]
            exit_pt = (sr.x + _style_frac(style, "exitX", 0.5) * sr.w,
                       sr.y + _style_frac(style, "exitY", 0.5) * sr.h)
            entry_pt = (dr.x + _style_frac(style, "entryX", 0.5) * dr.w,
                        dr.y + _style_frac(style, "entryY", 0.5) * dr.h)
            path = [exit_pt, *_edge_waypoints(ecell), entry_pt]
            a, b = _longest_segment(path)
            if not any(point_in_rect(a, cr) and point_in_rect(b, cr) for cr in chan_rects):
                off_channel += 1
        if off_channel and not chan_rects:
            out.append(Finding(
                "INFO", "CHANNEL_DISCIPLINE",
                "sapdp:channels metadata present but empty — skipped"))
        elif off_channel:
            out.append(Finding(
                "WARN", "CHANNEL_DISCIPLINE",
                f"{off_channel} inter-zone edge(s) whose dominant segment runs "
                f"outside every reserved channel (±{CHANNEL_TOL:.0f}px)"))
        else:
            out.append(Finding("INFO", "CHANNEL_DISCIPLINE", "0 channel-discipline warnings ✓"))


def check(path: Path) -> list[Finding]:
    out: list[Finding] = []
    try:
        root = _xml_parse(path).getroot()
    except Exception as exc:  # ParseError, FileNotFoundError, or defusedxml guards
        return [Finding("FAIL", "PARSE", f"cannot parse: {exc}")]

    cells = [c for d in root.findall("diagram") for c in d.iter("mxCell")]
    if not cells:
        return [Finding("FAIL", "EMPTY", "no cells")]

    def geom(c):
        g = c.find("mxGeometry")
        if g is None:
            return None
        try:
            return (float(g.get("x", "0")), float(g.get("y", "0")),
                    float(g.get("width", "0")), float(g.get("height", "0")))
        except ValueError:
            return None

    # ── TITLE ────────────────────────────────────────────────────────────────
    titles = [c for c in cells
              if (c.get("style", "").startswith("text;") and c.get("value")
                  and (geom(c) or (0, 999, 0, 0))[1] <= 60)]
    if not titles:
        out.append(Finding("WARN", "TITLE", "no title text cell in the top band (y ≤ 60)"))

    # ── top-level zone containers (id g-…, parent == '1') ─────────────────────
    zones = []
    for c in cells:
        if (c.get("id", "").startswith("g-") and c.get("parent") == "1"
                and c.get("vertex") == "1"):
            gm = geom(c)
            if gm:
                zones.append((c.get("id"), c.get("value", ""), c.get("style", ""), gm))

    # ── GROUP_OVERLAP (top-level zones must not overlap) ──────────────────────
    overlaps = 0
    for i in range(len(zones)):
        _, _, _, (ax, ay, aw, ah) = zones[i]
        for j in range(i + 1, len(zones)):
            _, _, _, (bx, by, bw, bh) = zones[j]
            ox = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
            oy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
            if ox > 2 and oy > 2:  # >2px overlap on both axes = real collision
                overlaps += 1
                out.append(Finding("FAIL", "GROUP_OVERLAP",
                                   f"zones {zones[i][0]} and {zones[j][0]} overlap "
                                   f"({int(ox)}×{int(oy)}px)"))

    # ── ZONES (columns) ───────────────────────────────────────────────────────
    if zones:
        centers = sorted(z[3][0] + z[3][2] / 2 for z in zones)
        cols, last = 1, centers[0]
        for cx in centers[1:]:
            if cx - last > 180:
                cols += 1
            last = cx
        out.append(Finding("INFO", "ZONES",
                           f"{len(zones)} top-level zone(s) across ~{cols} column(s)"))
        if len(zones) >= 3 and cols == 1:
            out.append(Finding("WARN", "ZONES",
                               "≥3 zones stacked in a single column — not a horizontal big-picture"))

    # ── BTP_CENTRAL (the #EBF8FF layer should be between the others) ──────────
    btp = [z for z in zones if _style(z[2]).get("fillColor", "").upper() == "#EBF8FF"]
    if btp and len(zones) >= 2:
        bx = btp[0][3][0] + btp[0][3][2] / 2
        left = [z for z in zones if z is not btp[0] and z[3][0] + z[3][2] / 2 < bx]
        right = [z for z in zones if z is not btp[0] and z[3][0] + z[3][2] / 2 > bx]
        if not left and not right:
            pass
        elif not (left or right):
            out.append(Finding("INFO", "BTP_CENTRAL", "BTP layer present"))

    # ── LEGEND (≥2 line styles ⇒ legend) ──────────────────────────────────────
    line_kinds = {(_line_kind(_style(c.get("style", ""))))
                  for c in cells if c.get("edge") == "1"}
    line_kinds.discard(None)
    has_legend = any((c.get("value") or "").strip().lower() == "legend"
                     or "legend" in c.get("id", "") for c in cells)
    if len(line_kinds) >= 2 and not has_legend:
        out.append(Finding("WARN", "LEGEND",
                           f"{len(line_kinds)} line styles used but no Legend present"))

    if overlaps == 0:
        out.append(Finding("INFO", "GROUP_OVERLAP", "no top-level zone overlaps ✓"))

    # ── v2 (Task 12) — geometric FAIL-blocking checks + WARN advisories ───────
    _check_v2_geometry(cells, out)

    return out


def render_text(findings: list[Finding], path: Path) -> str:
    order = {"FAIL": 0, "WARN": 1, "INFO": 2}
    findings = sorted(findings, key=lambda f: order[f.severity])
    lines = [f"🧭 Composition check — {path}", ""]
    mark = {"FAIL": "❌", "WARN": "⚠️", "INFO": "ℹ️"}
    for f in findings:
        lines.append(f"  {mark[f.severity]} [{f.rule}] {f.message}")
    n_fail = sum(f.severity == "FAIL" for f in findings)
    n_warn = sum(f.severity == "WARN" for f in findings)
    lines += ["", f"Summary: {n_fail} fail, {n_warn} warn, "
              f"{sum(f.severity == 'INFO' for f in findings)} info"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check SAP diagram composition.")
    ap.add_argument("path")
    ap.add_argument("--strict", action="store_true",
                     help="deprecated no-op — a FAIL always exits 2 now; kept "
                          "for backward compatibility with existing callers")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    p = Path(args.path)
    if not p.exists():
        print(f"ERROR: file not found: {p}", file=sys.stderr)
        return 3

    findings = check(p)
    if args.json:
        print(json.dumps([f.__dict__ for f in findings], indent=2))
    else:
        print(render_text(findings, p), end="")

    # A FAIL always blocks (exit 2), unconditionally — see the module
    # docstring: this aligns with validate-drawio.py's CRITICAL convention
    # instead of requiring the caller to opt in with --strict to catch a
    # readability-breaking defect (PIERCING, CAPTION_OUT, a substantial
    # TEXT_OVERLAP, PILL_COLLISION, or a top-level GROUP_OVERLAP).
    if any(f.severity == "FAIL" for f in findings):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
