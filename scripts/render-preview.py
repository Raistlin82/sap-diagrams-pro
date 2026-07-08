#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""
render-preview.py — render a .drawio to PNG, picking the best available engine.

Part of the visual verification loop: after generating a diagram, render it so
its composition can be eyeballed (or diffed) against the SAP gold standard.
Two engines can produce the PNG:

  - "drawio": the draw.io desktop CLI (Electron export). Pixel-accurate, but
    only present on a machine with draw.io desktop installed.
  - "pure": scripts/_pure_render.py, a pure-Python/Pillow renderer for OUR
    emitted vocabulary only. Slightly lower fidelity, but works anywhere
    Pillow is installed — including CI and claude.ai, where draw.io never is.

`--engine auto` (the default) picks draw.io when it resolves on this machine,
else falls back to the pure renderer — so by default rendering is a
*convenience* that (almost) never fails outright: it only prints a notice and
exits 0 without a PNG when `--engine drawio` is explicitly requested and no
draw.io launcher is present, preserving the historical CI-safe behavior for
callers that ask for draw.io by name.

Usage:
    python3 render-preview.py diagram.drawio
    python3 render-preview.py diagram.drawio --out preview.png --scale 2
    python3 render-preview.py diagram.drawio --engine pure
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

_CANDIDATES = [
    "/Applications/draw.io.app/Contents/MacOS/draw.io",
    os.path.expanduser("~/Applications/draw.io.app/Contents/MacOS/draw.io"),
    "/opt/drawio/drawio",
]


def find_launcher() -> str | None:
    for c in _CANDIDATES:
        if Path(c).exists():
            return c
    for name in ("drawio", "draw.io"):
        found = shutil.which(name)
        if found:
            return found
    return None


def render(launcher: str, src: str, out: str, fmt: str, scale: str, timeout: float) -> bool:
    """Run the Electron export, killing it if it hangs past `timeout` seconds."""
    proc = subprocess.Popen(
        [launcher, "--export", "--format", fmt, "--scale", str(scale),
         "--border", "20", "--output", out, src],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    start = time.time()
    while proc.poll() is None:
        if time.time() - start > timeout:
            proc.kill()
            break
        time.sleep(0.5)
    return Path(out).exists()


def render_pure(src: str, out: str, scale: str) -> int:
    """Render via scripts/_pure_render.py, in-process.

    Imported lazily (only when the pure engine is actually selected) so a
    machine with draw.io but no Pillow can still use `--engine drawio` (or
    auto-select drawio) without ever paying Pillow's import cost — or being
    forced to have it installed at all.

    Returns _pure_render's own exit code: 0 ok, 1 render error, 2 bad input,
    3 if Pillow itself is missing (that guard fires at import time, as a
    SystemExit — caught here and surfaced as a plain return code so `main()`
    never needs to special-case a raised SystemExit from a helper).
    """
    try:
        import _pure_render
    except SystemExit as exc:
        # _pure_render already printed its own "pip install pillow" message
        # to stderr before raising; just relay the exit code.
        return exc.code if isinstance(exc.code, int) else 3
    return _pure_render.main([src, "--out", out, "--scale", str(scale)])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Render a .drawio to an image via the draw.io CLI or the pure-Python renderer.")
    ap.add_argument("input", help="Path to the .drawio file.")
    ap.add_argument("--out", help="Output image path (default: alongside input).")
    ap.add_argument("--format", default="png", help="png | svg | pdf (default png; the pure engine only emits png).")
    ap.add_argument("--scale", default="2", help="Export scale (default 2).")
    ap.add_argument("--timeout", type=float, default=90.0, help="Seconds before giving up (drawio engine only).")
    ap.add_argument(
        "--engine", choices=("auto", "drawio", "pure"), default="auto",
        help="auto (default): draw.io if it resolves on this machine, else the pure renderer. "
             "drawio: force the draw.io CLI (skips gracefully, exit 0, if absent). "
             "pure: force scripts/_pure_render.py regardless of draw.io's presence.")
    args = ap.parse_args(argv)

    if not Path(args.input).exists():
        print(f"ERROR: file not found: {args.input}", file=sys.stderr)
        return 2

    out = args.out or str(Path(args.input).with_suffix("." + args.format))

    # Only probe for a draw.io launcher if it could actually matter — pure
    # (explicitly requested) never looks at draw.io's presence at all.
    engine = args.engine
    launcher: str | None = None
    if engine in ("auto", "drawio"):
        launcher = find_launcher()
        if engine == "auto":
            engine = "drawio" if launcher else "pure"

    if engine == "pure":
        if args.format != "png":
            print(f"ERROR: --engine pure only supports --format png (got {args.format!r}).", file=sys.stderr)
            return 2
        return render_pure(args.input, out, args.scale)

    # engine == "drawio" (explicitly requested, or auto's pick).
    if not launcher:
        print("ℹ️ draw.io launcher not found — skipping PNG render (not a failure). "
              "Install draw.io desktop or put `drawio` on PATH to enable previews.",
              file=sys.stderr)
        return 0

    if render(launcher, args.input, out, args.format, args.scale, args.timeout):
        print(f"✅ Rendered {out}")
    else:
        print(f"⚠️ draw.io render failed or timed out for {args.input}", file=sys.stderr)
    return 0  # never hard-fail the pipeline on a render hiccup


if __name__ == "__main__":
    raise SystemExit(main())
