# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""tests/test_pure_render.py — Task 10 (pure-Python PNG renderer) tests.

Three families:
  * Geometric-fidelity tests against ``tests/fixtures/render-sample.drawio``,
    a synthetic fixture exercising every primitive _pure_render.py supports
    (T5's molecule output doesn't exist in this worktree, so this fixture is
    hand-built and its one real-icon cell's sha1 is verified against the
    COMMITTED assets/icon-atlas/index.json at fixture-build time).
  * The load-bearing sha1 cross-check against the real generate-drawio.py
    (mirrors tests/test_icon_atlas.py's identical guard for
    build-icon-atlas.py) plus build-icon-atlas.py itself, so all three
    independent copies of the same normalization stay byte-identical.
  * Pure-function unit tests for the smaller helpers (no rendering needed).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageStat

from conftest import load_script

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "render-sample.drawio"
MINI_EXEMPLAR = ROOT / "tests" / "fixtures" / "mini-exemplar.drawio"
ATLAS_DIR = ROOT / "assets" / "icon-atlas"
ATLAS_INDEX_PATH = ATLAS_DIR / "index.json"

pr = load_script("_pure_render")


# ─────────────────────────────────────────────────────────────────────────
# pixel-region helpers
# ─────────────────────────────────────────────────────────────────────────
def _region_color_hits(img: Image.Image, box: tuple[float, float, float, float],
                        target: tuple[int, int, int], tol: int = 20) -> int:
    """Count pixels in ``box`` within ``tol`` per channel of ``target``."""
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    px = img.load()
    hits = 0
    for y in range(max(0, y0), min(img.height, y1)):
        for x in range(max(0, x0), min(img.width, x1)):
            p = px[x, y]
            if all(abs(p[i] - target[i]) <= tol for i in range(3)):
                hits += 1
    return hits


def _region_has_color(img: Image.Image, box: tuple[float, float, float, float],
                       target: tuple[int, int, int], tol: int = 20) -> bool:
    return _region_color_hits(img, box, target, tol) > 0


def _region_has_extra_color(img: Image.Image, box: tuple[float, float, float, float],
                             exclude: list[tuple[int, int, int]], tol: int = 15) -> bool:
    """True if some pixel in ``box`` matches NONE of ``exclude`` within tol
    (used to prove a rendered icon isn't secretly just background+placeholder)."""
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    px = img.load()
    for y in range(max(0, y0), min(img.height, y1)):
        for x in range(max(0, x0), min(img.width, x1)):
            p = px[x, y]
            if all(any(abs(p[i] - c[i]) > tol for i in range(3)) for c in exclude):
                return True
    return False


# ─────────────────────────────────────────────────────────────────────────
# canvas sizing
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("scale", [1.0, 2.0, 3.0])
def test_canvas_size_is_page_size_times_scale(tmp_path, scale):
    out = tmp_path / "out.png"
    rc = pr.main([str(FIXTURE), "--out", str(out), "--scale", str(scale)])
    assert rc == 0
    with Image.open(out) as img:
        assert img.size == (round(900 * scale), round(500 * scale))


# ─────────────────────────────────────────────────────────────────────────
# rounded rect: fill + strokeColor
# ─────────────────────────────────────────────────────────────────────────
def test_rounded_rect_border_matches_stroke_color(tmp_path):
    out = tmp_path / "out.png"
    assert pr.main([str(FIXTURE), "--out", str(out), "--scale", "2"]) == 0
    with Image.open(out) as img:
        # rect1: x=40,y=40,w=160,h=80, arcSize=16 (absolute) -> flat top edge
        # spans unscaled x in [56,184]; sample well clear of the rounded
        # corners, a few px into the (strokeWidth=1.5 * scale=2 ~= 3px) border.
        assert _region_has_color(img, (150 * 2, 78, 250 * 2, 90), (0x00, 0x70, 0xF2))
        # interior fill (#EBF8FF) is also present, away from any border.
        assert _region_has_color(img, (140 * 2, 70 * 2, 150 * 2, 80 * 2), (0xEB, 0xF8, 0xFF))


