# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""tests/test_icon_atlas.py — Task 3 (icon atlas) contract + unit tests.

Two families:
  * Artifact/contract tests — read the COMMITTED assets/icon-atlas/index.json
    and PNGs directly. These need no rasterizer (the atlas is pre-built) and
    must always run in CI.
  * Pure-function / pipeline unit tests — exercise build-icon-atlas.py's
    helpers directly via ``load_script``. The handful that actually rasterize
    an SVG are skipped when neither resvg nor cairosvg is installed (CI's
    engine-smoke-test.yml currently installs pytest + pillow only), mirroring
    tests/test_style_contract.py's ``pytest.importorskip("jsonschema")``
    pattern for an optional dependency.
"""
import base64
import hashlib
import json
import subprocess
import shutil
from pathlib import Path

import pytest
from PIL import Image

from conftest import load_script

ROOT = Path(__file__).resolve().parent.parent
ATLAS_DIR = ROOT / "assets" / "icon-atlas"
LOCAL_ATLAS_DIR = ROOT / "assets" / "icon-atlas.local"
LOCAL_BRAND_PACK = ROOT / "assets" / "brand-pack.local" / "index.json"


def _has_rasterizer() -> bool:
    if shutil.which("resvg"):
        return True
    try:
        import cairosvg  # noqa: F401
    except ImportError:
        return False
    return True


requires_rasterizer = pytest.mark.skipif(
    not _has_rasterizer(), reason="neither resvg nor cairosvg is available")


def shape_index() -> dict:
    return json.loads((ROOT / "assets/shape-index.json").read_text(encoding="utf-8"))


def atlas_index() -> dict:
    return json.loads((ATLAS_DIR / "index.json").read_text(encoding="utf-8"))


def _assert_all_pngs_are_valid(index: dict, atlas_dir: Path) -> None:
    files = set(index["by_name"].values()) | set(index["by_sha1"].values())
    assert files, "index.json has no entries"
    for rel in files:
        p = atlas_dir / rel
        assert p.exists(), f"missing PNG referenced by index.json: {rel}"
        with Image.open(p) as img:
            assert img.size == (96, 96), f"{rel} is {img.size}, expected (96, 96)"


# ─────────────────────────────────────────────────────────────────────────
# Step 1 contract: index.json exists; every entry's PNG exists, opens, is
# 96x96; every services/genericIcons icon name has an atlas entry (modulo an
# empty skip-list).
# ─────────────────────────────────────────────────────────────────────────
def test_index_json_exists():
    assert (ATLAS_DIR / "index.json").exists()


def test_every_atlas_png_exists_opens_and_is_96x96():
    _assert_all_pngs_are_valid(atlas_index(), ATLAS_DIR)


def test_skip_list_is_currently_empty():
    bia = load_script("build-icon-atlas")
    assert bia.SKIP_LIST == frozenset(), (
        "SKIP_LIST must stay empty unless a specific icon is provably "
        "unrasterizable by both backends -- see the module docstring"
    )


def test_every_service_icon_name_has_atlas_entry():
    bia = load_script("build-icon-atlas")
    expected = {
        s["name"] for s in shape_index()["services"]
        if "image=data:image/svg" in (s.get("drawioStyle") or "")
    } - bia.SKIP_LIST
    missing = expected - set(atlas_index()["by_name"])
    assert not missing, f"{len(missing)} service icon name(s) missing from the atlas: {sorted(missing)[:10]}"


def test_every_generic_icon_name_has_atlas_entry():
    bia = load_script("build-icon-atlas")
    expected = {
        g["name"] for g in shape_index()["genericIcons"]
        if "image=data:image/svg" in (g.get("drawioStyle") or "")
    } - bia.SKIP_LIST
    missing = expected - set(atlas_index()["by_name"])
    assert not missing, f"{len(missing)} generic icon name(s) missing from the atlas: {sorted(missing)[:10]}"


# ─────────────────────────────────────────────────────────────────────────
# Binding decision: brand-pack (public) is rasterized into the SAME committed
# atlas; brand-pack.local goes to the gitignored assets/icon-atlas.local/ and
# must never leak into the public one.
# ─────────────────────────────────────────────────────────────────────────
def test_public_brand_pack_entry_present():
    assert "sap-logo-chip" in atlas_index()["by_name"]


def test_public_atlas_excludes_local_brand_entries():
    if not LOCAL_BRAND_PACK.exists():
        pytest.skip("brand-pack.local not hydrated in this environment")
    local_keys = set(json.loads(LOCAL_BRAND_PACK.read_text(encoding="utf-8")))
    leaked = local_keys & set(atlas_index()["by_name"])
    assert not leaked, f"local-only brand-pack keys leaked into the public atlas: {leaked}"


def test_local_atlas_is_gitignored_and_present_when_source_is_hydrated():
    if not LOCAL_BRAND_PACK.exists():
        pytest.skip("brand-pack.local not hydrated in this environment")
    assert (LOCAL_ATLAS_DIR / "index.json").exists()
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", str(LOCAL_ATLAS_DIR)], cwd=ROOT)
    assert ignored.returncode == 0, "assets/icon-atlas.local/ must be gitignored"


def test_local_atlas_covers_every_local_brand_key():
    if not LOCAL_BRAND_PACK.exists():
        pytest.skip("brand-pack.local not hydrated in this environment")
    local_keys = set(json.loads(LOCAL_BRAND_PACK.read_text(encoding="utf-8")))
    local_index = json.loads((LOCAL_ATLAS_DIR / "index.json").read_text(encoding="utf-8"))
    missing = local_keys - set(local_index["by_name"])
    assert not missing, f"local brand-pack keys missing from icon-atlas.local: {missing}"
    _assert_all_pngs_are_valid(local_index, LOCAL_ATLAS_DIR)


# ─────────────────────────────────────────────────────────────────────────
# The load-bearing sha1 contract: build-icon-atlas's normalization must be
# byte-identical to what generate-drawio.py's _safe_img/_extract_image_uri
# actually write into the emitted .drawio (Task 10 sha1's THAT string).
# ─────────────────────────────────────────────────────────────────────────
def test_sha1_normalization_matches_the_real_emitter():
    bia = load_script("build-icon-atlas")
    gen = load_script("generate-drawio")
    marker_style = "shape=image;image=data:image/svg+xml;base64,QUJDRA==;fontSize=10"
    comma_style = "shape=image;image=data:image/svg+xml,QUJDRA==;fontSize=10"
    for style in (marker_style, comma_style):
        emitter_uri = gen._extract_image_uri(style)
        atlas_uri = bia.extract_image_value(style)
        assert emitter_uri == atlas_uri == "data:image/svg+xml,QUJDRA=="
        assert bia.sha1_of(atlas_uri) == hashlib.sha1(emitter_uri.encode("utf-8")).hexdigest()


def test_brand_pack_datauri_is_already_normalized_for_sha1():
    bia = load_script("build-icon-atlas")
    # harvest-brand-assets.py's contract: dataUri is pre-stripped of ";base64,"
    # -- normalizing it again must be a no-op.
    assert bia.normalize_style("data:image/png,AAAA") == "data:image/png,AAAA"


# ─────────────────────────────────────────────────────────────────────────
# Pure-function unit tests (no rasterizer needed)
# ─────────────────────────────────────────────────────────────────────────
def test_decode_data_uri_base64_comma_form():
    bia = load_script("build-icon-atlas")
    payload = base64.b64encode(b"hello").decode()
    mime, raw = bia.decode_data_uri(f"data:text/plain,{payload}")
    assert mime == "text/plain" and raw == b"hello"


def test_decode_data_uri_rejects_non_data_uri():
    bia = load_script("build-icon-atlas")
    with pytest.raises(ValueError):
        bia.decode_data_uri("not-a-data-uri")


def test_intrinsic_svg_size_prefers_viewbox():
    bia = load_script("build-icon-atlas")
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 412.38 204" width="99" height="99"/>'
    assert bia.intrinsic_svg_size(svg) == (412.38, 204.0)


def test_intrinsic_svg_size_falls_back_to_width_height():
    bia = load_script("build-icon-atlas")
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="16"/>'
    assert bia.intrinsic_svg_size(svg) == (24.0, 16.0)


def test_intrinsic_svg_size_falls_back_to_square_when_undeclared():
    bia = load_script("build-icon-atlas")
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
    assert bia.intrinsic_svg_size(svg) == (1.0, 1.0)


def test_fit_dimensions_preserves_aspect():
    bia = load_script("build-icon-atlas")
    assert bia.fit_dimensions(16, 16, 96) == (96, 96)
    assert bia.fit_dimensions(412.38, 204, 96) == (96, 47)  # sap-logo-chip's real aspect


def test_fit_and_pad_pads_non_square_content_transparently():
    bia = load_script("build-icon-atlas")
    wide = Image.new("RGBA", (200, 100), (255, 0, 0, 255))
    out = bia.fit_and_pad(wide, 96)
    assert out.size == (96, 96)
    # 200x100 -> scale 0.48 -> rendered 96x48, letterboxed top/bottom by 24px.
    assert out.getpixel((48, 2))[3] == 0               # top padding: transparent
    assert out.getpixel((48, 93))[3] == 0              # bottom padding: transparent
    assert out.getpixel((48, 48)) == (255, 0, 0, 255)  # content: opaque red


def test_prefer_size_tie_break():
    bia = load_script("build-icon-atlas")
    assert bia._prefer_size(None, "M") is True
    assert bia._prefer_size("M", "S") is False
    assert bia._prefer_size("S", "L") is False
    assert bia._prefer_size("S", "M") is True


def test_iter_brand_pack_sources_never_fails_when_absent(tmp_path):
    bia = load_script("build-icon-atlas")
    assert bia.iter_brand_pack_sources(tmp_path / "does-not-exist.json") == []


# ─────────────────────────────────────────────────────────────────────────
# CLI wiring (no rasterizer touched -- both fail before detect_rasterizer())
# ─────────────────────────────────────────────────────────────────────────
def test_main_requires_date_argument():
    bia = load_script("build-icon-atlas")
    with pytest.raises(SystemExit):
        bia.main(["--shape-index", str(ROOT / "assets/shape-index.json")])


def test_main_errors_cleanly_on_missing_shape_index(tmp_path):
    bia = load_script("build-icon-atlas")
    rc = bia.main([
        "--shape-index", str(tmp_path / "nope.json"),
        "--out", str(tmp_path / "atlas"), "--out-local", str(tmp_path / "atlas-local"),
        "--brand-pack", str(tmp_path / "bp"), "--brand-pack-local", str(tmp_path / "bpl"),
        "--date", "2026-01-01",
    ])
    assert rc == 2


# ─────────────────────────────────────────────────────────────────────────
# End-to-end pipeline: decode -> rasterize -> fit/pad -> write -> index.
# Needs a live rasterizer.
# ─────────────────────────────────────────────────────────────────────────
_TINY_RED_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10" fill="#ff0000"/></svg>'
_TINY_GREEN_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10" fill="#00ff00"/></svg>'
_TINY_RED_URI = "data:image/svg+xml," + base64.b64encode(_TINY_RED_SVG).decode()
_TINY_GREEN_URI = "data:image/svg+xml," + base64.b64encode(_TINY_GREEN_SVG).decode()


@requires_rasterizer
def test_process_sources_dedupes_identical_content_by_sha1(tmp_path):
    bia = load_script("build-icon-atlas")
    rasterizer = bia.detect_rasterizer()
    style = f"shape=image;image={_TINY_RED_URI};fontSize=10"
    sources = [
        bia.IconSource(name="Widget A", value=style, is_style=True, size="M"),
        bia.IconSource(name="Widget B", value=style, is_style=True, size="M"),
    ]
    icons_dir = tmp_path / "icons"
    icons_dir.mkdir()
    by_name, by_sha1, failures = bia.process_sources(sources, 96, rasterizer, icons_dir)
    assert not failures
    assert set(by_name) == {"Widget A", "Widget B"}
    assert len(by_sha1) == 1                              # one unique image
    assert by_name["Widget A"] == by_name["Widget B"]     # both point at the same file
    assert len(list(icons_dir.glob("*.png"))) == 1        # rasterized exactly once
    with Image.open(icons_dir / next(icons_dir.glob("*.png"))) as img:
        assert img.size == (96, 96)


@requires_rasterizer
def test_process_sources_prefers_m_size_on_name_collision(tmp_path):
    bia = load_script("build-icon-atlas")
    rasterizer = bia.detect_rasterizer()
    style_s = f"shape=image;image={_TINY_RED_URI};fontSize=10"
    style_m = f"shape=image;image={_TINY_GREEN_URI};fontSize=10"
    sources = [
        bia.IconSource(name="SAP Widget", value=style_s, is_style=True, size="S"),
        bia.IconSource(name="SAP Widget", value=style_m, is_style=True, size="M"),
    ]
    icons_dir = tmp_path / "icons"
    icons_dir.mkdir()
    by_name, by_sha1, failures = bia.process_sources(sources, 96, rasterizer, icons_dir)
    assert not failures
    m_sha1 = bia.sha1_of(_TINY_GREEN_URI)
    assert by_name["SAP Widget"] == f"icons/{m_sha1}.png"   # M wins over S


@requires_rasterizer
def test_write_atlas_only_merges_into_existing_index(tmp_path):
    bia = load_script("build-icon-atlas")
    rasterizer = bia.detect_rasterizer()
    style_a = f"shape=image;image={_TINY_RED_URI};fontSize=10"
    style_b = f"shape=image;image={_TINY_GREEN_URI};fontSize=10"
    atlas_dir = tmp_path / "atlas"

    index1, failures1 = bia.write_atlas(
        atlas_dir, [bia.IconSource("Icon A", style_a, True, "M")],
        96, rasterizer, "2026-01-01", None)
    assert not failures1
    assert set(index1["by_name"]) == {"Icon A"}

    index2, failures2 = bia.write_atlas(
        atlas_dir,
        [bia.IconSource("Icon A", style_a, True, "M"), bia.IconSource("Icon B", style_b, True, "M")],
        96, rasterizer, "2026-01-02", {"Icon B"},
    )
    assert not failures2
    assert set(index2["by_name"]) == {"Icon A", "Icon B"}   # merged, not replaced
    assert index2["meta"]["generated"] == "2026-01-02"


@requires_rasterizer
def test_write_atlas_full_rebuild_prunes_stale_pngs(tmp_path):
    bia = load_script("build-icon-atlas")
    rasterizer = bia.detect_rasterizer()
    style_a = f"shape=image;image={_TINY_RED_URI};fontSize=10"
    style_b = f"shape=image;image={_TINY_GREEN_URI};fontSize=10"
    atlas_dir = tmp_path / "atlas"

    bia.write_atlas(atlas_dir, [bia.IconSource("Icon A", style_a, True, "M")],
                     96, rasterizer, "2026-01-01", None)
    stale_png = next((atlas_dir / "icons").glob("*.png"))
    assert stale_png.exists()

    index2, _ = bia.write_atlas(atlas_dir, [bia.IconSource("Icon B", style_b, True, "M")],
                                 96, rasterizer, "2026-01-02", None)
    assert set(index2["by_name"]) == {"Icon B"}   # "Icon A" is gone: full rebuild, not merged
    assert not stale_png.exists()                 # its PNG was pruned
