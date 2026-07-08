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
from PIL import Image, ImageChops, ImageDraw, ImageStat

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
# Fonts: bundled Arimo family (FIX-1 -- was DejaVuSans.ttf, unresolvable on
# macOS, silently degrading every render to load_default()'s tiny bitmap
# font: tofu for the em dash used in every diagram title, no bold/italic,
# and cross-environment nondeterminism since it depended on whatever
# TrueType fonts happened to be installed system-wide).
# ─────────────────────────────────────────────────────────────────────────
def test_load_font_resolves_each_bundled_arimo_face_by_absolute_path():
    """(bold, italic) must select the matching bundled face file -- not
    just "some truetype font" -- so fontStyle=1 cells actually render in
    the bold face rather than a synthetic/approximate one."""
    cases = [
        (False, False, "Arimo-Regular.ttf", "Regular"),
        (True, False, "Arimo-Bold.ttf", "Bold"),
        (False, True, "Arimo-Italic.ttf", "Italic"),
        (True, True, "Arimo-BoldItalic.ttf", "Bold Italic"),
    ]
    for bold, italic, filename, style_name in cases:
        font = pr.load_font(24, bold=bold, italic=italic)
        assert isinstance(font, pr.ImageFont.FreeTypeFont)
        assert font.path == str(pr.FONTS_DIR / filename)
        assert font.getname() == ("Arimo", style_name)


def test_bundled_font_files_actually_exist_and_are_valid_truetype():
    for filename in ("Arimo-Regular.ttf", "Arimo-Bold.ttf", "Arimo-Italic.ttf", "Arimo-BoldItalic.ttf"):
        path = pr.FONTS_DIR / filename
        assert path.is_file(), f"missing bundled font {path}"
        # Loadable at all -- a corrupt/truncated commit would raise here.
        pr.ImageFont.truetype(str(path), 12)


def test_em_dash_glyph_is_a_real_mapped_glyph_not_tofu():
    """A TrueType font with no glyph for a codepoint substitutes the SAME
    ".notdef" placeholder box for ANY unmapped codepoint -- so a genuinely
    mapped em-dash glyph (U+2014, used in every diagram title, e.g. "NOVA
    Invoice Suite — L1 Architecture") must render a DIFFERENT ink footprint
    than a deliberately-unmapped Private Use Area codepoint at the same
    size. Previously (DejaVuSans.ttf unresolvable on macOS -> load_default())
    this would have been tofu.
    """
    font = pr.load_font(40)

    def glyph_bbox(ch):
        img = Image.new("L", (150, 150), 0)
        ImageDraw.Draw(img).text((10, 10), ch, font=font, fill=255)
        return img.getbbox()

    bbox_dash = glyph_bbox("—")
    bbox_missing = glyph_bbox("")  # PUA codepoint no ordinary text font maps
    assert bbox_dash is not None
    assert bbox_missing is not None
    assert bbox_dash != bbox_missing


def test_bold_style_renders_visibly_heavier_than_regular():
    """fontStyle=1 must be visually distinguishable, not just "the same
    glyph shape at a fallback bitmap size" (load_default() has no bold)."""
    def ink_pixel_count(font):
        img = Image.new("L", (400, 100), 0)
        ImageDraw.Draw(img).text((10, 10), "SAP BTP Title", font=font, fill=255)
        return sum(img.histogram()[129:])  # count of pixels brighter than 128

    regular_ink = ink_pixel_count(pr.load_font(40))
    bold_ink = ink_pixel_count(pr.load_font(40, bold=True))
    assert bold_ink > regular_ink * 1.1  # comfortably more than noise (~1.44x measured)


def test_load_font_uses_the_bundle_with_no_warnings_by_default(monkeypatch, capsys):
    monkeypatch.setattr(pr, "_FONT_CACHE", {})
    monkeypatch.setattr(pr, "_warned_no_bundled_font", False)
    monkeypatch.setattr(pr, "_warned_no_truetype", False)
    font = pr.load_font(20)
    assert capsys.readouterr().err == ""
    assert isinstance(font, pr.ImageFont.FreeTypeFont)
    assert font.path == str(pr.FONTS_DIR / "Arimo-Regular.ttf")


def test_load_font_falls_back_and_warns_when_bundle_is_missing(monkeypatch, capsys):
    """If assets/fonts/ is somehow missing (corrupt checkout, packaging
    bug), rendering must still succeed -- degrading gracefully instead of
    crashing -- and say so loudly rather than silently drifting."""
    monkeypatch.setattr(pr, "_FONT_CACHE", {})
    monkeypatch.setattr(pr, "_warned_no_bundled_font", False)
    monkeypatch.setattr(pr, "_warned_no_truetype", False)
    monkeypatch.setattr(pr, "FONTS_DIR", Path("/nonexistent/assets/fonts/for-testing"))

    font = pr.load_font(20)
    stderr = capsys.readouterr().err
    assert "WARNING" in stderr
    assert "bundled font" in stderr
    assert font is not None


