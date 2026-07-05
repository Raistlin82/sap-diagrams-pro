# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""tests/test_render_preview.py — Task 11 (render-preview engine auto-selection).

render-preview.py wraps two engines behind ``--engine auto|drawio|pure``:
  * "drawio" — the draw.io desktop CLI (Electron export). Historical
    behavior: gracefully skipped (prints a notice, exit 0, no PNG) when no
    launcher is found.
  * "pure" — scripts/_pure_render.py, run in-process. Works wherever Pillow
    is installed; never needs draw.io at all.

``--engine auto`` (the default) picks drawio when ``find_launcher()``
resolves a binary, else falls back to pure. Rather than depend on whether
draw.io desktop happens to be installed on the machine running the suite
(it IS installed on at least one contributor's Mac — see
``_CANDIDATES`` in render-preview.py, which checks
``/Applications/draw.io.app/...`` directly, independent of ``$PATH`` — so
merely scrubbing ``$PATH`` would not reliably hide it there), these tests
monkeypatch ``find_launcher`` (and, where the drawio branch is exercised,
``render``) to control engine selection deterministically.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PIL import Image

from conftest import load_script

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "render-sample.drawio"

rp = load_script("render-preview")


def _assert_valid_png(path: Path) -> None:
    assert path.exists() and path.stat().st_size > 0
    with Image.open(path) as img:
        assert img.size[0] > 0 and img.size[1] > 0


# ─────────────────────────────────────────────────────────────────────────
# --engine pure: never needs draw.io
# ─────────────────────────────────────────────────────────────────────────
def test_engine_pure_never_probes_for_a_drawio_launcher(tmp_path, monkeypatch):
    """White-box guarantee: --engine pure must not even call find_launcher()
    — it forces the pure renderer "regardless of whether draw.io is
    present" per spec, not merely "when draw.io happens to be absent"."""
    def unexpected_finder():
        raise AssertionError("find_launcher() must not run for --engine pure")

    monkeypatch.setattr(rp, "find_launcher", unexpected_finder)
    out = tmp_path / "out.png"
    rc = rp.main([str(FIXTURE), "--out", str(out), "--engine", "pure"])
    assert rc == 0
    _assert_valid_png(out)


def test_cli_engine_pure_end_to_end_with_drawio_path_hidden(tmp_path):
    """Black-box / literal spec check, run as a real subprocess (not just
    rp.main() in-process): scrub $PATH down to /usr/bin:/bin — no
    drawio/draw.io binary can resolve via shutil.which() on such a PATH —
    and confirm `--engine pure` still produces a real, valid PNG. This also
    exercises the actual `if __name__ == "__main__":` entry point, proving
    the deferred `import _pure_render` inside render_pure() resolves via
    the script's own directory being auto-added to sys.path[0] (the same
    mechanism conftest.py's sys.path.insert relies on for pytest
    collection) — not just via conftest's test-only rigging.
    """
    out = tmp_path / "out.png"
    env = dict(os.environ)
    env["PATH"] = "/usr/bin:/bin"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "render-preview.py"),
         str(FIXTURE), "--out", str(out), "--engine", "pure"],
        capture_output=True, text=True, cwd=ROOT, env=env,
    )
    assert result.returncode == 0, result.stderr
    _assert_valid_png(out)


def test_engine_pure_on_real_fixture_is_deterministic(tmp_path):
    """Determinism/smoke: two --engine pure runs of the same input produce
    byte-identical, valid PNGs (mirrors _pure_render's own determinism
    guarantee, exercised here through render-preview's CLI surface)."""
    out1, out2 = tmp_path / "a.png", tmp_path / "b.png"
    assert rp.main([str(FIXTURE), "--out", str(out1), "--engine", "pure", "--scale", "2"]) == 0
    assert rp.main([str(FIXTURE), "--out", str(out2), "--engine", "pure", "--scale", "2"]) == 0
    _assert_valid_png(out1)
    assert out1.read_bytes() == out2.read_bytes()


def test_engine_pure_rejects_non_png_format(tmp_path, capsys):
    """The pure renderer only emits PNG; asking for --format svg/pdf must
    fail loudly (bad usage, exit 2) rather than silently writing PNG bytes
    into a file named .svg."""
    out = tmp_path / "out.svg"
    rc = rp.main([str(FIXTURE), "--out", str(out), "--engine", "pure", "--format", "svg"])
    stderr = capsys.readouterr().err
    assert rc == 2
    assert "png" in stderr.lower()
    assert not out.exists()


