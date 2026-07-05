#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""Pure geometry predicates shared by the channel router (``_channel_router.py``,
Task 8) and the geometric gate (``check-composition.py`` v2, Task 12).

Axis-aligned rectangle overlap/containment, plus segment/segment and
segment/rectangle intersection tests. Zero dependencies beyond the standard
library — no drawio knowledge, no I/O, no numpy. Coordinates are drawio's
top-left-origin canvas space (x grows right, y grows down); every predicate
here is orientation-agnostic and behaves identically regardless.

Correctness on degenerate cases is the entire point of this module — the
router and the gate both need to trust it at the pixel level. Two
conventions are used consistently across every function below:

1. ``EPS = 1e-9`` is a **floating-point** tolerance: it only protects sign
   and equality comparisons from float accumulation error. It is NOT a
   geometric/pixel tolerance — callers who want "how many px of slack is
   acceptable" should use the ``pad`` parameter on `rects_overlap` /
   `point_in_rect` instead.

2. **Touching is not overlapping, but touching IS intersecting** — these
   are deliberately different rules for different questions:
   - `rects_overlap` and `segments_cross` answer "do these two things
     properly overlap/cross" — a shared edge, shared endpoint, or collinear
     overlap does NOT count (strict: real 2-D penetration / a genuine
     transversal crossing is required). This is what a crossing-count
     budget or a "these two boxes collided" check wants.
   - `point_in_rect` and `seg_intersects_rect` answer "is this point/segment
     touching or inside this *closed* region" — boundaries are INCLUSIVE,
     so a point exactly on an edge, or a segment that only grazes a corner,
     counts as intersecting. This is what a "does this edge cut through
     that box" check wants (better to over-flag a graze than miss it).
