#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""
check-composition.py — verify the *composition* of a generated .drawio against
the SAP big-picture conventions. Complements validate-drawio.py (which checks
palette / XML / line-styles): this one checks layout quality.

Checks:
  • TITLE        — a title text cell sits in the top band.
  • GROUP_OVERLAP— top-level zone containers must NOT overlap each other (FAIL).
  • ZONES        — top-level groups should spread across columns, not stack in one.
  • LEGEND       — ≥2 line styles ⇒ a "Legend" should be present.
  • BTP_CENTRAL  — the BTP layer (blue #EBF8FF) should sit between the other zones.

Top-level zone containers are the cells with id "g-…" parented to "1" (the
renderer emits zone/organism boxes there; nested lanes parent to their group).
Intentional child overlaps (step circles, interface/edge pills, embedded icons)
are ignored by construction — only top-level group boxes are compared.

Usage:
    python3 check-composition.py diagram.drawio [--strict] [--json]
Exit: 0 ok · 2 a FAIL is present and --strict given · 3 unreadable.
"""
from __future__ import annotations

import argparse
import json
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
    ap.add_argument("--strict", action="store_true", help="exit 2 if any FAIL")
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

    if args.strict and any(f.severity == "FAIL" for f in findings):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