def test_title_with_em_dash_renders_in_a_real_page_not_just_in_isolation(tmp_path):
    """End-to-end companion to test_em_dash_glyph_is_a_real_mapped_glyph_not_tofu:
    title1's value is "NOVA Invoice Suite — L1 Architecture" (the exact
    kind of cell generate-drawio.py emits for every diagram's title) --
    confirm it paints as part of a real page render, not just in the
    isolated load_font unit test above."""
    out = tmp_path / "out.png"
    assert pr.main([str(FIXTURE), "--out", str(out), "--scale", "2"]) == 0
    with Image.open(out) as img:
        # title1: x=40,y=2,w=500,h=30, fontColor=#1D2D3E.
        assert _region_has_color(img, (40 * 2, 2 * 2, 540 * 2, 32 * 2), (0x1D, 0x2D, 0x3E))


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
        # its caption ("Bogus Icon", fontColor=#1D2D3E) still renders below
        # -- FIX-2(a): caption rendering doesn't depend on the atlas lookup
        # having hit; only the icon graphic itself is a placeholder.
        assert _region_has_color(img, (100 * 2, 206 * 2, 230 * 2, 230 * 2), (0x1D, 0x2D, 0x3E))


def test_image_cell_caption_renders_below_the_icon(tmp_path):
    """FIX-2(a): draw_vertex used to ``return`` right after drawing a
    shape=image cell's icon, before ever looking at cell.value -- so the
    service name (e.g. "SAP Digital Assistant Service") set as the cell's
    value with verticalLabelPosition=bottom/verticalAlign=top was silently
    dropped for every icon in every diagram (46 bare unlabeled icons in
    nova-L1)."""
    out = tmp_path / "out.png"
    assert pr.main([str(FIXTURE), "--out", str(out), "--scale", "2"]) == 0
    with Image.open(out) as img:
        # icon1: x=40,y=160,w=48,h=48, verticalLabelPosition=bottom,
        # verticalAlign=top, fontColor=#556B82 -- caption band sits just
        # below the icon's bottom edge (unscaled y=208).
        assert _region_has_color(img, (0, 206 * 2, 300 * 2, 230 * 2), (0x55, 0x6B, 0x82))


# ─────────────────────────────────────────────────────────────────────────
# FIX-2(b): image= compositing for ANY shape, not just the exact
# shape=image spelling -- shape=label backend boxes, capability chips, and
# the resolved sap-btp-chip text cell all carry an image= too and were
# previously dropped with no warning at all (worse than the atlas-miss path).
# ─────────────────────────────────────────────────────────────────────────
def test_shape_label_cell_with_image_composites_the_icon(tmp_path):
    out = tmp_path / "out.png"
    assert pr.main([str(FIXTURE), "--out", str(out), "--scale", "2"]) == 0
    with Image.open(out) as img:
        # boxicon1: x=240,y=160,w=200,h=48, shape=label, imageAlign=left,
        # imageVerticalAlign=middle, imageWidth=28, imageHeight=28 -> the
        # icon's own (unscaled) box is (242,170,28,28) -- see icon_box_rect
        # -- i.e. scaled (484,340)-(540,396).
        icon_box = (484, 340, 540, 396)
        crop = img.crop(icon_box)
        # 1) not empty/flat.
        assert any(s > 0 for s in ImageStat.Stat(crop).stddev)
        # 2) not merely the rect's own white fill / blue border / a grey
        #    placeholder -- some pixel is genuinely icon-colored.
        assert _region_has_extra_color(img, icon_box, [(255, 255, 255), pr.PLACEHOLDER_RGB, (0x00, 0x70, 0xF2)])

        # The shape's own rect border AND its text label are STILL drawn --
        # compositing the icon is additive, never a replacement.
        assert _region_has_color(img, (520, 316, 840, 328), (0x00, 0x70, 0xF2))  # top border
        assert _region_has_color(img, (580, 352, 840, 388), (0x1D, 0x2D, 0x3E))  # "Backend Service"


def test_plain_shapes_without_image_key_never_trigger_icon_lookup_warnings(tmp_path, capsys):
    """Negative control for FIX-2(b): a cell with no ``image=`` at all
    (a plain rect/pill/ellipse/text/unknown-shape cell) must never be
    treated as "an icon lookup that missed" -- extract_image_value(...)
    has to gate the new unconditional compositing call, or every ordinary
    shape would spuriously warn about a nonexistent icon."""
    out = tmp_path / "out.png"
    rc = pr.main([str(FIXTURE), "--out", str(out), "--scale", "2"])
    stderr = capsys.readouterr().err
    assert rc == 0
    for cell_id in ("rect1", "pill1", "step1", "src1", "tgt1", "badshape1"):
        assert cell_id not in stderr