"""
from __future__ import annotations

from dataclasses import dataclass

# Floating-point equality/sign tolerance — see module docstring, point 1.
EPS = 1e-9


@dataclass(frozen=True)
class Rect:
    """Axis-aligned rectangle: (x, y) top-left corner + width/height.

    Degenerate rects (w<=0 or h<=0) are legal to construct (e.g. a
    zero-height slab mid-computation) — they simply never satisfy the
    strict "> pad" overlap rule against anything, by construction.
    """
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def contains_point(self, p: tuple[float, float], pad: float = 0.0) -> bool:
        """Convenience for `point_in_rect(p, self, pad)` — see that function
        for the inclusive-boundary convention."""
        return point_in_rect(p, self, pad)

    def intersects(self, other: "Rect", pad: float = 0.0) -> bool:
        """Convenience for `rects_overlap(self, other, pad)` — see that
        function for the strict "more than pad" convention."""
        return rects_overlap(self, other, pad)


# ── internal: EPS-robust scalar comparisons ─────────────────────────────────
# Every geometric decision funnels through these two so the epsilon is applied
# exactly once, consistently, everywhere.

def _gt(a: float, b: float) -> bool:
    """Robust a > b: True only if a exceeds b by more than EPS."""
    return (a - b) > EPS


def _le(a: float, b: float) -> bool:
    """Robust a <= b: True unless a exceeds b by more than EPS."""
    return (a - b) <= EPS


def rects_overlap(a: Rect, b: Rect, pad: float = 0.0) -> bool:
    """True if a and b overlap by MORE than `pad` on BOTH axes.

    Touching edges (0px penetration) are NOT overlap — this is a strict
    ``>``, not ``>=``. With pad>0, a graze of <=pad px of penetration also
    does not count (e.g. a 2px graze with pad=2 is False). This mirrors the
    historical ``check-composition.py`` GROUP_OVERLAP threshold
    (">2px overlap on both axes = real collision") that this generalizes.
    """
    ox = min(a.right, b.right) - max(a.x, b.x)
    oy = min(a.bottom, b.bottom) - max(a.y, b.y)
    return _gt(ox, pad) and _gt(oy, pad)


def point_in_rect(p: tuple[float, float], r: Rect, pad: float = 0.0) -> bool:
    """True if p lies within r, grown/shrunk by `pad` on every side.

    Boundary convention: INCLUSIVE — a point exactly on the (possibly
    padded) edge counts as inside, i.e. the closed interval
    ``[x - pad, right + pad]`` on each axis. pad<0 shrinks r (require a
    safety margin from the edge); pad>0 grows it (treat "just outside" as
    still inside for a generous containment check).
    """
    px, py = p
    return (_le(r.x - pad, px) and _le(px, r.right + pad)
            and _le(r.y - pad, py) and _le(py, r.bottom + pad))


# ── internal: orientation-based segment machinery ───────────────────────────

def _sign(v: float) -> int:
    """Robust sign of v: values within EPS of zero collapse to exactly 0.
    Used for every "which side of the line" / "is this exactly zero"
    decision below, so float noise never flips a boundary case."""
    if v > EPS:
        return 1
    if v < -EPS:
        return -1
    return 0


def _orient(a: tuple[float, float], b: tuple[float, float],
            c: tuple[float, float]) -> float:
    """2-D cross product of (b-a) x (c-a): >0 if a->b->c turns left (CCW),
    <0 if right (CW), 0 if collinear. Only the sign is meaningful here."""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: tuple[float, float], b: tuple[float, float],
                p: tuple[float, float]) -> bool:
    """True if p — already known to be collinear with line a-b — lies
    within the closed bounding box of segment a-b (i.e. actually between
    a and b, not just somewhere on the infinite line through them)."""
    return (min(a[0], b[0]) - EPS <= p[0] <= max(a[0], b[0]) + EPS
            and min(a[1], b[1]) - EPS <= p[1] <= max(a[1], b[1]) + EPS)


def _orientation_signs(p1: tuple[float, float], q1: tuple[float, float],
                        p2: tuple[float, float], q2: tuple[float, float]
                        ) -> tuple[int, int, int, int]:
    """The four orientation signs the classic segment-intersection test
    needs: where p1 and q1 fall relative to line p2-q2, and vice versa."""
    return (_sign(_orient(p2, q2, p1)), _sign(_orient(p2, q2, q1)),
            _sign(_orient(p1, q1, p2)), _sign(_orient(p1, q1, q2)))


def _touch_or_cross(p1: tuple[float, float], q1: tuple[float, float],
                     p2: tuple[float, float], q2: tuple[float, float]) -> bool:
    """INCLUSIVE segment intersection: True if the closed segments share at
    least one point for ANY reason — a proper crossing, a touching
    endpoint, a T-junction, or collinear overlap all count.

    This is the classical intersection test (orientation signs + on-segment
    fallback for the collinear cases). `seg_intersects_rect` uses this one;
    `segments_cross` below deliberately does NOT — it wants only the strict
    transversal crossing. See the module docstring, point 2.
    """
    s1, s2, s3, s4 = _orientation_signs(p1, q1, p2, q2)
    if s1 * s2 < 0 and s3 * s4 < 0:
        return True
    if s1 == 0 and _on_segment(p2, q2, p1):
        return True
    if s2 == 0 and _on_segment(p2, q2, q1):
        return True
    if s3 == 0 and _on_segment(p1, q1, p2):
        return True
    if s4 == 0 and _on_segment(p1, q1, q2):
        return True
    return False


def seg_intersects_rect(p: tuple[float, float], q: tuple[float, float],
                         r: Rect) -> bool:
    """True if segment p->q intersects the CLOSED rectangle r — its
    interior OR its boundary. Either endpoint lying inside/on r counts
    (covers "passes through", "ends inside", and "entirely inside"); a
    segment that only grazes a corner tangentially also counts, per this
    module's inclusive-boundary convention (point 2 above). A segment
    entirely outside r that never touches it is False.
    """
    if point_in_rect(p, r) or point_in_rect(q, r):
        return True
    corners = ((r.x, r.y), (r.right, r.y), (r.right, r.bottom), (r.x, r.bottom))
    return any(_touch_or_cross(p, q, corners[i], corners[(i + 1) % 4])
               for i in range(4))


def segments_cross(p1: tuple[float, float], q1: tuple[float, float],
                    p2: tuple[float, float], q2: tuple[float, float]) -> bool:
    """True if segment p1-q1 and segment p2-q2 PROPERLY cross: they meet at
    exactly one point that lies strictly in the interior of BOTH segments.

    Convention (deliberately strict — see module docstring, point 2):
    - A shared endpoint (segments meeting at a common vertex) is NOT a
      crossing.
    - A T-junction (one segment's endpoint landing on the other's interior)
      is NOT a crossing.
    - Collinear segments — whether overlapping or disjoint — are NEVER a
      crossing; running along the same line is not a transversal cross.
    - A zero-length segment (p==q) can never cross anything: its direction
      is undefined, so by definition it has no "interior" to cross through.
    Use `seg_intersects_rect` / `point_in_rect` instead if what you need is
    "do these touch at all", including the cases excluded above.
    """
    s1, s2, s3, s4 = _orientation_signs(p1, q1, p2, q2)
    return s1 * s2 < 0 and s3 * s4 < 0