# ─────────────────────────────────────────────────────────────────────────
# text: fontColor / fontStyle / align
# ─────────────────────────────────────────────────────────────────────────
def test_text_cell_renders_in_fontcolor(tmp_path):
    out = tmp_path / "out.png"
    assert pr.main([str(FIXTURE), "--out", str(out), "--scale", "2"]) == 0
    with Image.open(out) as img:
        # text1: x=240,y=40,w=220,h=40, fontColor=#0070F2 -- scan the whole
        # label box (font metrics vary by fallback vs truetype, see
        # load_font()) for at least one ink pixel matching fontColor.
        assert _region_has_color(img, (240 * 2, 40 * 2, 460 * 2, 80 * 2), (0x00, 0x70, 0xF2))


# ─────────────────────────────────────────────────────────────────────────
# pill (rounded rect, arcSize=50) + step-circle (ellipse)
# ─────────────────────────────────────────────────────────────────────────
def test_pill_renders_as_filled_stadium(tmp_path):
    out = tmp_path / "out.png"
    assert pr.main([str(FIXTURE), "--out", str(out), "--scale", "2"]) == 0
    with Image.open(out) as img:
        # pill1: x=40,y=260,w=90,h=26, fillColor=#0070F2 -- sample its
        # (flat, non-arc) interior band.
        assert _region_has_color(img, (100 * 2, 265 * 2, 120 * 2, 275 * 2), (0x00, 0x70, 0xF2))


def test_step_circle_ellipse_renders_fill_color(tmp_path):
    out = tmp_path / "out.png"
    assert pr.main([str(FIXTURE), "--out", str(out), "--scale", "2"]) == 0
    with Image.open(out) as img:
        # step1: x=40,y=320,w=30,h=30 ellipse, fillColor=#5B738B.
        assert _region_has_color(img, (45 * 2, 325 * 2, 65 * 2, 345 * 2), (0x5B, 0x73, 0x8B))


# ─────────────────────────────────────────────────────────────────────────
# images: real atlas hit vs bogus-URI placeholder
# ─────────────────────────────────────────────────────────────────────────
def test_real_icon_atlas_hit_paints_the_actual_atlas_png(tmp_path):
    """Proves the sha1 lookup actually HITS: the rendered region isn't just
    "non-empty" (stddev>0, the literal spec ask) -- it is pixel-identical
    (composited onto white) to the real committed atlas PNG, not the grey
    placeholder."""
    out = tmp_path / "out.png"
    assert pr.main([str(FIXTURE), "--out", str(out), "--scale", "2"]) == 0

    style = json.loads((ROOT / "assets/shape-index.json").read_text(encoding="utf-8"))
    entry = next(s for s in style["services"]
                 if s["name"] == "SAP Digital Assistant Service" and s["size"] == "S")
    uri = pr.extract_image_value(entry["drawioStyle"])
    digest = pr.sha1_of(uri)
    atlas_index = json.loads(ATLAS_INDEX_PATH.read_text(encoding="utf-8"))
    rel = atlas_index["by_sha1"][digest]

    with Image.open(out) as img:
        # icon1: x=40,y=160,w=48,h=48 -> at scale=2, a 96x96 px box: exactly
        # the atlas's own native size (see build-icon-atlas.py), so no
        # resize occurs and the paste should be pixel-identical.
        box = (40 * 2, 160 * 2, 88 * 2, 208 * 2)
        crop = img.crop(box)

        # 1) literal spec ask: the region is not empty/flat.
        assert any(s > 0 for s in ImageStat.Stat(crop).stddev)
        # 2) stronger: it's not merely "some other flat color" (e.g. the
        #    grey placeholder) -- some pixel is neither background white
        #    nor placeholder grey.
        assert _region_has_extra_color(img, box, [(255, 255, 255), pr.PLACEHOLDER_RGB])
        # 3) strongest: pixel-identical to the real atlas PNG composited on white.
        with Image.open(ATLAS_DIR / rel) as expected_icon:
            expected_icon = expected_icon.convert("RGBA")
            assert expected_icon.size == (crop.width, crop.height)
            expected_rgb = Image.new("RGB", expected_icon.size, (255, 255, 255))
            expected_rgb.paste(expected_icon, (0, 0), expected_icon)
            diff = ImageChops.difference(crop, expected_rgb)
            assert diff.getbbox() is None, "rendered icon region does not match the atlas PNG exactly"