# ─────────────────────────────────────────────────────────────────────────
# FIX-3: shape=cylinder3 / shape=cylinder (the "db" molecule) -- previously
# fell through to a plain rect with no visual distinction at all.
# ─────────────────────────────────────────────────────────────────────────
def test_cylinder3_shape_has_a_curved_top_distinct_from_a_plain_rect(tmp_path):
    out = tmp_path / "out.png"
    assert pr.main([str(FIXTURE), "--out", str(out), "--scale", "2"]) == 0
    with Image.open(out) as img:
        # cyl1: x=480,y=160,w=60,h=80, strokeColor=#0070F2, fillColor=#FFFFFF,
        # size=15 -> scaled bounding box (960,320)-(1080,480).
        # A PLAIN rect (same stroke) paints its border into its own
        # bounding-box corners; an elliptical cap does NOT reach them --
        # the corner must stay plain background...
        assert img.getpixel((962, 322)) == (255, 255, 255)
        # ...while the cap's arc IS present at the top-center, proving a
        # shape was actually drawn there (not just an empty box).
        assert img.getpixel((1020, 321)) == (0x00, 0x70, 0xF2)


def test_draw_cylinder_unit_caps_dont_reach_the_bounding_box_corners():
    """Unit-level version of the fixture test above, isolated from any
    surrounding geometry: draw_cylinder's own corner must stay background."""
    img = Image.new("RGB", (120, 160), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    pr.draw_cylinder(draw, (10, 10, 110, 150), (255, 255, 255), (0, 112, 242), 2, cap_h_px=30)
    assert img.getpixel((12, 12)) == (255, 255, 255)  # corner: untouched
    assert img.getpixel((60, 10)) == (0, 112, 242)  # top-center: cap outline


def test_unknown_shape_falls_back_to_plain_rect_with_a_warning(tmp_path, capsys):
    out = tmp_path / "out.png"
    rc = pr.main([str(FIXTURE), "--out", str(out), "--scale", "2"])
    stderr = capsys.readouterr().err

    assert rc == 0
    assert "WARNING" in stderr
    assert "hexagon" in stderr

    with Image.open(out) as img:
        # badshape1: x=600,y=160,w=60,h=40, fillColor=#F5F6F7 -- a PLAIN
        # rect (unlike the cylinder above), so even a point near its own
        # bounding-box corner is filled.
        assert img.getpixel((1204, 324)) == (0xF5, 0xF6, 0xF7)


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


def test_icon_box_rect_defaults_to_the_whole_rect():
    """The plain shape=image case, and any image= cell with no positioning
    hints (e.g. the resolved sap-btp-chip text cell): fit the icon to the
    WHOLE cell rect, unchanged."""
    rect = (10.0, 20.0, 90.0, 30.0)
    assert pr.icon_box_rect(rect, pr.parse_style("")) == rect
    assert pr.icon_box_rect(rect, pr.parse_style("text;fontSize=16")) == rect


def test_icon_box_rect_honors_image_width_height_and_alignment():
    rect = (100.0, 100.0, 200.0, 60.0)
    style = pr.parse_style("imageWidth=28;imageHeight=28;imageAlign=left;imageVerticalAlign=middle")
    bx, by, bw, bh = pr.icon_box_rect(rect, style)
    assert (bw, bh) == (28.0, 28.0)
    assert bx == 100.0 + pr._ICON_EDGE_INSET  # hugs the left edge
    assert by == 100.0 + (60.0 - 28.0) / 2.0  # vertically centered

    style_right_top = pr.parse_style("imageWidth=20;imageHeight=20;imageAlign=right;imageVerticalAlign=top")
    bx2, by2, _, _ = pr.icon_box_rect(rect, style_right_top)
    assert bx2 == 100.0 + 200.0 - 20.0 - pr._ICON_EDGE_INSET
    assert by2 == 100.0 + pr._ICON_EDGE_INSET


def test_icon_box_rect_never_exceeds_the_cell_rect():
    """An imageWidth/imageHeight larger than the cell itself (malformed
    input) must clamp down rather than overflow the shape's own box."""
    rect = (0.0, 0.0, 40.0, 20.0)
    style = pr.parse_style("imageWidth=999;imageHeight=999")
    _, _, bw, bh = pr.icon_box_rect(rect, style)
    assert (bw, bh) == (40.0, 20.0)


def test_label_band_rect_places_caption_outside_the_shape():
    rect = (40.0, 160.0, 48.0, 48.0)
    bottom_style = pr.parse_style("verticalLabelPosition=bottom;fontSize=10")
    x, y, w, h = pr.label_band_rect(rect, bottom_style)
    assert (x, w) == (40.0, 48.0)
    assert y == 208.0  # rect's bottom edge (160 + 48)
    assert h > 0

    top_style = pr.parse_style("verticalLabelPosition=top;fontSize=10")
    _, y_top, _, h_top = pr.label_band_rect(rect, top_style)
    assert y_top == 160.0 - h_top

    # Unset (the common case: a label INSIDE the shape) -> unchanged.
    assert pr.label_band_rect(rect, pr.parse_style("")) == rect