def test_engine_pure_surfaces_exit_3_when_pillow_is_unavailable(tmp_path, monkeypatch):
    """Mirrors test_pure_render.py::test_pillow_guard_exits_3_when_pillow_unavailable:
    simulate a Pillow-less environment via the standard sys.modules[name]=None
    ImportError sentinel and confirm render-preview relays _pure_render's
    exit code 3 (with its "pip install pillow" message already on stderr)
    instead of crashing with an unhandled SystemExit or a traceback."""
    monkeypatch.setitem(sys.modules, "PIL", None)
    sys.modules.pop("_pure_render", None)
    try:
        out = tmp_path / "out.png"
        rc = rp.main([str(FIXTURE), "--out", str(out), "--engine", "pure"])
        assert rc == 3
        assert not out.exists()
    finally:
        sys.modules.pop("_pure_render", None)


# ─────────────────────────────────────────────────────────────────────────
# --engine auto: drawio when resolvable, else pure
# ─────────────────────────────────────────────────────────────────────────
def test_engine_auto_picks_drawio_when_launcher_resolves(tmp_path, monkeypatch, capsys):
    calls = []

    def fake_render(launcher, src, out, fmt, scale, timeout):
        calls.append((launcher, src, out, fmt, scale, timeout))
        Path(out).write_bytes(b"fake-drawio-png-bytes")
        return True

    def unexpected_pure(*_a, **_k):
        raise AssertionError("render_pure() must not run when draw.io resolves")

    monkeypatch.setattr(rp, "find_launcher", lambda: "/fake/bin/drawio")
    monkeypatch.setattr(rp, "render", fake_render)
    monkeypatch.setattr(rp, "render_pure", unexpected_pure)

    out = tmp_path / "out.png"
    rc = rp.main([str(FIXTURE), "--out", str(out), "--engine", "auto"])

    assert rc == 0
    assert len(calls) == 1
    assert calls[0][0] == "/fake/bin/drawio"
    assert out.read_bytes() == b"fake-drawio-png-bytes"
    assert "Rendered" in capsys.readouterr().out


def test_engine_auto_picks_pure_when_no_launcher_found(tmp_path, monkeypatch):
    """Also proves the default engine is "auto": --engine is omitted
    entirely here, matching pre-Task-11 invocations verbatim — those now
    get a real PNG instead of a graceful skip whenever Pillow is present."""
    def unexpected_drawio(*_a, **_k):
        raise AssertionError("render() [drawio] must not run when no launcher is found")

    monkeypatch.setattr(rp, "find_launcher", lambda: None)
    monkeypatch.setattr(rp, "render", unexpected_drawio)

    out = tmp_path / "out.png"
    rc = rp.main([str(FIXTURE), "--out", str(out)])  # no --engine -> default "auto"
    assert rc == 0
    _assert_valid_png(out)


# ─────────────────────────────────────────────────────────────────────────
# --engine drawio: unchanged historical behavior (explicit request only)
# ─────────────────────────────────────────────────────────────────────────
def test_engine_drawio_explicit_skips_gracefully_when_absent(tmp_path, monkeypatch, capsys):
    def unexpected_pure(*_a, **_k):
        raise AssertionError("render_pure() must not run for --engine drawio")

    monkeypatch.setattr(rp, "find_launcher", lambda: None)
    monkeypatch.setattr(rp, "render_pure", unexpected_pure)

    out = tmp_path / "out.png"
    rc = rp.main([str(FIXTURE), "--out", str(out), "--engine", "drawio"])
    stderr = capsys.readouterr().err

    assert rc == 0
    assert "draw.io launcher not found" in stderr
    assert not out.exists()


def test_engine_drawio_explicit_still_renders_when_launcher_resolves(tmp_path, monkeypatch):
    """Regression net for the existing (pre-Task-11) success path: an
    explicit --engine drawio with a resolvable launcher must still call
    render() and report success exactly as before."""
    def fake_render(launcher, src, out, fmt, scale, timeout):
        Path(out).write_bytes(b"fake-drawio-png-bytes")
        return True

    monkeypatch.setattr(rp, "find_launcher", lambda: "/fake/bin/drawio")
    monkeypatch.setattr(rp, "render", fake_render)

    out = tmp_path / "out.png"
    rc = rp.main([str(FIXTURE), "--out", str(out), "--engine", "drawio"])
    assert rc == 0
    assert out.read_bytes() == b"fake-drawio-png-bytes"


# ─────────────────────────────────────────────────────────────────────────
# unchanged behavior regardless of engine
# ─────────────────────────────────────────────────────────────────────────
def test_missing_input_file_still_exits_2_before_any_engine_selection(tmp_path, monkeypatch):
    def unexpected_finder():
        raise AssertionError("engine selection must not run before the input-file check")

    monkeypatch.setattr(rp, "find_launcher", unexpected_finder)
    rc = rp.main([str(tmp_path / "does-not-exist.drawio"), "--engine", "auto"])
    assert rc == 2