def test_bogus_image_uri_falls_back_to_placeholder_with_warn_and_exit_0(tmp_path, capsys):
    out = tmp_path / "out.png"
    rc = pr.main([str(FIXTURE), "--out", str(out), "--scale", "2"])
    stderr = capsys.readouterr().err

    assert rc == 0
    assert "WARNING" in stderr
    assert "icon2" in stderr

    with Image.open(out) as img:
        # icon2: x=140,y=160,w=48,h=48 -> placeholder circle's center.
        assert img.getpixel((164 * 2, 184 * 2)) == pr.PLACEHOLDER_RGB


# ─────────────────────────────────────────────────────────────────────────
# dashed edges
# ─────────────────────────────────────────────────────────────────────────
def test_dashed_edge_shows_alternating_stroke_and_background(tmp_path):
    out = tmp_path / "out.png"
    assert pr.main([str(FIXTURE), "--out", str(out), "--scale", "2"]) == 0
    with Image.open(out) as img:
        # edge1 runs horizontally from src1's right-center (440,415 unscaled)
        # to tgt1's left-center (650,415) -- scaled y=830.
        row = [img.getpixel((x, 830)) for x in range(900, 1280)]
        stroke_hits = sum(1 for p in row if all(abs(p[i] - (0x47, 0x5E, 0x75)[i]) <= 20 for i in range(3)))
        bg_hits = sum(1 for p in row if all(abs(p[i] - 255) <= 5 for i in range(3)))
        assert stroke_hits > 0, "no dash-colored pixels found on the edge row"
        assert bg_hits > 0, "no background gaps found -- line isn't actually dashed"


def test_solid_edge_has_no_background_gaps():
    """Negative control for the dashed test: a solid (non-dashed) polyline
    must NOT show background gaps along its own row."""
    from PIL import ImageDraw

    img = Image.new("RGB", (100, 20), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    pr.draw_polyline(draw, [(5, 10), (95, 10)], (0, 0, 0), 3, None)
    row = [img.getpixel((x, 10)) for x in range(10, 90)]
    assert all(p == (0, 0, 0) for p in row)


# ─────────────────────────────────────────────────────────────────────────
# determinism
# ─────────────────────────────────────────────────────────────────────────
def test_same_input_renders_byte_identical_png(tmp_path):
    out1, out2 = tmp_path / "a.png", tmp_path / "b.png"
    assert pr.main([str(FIXTURE), "--out", str(out1), "--scale", "2"]) == 0
    assert pr.main([str(FIXTURE), "--out", str(out2), "--scale", "2"]) == 0
    assert out1.read_bytes() == out2.read_bytes()


# ─────────────────────────────────────────────────────────────────────────
# object-wrapped cells + real-world smoke test
# ─────────────────────────────────────────────────────────────────────────
def test_object_wrapped_cells_render_without_crashing(tmp_path):
    out = tmp_path / "out.png"
    rc = pr.main([str(MINI_EXEMPLAR), "--out", str(out)])
    assert rc == 0
    with Image.open(out) as img:
        assert img.size[0] > 0 and img.size[1] > 0


def test_negative_geometry_does_not_crash(tmp_path):
    """Malformed/adversarial input (a negative width) must never traceback:
    PIL's rounded_rectangle rejects an "inverted" box outright, so geometry
    parsing clamps width/height to >= 0 defensively."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<mxfile><diagram name="p1"><mxGraphModel pageWidth="200" pageHeight="200"><root>
<mxCell id="0" /><mxCell id="1" parent="0" />
<mxCell id="bad" value="neg" style="rounded=1;absoluteArcSize=1;arcSize=8;strokeColor=#0070F2;fillColor=#EBF8FF;" vertex="1" parent="1">
<mxGeometry x="10" y="10" width="-40" height="-20" as="geometry" />
</mxCell>
</root></mxGraphModel></diagram></mxfile>"""
    src = tmp_path / "bad.drawio"
    src.write_text(xml, encoding="utf-8")
    out = tmp_path / "out.png"
    rc = pr.main([str(src), "--out", str(out)])
    assert rc == 0
    assert out.exists()


def test_smoke_render_nova_l1(tmp_path):
    nova_path = ROOT / "demo" / "nova" / "nova-L1.drawio"
    if not nova_path.exists():
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "generate-drawio.py"),
             str(ROOT / "demo" / "nova" / "nova-L1.json"), "--out", str(tmp_path / "n.drawio")],
            check=True, cwd=ROOT,
        )
        nova_path = tmp_path / "n.drawio"

    out = tmp_path / "nova-l1.png"
    rc = pr.main([str(nova_path), "--out", str(out)])
    assert rc == 0
    assert out.exists() and out.stat().st_size > 0
    with Image.open(out) as img:
        assert img.size[0] > 0 and img.size[1] > 0


