# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for scripts/_geom_checks.py — the pure geometry predicate
kernel shared by the channel router (Task 8) and the geometric gate
(Task 12).

Correctness on degenerate cases (touching edges, collinear segments, shared
endpoints, corner grazes) is the entire point of this module, so this file
deliberately over-tests boundary conditions rather than happy-path shapes.
"""
import pytest
from conftest import load_script

gc = load_script("_geom_checks")
Rect = gc.Rect
rects_overlap = gc.rects_overlap
point_in_rect = gc.point_in_rect
seg_intersects_rect = gc.seg_intersects_rect
segments_cross = gc.segments_cross


# ── Rect ─────────────────────────────────────────────────────────────────

def test_rect_convenience_properties():
    r = Rect(10, 20, 30, 40)
    assert r.right == 40
    assert r.bottom == 60
    assert r.cx == 25
    assert r.cy == 40


def test_rect_is_frozen():
    r = Rect(0, 0, 10, 10)
    with pytest.raises(Exception):
        r.x = 5  # dataclasses.FrozenInstanceError (an AttributeError subclass)


def test_rect_convenience_methods_delegate_to_module_functions():
    a, b = Rect(0, 0, 10, 10), Rect(5, 5, 10, 10)
    assert a.intersects(b) == rects_overlap(a, b)
    assert a.contains_point((1, 1)) == point_in_rect((1, 1), a)


# ── rects_overlap ────────────────────────────────────────────────────────

def test_rects_overlap_disjoint():
    a, b = Rect(0, 0, 10, 10), Rect(100, 100, 10, 10)
    assert rects_overlap(a, b) is False


def test_rects_overlap_touching_edges_is_not_overlap():
    # share the vertical line x=10 exactly: 0px penetration on the x axis
    a, b = Rect(0, 0, 10, 10), Rect(10, 0, 10, 10)
    assert rects_overlap(a, b) is False
    # share the horizontal line y=10 exactly: 0px penetration on the y axis
    c, d = Rect(0, 0, 10, 10), Rect(0, 10, 10, 10)
    assert rects_overlap(c, d) is False


def test_rects_overlap_one_axis_only_is_not_overlap():
    # x-ranges overlap but y-ranges are disjoint: AABB overlap needs BOTH axes
    a, b = Rect(0, 0, 10, 10), Rect(5, 20, 10, 10)
    assert rects_overlap(a, b) is False


def test_rects_overlap_real_overlap():
    a, b = Rect(0, 0, 10, 10), Rect(5, 5, 10, 10)
    assert rects_overlap(a, b) is True


def test_rects_overlap_nested():
    outer, inner = Rect(0, 0, 20, 20), Rect(5, 5, 5, 5)
    assert rects_overlap(outer, inner) is True
    assert rects_overlap(inner, outer) is True  # symmetric


def test_rects_overlap_identical():
    a = Rect(0, 0, 10, 10)
    assert rects_overlap(a, a) is True


@pytest.mark.parametrize("bx,pad,expected", [
    (8.5, 2.0, False),  # 1.5px penetration < pad=2 -> graze BELOW threshold
    (8.0, 2.0, False),  # exactly 2px penetration == pad=2 -> "more than" fails
    (8.0, 1.9, True),   # same 2px penetration clears a slightly smaller pad
    (7.0, 2.0, True),   # 3px penetration > pad=2 -> graze ABOVE threshold
])
def test_rects_overlap_pad_threshold(bx, pad, expected):
    a, b = Rect(0, 0, 10, 10), Rect(bx, 0, 10, 10)
    assert rects_overlap(a, b, pad=pad) is expected


# ── point_in_rect ────────────────────────────────────────────────────────

def test_point_in_rect_strictly_inside():
    assert point_in_rect((5, 5), Rect(0, 0, 10, 10)) is True


def test_point_in_rect_outside():
    assert point_in_rect((15, 5), Rect(0, 0, 10, 10)) is False


@pytest.mark.parametrize("p", [
    (0, 5), (10, 5),   # left / right edge midpoints
    (5, 0), (5, 10),   # top / bottom edge midpoints
    (0, 0), (10, 10),  # corners
])
def test_point_in_rect_boundary_is_inclusive(p):
    # documented convention: a point exactly on the edge counts as inside
    assert point_in_rect(p, Rect(0, 0, 10, 10)) is True


def test_point_in_rect_pad_grows_rect():
    r = Rect(0, 0, 10, 10)
    assert point_in_rect((-2, 5), r) is False
    assert point_in_rect((-2, 5), r, pad=3) is True


def test_point_in_rect_negative_pad_shrinks_rect():
    r = Rect(0, 0, 10, 10)
    assert point_in_rect((0, 5), r) is True          # boundary point, pad=0
    assert point_in_rect((0, 5), r, pad=-1) is False  # shrunk rect excludes it


# ── seg_intersects_rect ──────────────────────────────────────────────────

RECT = Rect(0, 0, 10, 10)


def test_seg_intersects_rect_fully_outside():
    assert seg_intersects_rect((20, 20), (30, 30), RECT) is False


def test_seg_intersects_rect_one_endpoint_inside():
    assert seg_intersects_rect((5, 5), (20, 20), RECT) is True


def test_seg_intersects_rect_both_endpoints_inside():
    assert seg_intersects_rect((2, 2), (8, 8), RECT) is True


def test_seg_intersects_rect_passes_through():
    # both endpoints outside; the segment slices straight through the interior
    assert seg_intersects_rect((-5, 5), (15, 5), RECT) is True


def test_seg_intersects_rect_grazes_a_corner():
    # midpoint of (5,15)-(15,5) is exactly (10,10) -- the rect's corner --
    # and both endpoints are strictly outside the rect
    assert seg_intersects_rect((5, 15), (15, 5), RECT) is True


def test_seg_intersects_rect_parallel_outside_no_touch():
    assert seg_intersects_rect((-5, -5), (-5, 20), RECT) is False


def test_seg_intersects_rect_collinear_with_edge_overlapping():
    # lies exactly on the top edge's line (y=0), overlapping its x-range
    assert seg_intersects_rect((-5, 0), (5, 0), RECT) is True


def test_seg_intersects_rect_zero_length_segment():
    assert seg_intersects_rect((5, 5), (5, 5), RECT) is True      # point inside
    assert seg_intersects_rect((50, 50), (50, 50), RECT) is False  # point outside


# ── segments_cross ───────────────────────────────────────────────────────

def test_segments_cross_proper_x():
    assert segments_cross((0, 0), (10, 10), (0, 10), (10, 0)) is True


def test_segments_cross_parallel_non_touching():
    assert segments_cross((0, 0), (10, 0), (0, 5), (10, 5)) is False


def test_segments_cross_collinear_overlapping_is_not_a_cross():
    # same line (y=0), overlapping x-ranges [0,10] and [5,15]: an overlap,
    # not a transversal crossing
    assert segments_cross((0, 0), (10, 0), (5, 0), (15, 0)) is False


def test_segments_cross_collinear_disjoint():
    assert segments_cross((0, 0), (1, 0), (5, 0), (10, 0)) is False


def test_segments_cross_shared_endpoint_is_not_a_cross():
    # convention: touching at a shared endpoint is NOT a proper crossing
    assert segments_cross((0, 0), (10, 0), (10, 0), (10, 10)) is False


def test_segments_cross_t_junction_touch_is_not_a_cross():
    # p2 lands exactly on segment1's interior (a "T"), without passing through
    assert segments_cross((0, 0), (10, 0), (5, 0), (5, 5)) is False


def test_segments_cross_zero_length_segment_never_crosses():
    # a degenerate point-segment has no direction, so it cannot "properly"
    # cross anything, even if it sits exactly on the other segment
    assert segments_cross((5, 0), (5, 0), (0, 0), (10, 0)) is False


def test_segments_cross_bounding_boxes_disjoint():
    assert segments_cross((0, 0), (1, 1), (100, 100), (101, 101)) is False


def test_segments_cross_near_miss_finite_segments_do_not_reach():
    # the infinite lines would cross at (5,5); neither actual segment reaches it
    assert segments_cross((0, 0), (2, 2), (5, 0), (5, 3)) is False
