#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""
render-preview.py — render a .drawio to PNG via the draw.io desktop CLI.

Part of the visual verification loop: after generating a diagram, render it so
its composition can be eyeballed (or diffed) against the SAP gold standard.
Rendering is a *convenience*, never a gate — if no draw.io launcher is present
(typical in CI) the script prints a notice and exits 0.

Usage:
    python3 render-preview.py diagram.drawio
    python3 render-preview.py diagram.drawio --out preview.png --scale 2
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render a .drawio to an image via the draw.io CLI.")
    ap.add_argument("input", help="Path to the .drawio file.")
    ap.add_argument("--out", help="Output image path (default: alongside input).")
    ap.add_argument("--format", default="png", help="png | svg | pdf (default png).")
    ap.add_argument("--scale", default="2", help="Export scale (default 2).")
    ap.add_argument("--timeout", type=float, default=90.0, help="Seconds before giving up.")
    args = ap.parse_args(argv)

    if not Path(args.input).exists():
        print(f"ERROR: file not found: {args.input}", file=sys.stderr)
        return 2

    out = args.out or str(Path(args.input).with_suffix("." + args.format))
    launcher = find_launcher()
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