# ─────────────────────────────────────────────────────────────────────────
# The load-bearing sha1 contract: THIS module's normalization must be
# byte-identical to generate-drawio.py's _safe_img/_extract_image_uri (and
# to build-icon-atlas.py's own copy) -- mirrors
# tests/test_icon_atlas.py::test_sha1_normalization_matches_the_real_emitter.
# ─────────────────────────────────────────────────────────────────────────
def test_sha1_normalization_matches_the_real_emitter():
    gen = load_script("generate-drawio")
    bia = load_script("build-icon-atlas")
    marker_style = "shape=image;image=data:image/svg+xml;base64,QUJDRA==;fontSize=10"
    comma_style = "shape=image;image=data:image/svg+xml,QUJDRA==;fontSize=10"
    for style in (marker_style, comma_style):
        emitter_uri = gen._extract_image_uri(style)
        render_uri = pr.extract_image_value(style)
        atlas_uri = bia.extract_image_value(style)
        assert emitter_uri == render_uri == atlas_uri == "data:image/svg+xml,QUJDRA=="
        assert (
            pr.sha1_of(render_uri)
            == bia.sha1_of(atlas_uri)
            == hashlib.sha1(emitter_uri.encode("utf-8")).hexdigest()
        )


def test_fixture_real_icon_sha1_is_actually_in_the_committed_atlas():
    """Belt-and-suspenders: the fixture-generation check re-run as a test,
    so a future atlas rebuild that drops this icon fails loudly here too."""
    style = json.loads((ROOT / "assets/shape-index.json").read_text(encoding="utf-8"))
    entry = next(s for s in style["services"]
                 if s["name"] == "SAP Digital Assistant Service" and s["size"] == "S")
    uri = pr.extract_image_value(entry["drawioStyle"])
    digest = pr.sha1_of(uri)
    atlas_index = json.loads(ATLAS_INDEX_PATH.read_text(encoding="utf-8"))
    assert digest in atlas_index["by_sha1"]


# ─────────────────────────────────────────────────────────────────────────
# Pillow guard
# ─────────────────────────────────────────────────────────────────────────
def test_pillow_guard_exits_3_when_pillow_unavailable(monkeypatch):
    """Simulates a Pillow-less environment via the standard
    sys.modules[name]=None ImportError sentinel, without touching the real
    Pillow install. Cleans up the half-initialized module afterward so later
    tests still get the real, fully-loaded _pure_render."""
    monkeypatch.setitem(sys.modules, "PIL", None)
    sys.modules.pop("_pure_render", None)
    try:
        with pytest.raises(SystemExit) as exc_info:
            load_script("_pure_render")
        assert exc_info.value.code == 3
    finally:
        sys.modules.pop("_pure_render", None)


