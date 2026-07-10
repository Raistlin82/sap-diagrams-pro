# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""Regression: edge pills/labels must not be parked on the NETWORK separator bar
or its 'NETWORK' caption (a cross-network pill-on-caption TEXT_OVERLAP the gate
flags). _sep_obstacle_rects contributes both keep-out rects to the pill slotter."""
from conftest import load_script


def _router():
    return load_script("_channel_router")


def test_no_separator_returns_empty():
    R = _router()
    assert R._sep_obstacle_rects(None) == ()
    assert R._sep_obstacle_rects({}) == ()


def test_bar_and_caption_keepout():
    R = _router()
    sep = {"x": 1000.0, "y0": 200.0, "y1": 1300.0}
    rects = R._sep_obstacle_rects(sep)
    assert len(rects) == 2, "expected a bar band + a caption keep-out"
    bar, label = rects
    # bar band is centred on the seam, spans the full separator height
    assert bar.x < 1000.0 < bar.right and bar.y <= 200.0 and bar.bottom >= 1300.0
    # caption band is wider (covers the ~80px 'NETWORK' label) and sits near y1
    assert (label.right - label.x) >= 80.0
    assert label.x < 1000.0 < label.right          # centred on the seam
    assert label.bottom <= 1300.0 and label.y >= 1300.0 - 60.0   # near the bottom


def test_a_pill_centred_on_the_caption_is_rejected_by_the_band():
    """A pill rect sitting on the caption overlaps the caption keep-out, so the
    slot scan (_slot_free) would reject that position."""
    R = _router()
    sep = {"x": 1705.0, "y0": 300.0, "y1": 1325.0}
    _bar, label = R._sep_obstacle_rects(sep)
    # the real regression case: 'zero-copy' pill (74x18) landing on the caption
    pill = R.Rect(1611.0, 1289.0, 74.0, 18.0)
    assert not R._slot_free(pill, [label], [])