# ─────────────────────────────────────────────────────────────────────────
# pure-function unit tests
# ─────────────────────────────────────────────────────────────────────────
def test_parse_style_handles_bare_flags_and_key_values():
    style = pr.parse_style("ellipse;whiteSpace=wrap;html=1;fontColor=#FFFFFF")
    assert style["ellipse"] == "1"
    assert style["whiteSpace"] == "wrap"
    assert style["fontColor"] == "#FFFFFF"


def test_corner_radius_absolute_vs_percentage():
    absolute = pr.parse_style("rounded=1;absoluteArcSize=1;arcSize=16")
    assert pr.corner_radius(absolute, 202, 70) == 16.0

    pill = pr.parse_style("rounded=1;arcSize=50")
    assert pr.corner_radius(pill, 57, 16) == 8.0  # min(w,h)/2 -- a true stadium

    not_rounded = pr.parse_style("rounded=0;arcSize=50")
    assert pr.corner_radius(not_rounded, 57, 16) == 0.0

    clamped = pr.parse_style("rounded=1;absoluteArcSize=1;arcSize=999")
    assert pr.corner_radius(clamped, 40, 20) == 10.0  # clamped to min(w,h)/2


def test_dash_spec_distinguishes_dashed_from_dotted():
    dashed = pr.parse_style("dashed=1;dashPattern=8 4")
    dotted = pr.parse_style("dashed=1;dashPattern=1 4")
    solid = pr.parse_style("dashed=0")
    no_pattern = pr.parse_style("dashed=1")

    assert pr.dash_spec(dashed, 1.0) == (6.0, 4.0)
    assert pr.dash_spec(dotted, 1.0) == (2.0, 4.0)
    assert pr.dash_spec(solid, 1.0) is None
    assert pr.dash_spec(no_pattern, 1.0) == (6.0, 4.0)

    scaled = pr.dash_spec(dashed, 2.0)
    assert scaled == (12.0, 8.0)


def test_parse_color_hex_and_sentinels():
    assert pr.parse_color("#0070F2") == (0x00, 0x70, 0xF2)
    assert pr.parse_color("#FFF") == (255, 255, 255)
    assert pr.parse_color("none") is None
    assert pr.parse_color("default") is None
    assert pr.parse_color(None) is None


def test_strip_label_html_converts_br_and_strips_tags():
    raw = '<p style="line-height: 100%;"><b><font color="#ffffff">1</font></b></p>'
    assert pr.strip_label_html(raw) == "1"
    assert pr.strip_label_html("line1<br>line2<br/>line3") == "line1\nline2\nline3"
    assert pr.strip_label_html("") == ""
    assert pr.strip_label_html(None) == ""


def test_point_at_fraction_midpoint_and_endpoints():
    path = [(0.0, 0.0), (10.0, 0.0)]
    assert pr.point_at_fraction(path, 0.0) == (5.0, 0.0)
    assert pr.point_at_fraction(path, -1.0) == (0.0, 0.0)
    assert pr.point_at_fraction(path, 1.0) == (10.0, 0.0)


def test_apply_opacity_scales_alpha_channel():
    img = Image.new("RGBA", (4, 4), (10, 20, 30, 255))
    out = pr.apply_opacity(img, 50.0)
    r, g, b, a = out.getpixel((0, 0))
    assert (r, g, b) == (10, 20, 30)
    assert 120 <= a <= 130  # ~50% of 255


def test_extract_image_value_normalizes_semicolon_base64_marker():
    style = "shape=image;image=data:image/png;base64,AAAA;fontSize=10"
    assert pr.extract_image_value(style) == "data:image/png,AAAA"
    assert pr.extract_image_value(None) is None
    assert pr.extract_image_value("rounded=1;fillColor=#FFFFFF") is None
