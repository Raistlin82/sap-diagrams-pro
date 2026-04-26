#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""
validate-drawio.py — check a .drawio file against the SAP BTP Solution
Diagram Guideline (atomic design + Horizon palette + line-style conventions).

Usage:
    python3 validate-drawio.py path/to/diagram.drawio
    python3 validate-drawio.py path/to/diagram.drawio --strict   # exit 1 on CRITICAL
    python3 validate-drawio.py path/to/diagram.drawio --json     # machine-readable

Exit codes:
    0  — no CRITICAL issues (or any issues if --strict not set)
    1  — at least one CRITICAL issue and --strict requested
    2  — input file unreadable or not a valid mxfile
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

# Horizon palette (subset that we actively check). Source:
# btp-solution-diagrams/guideline/docs/btp_guideline/foundation.md
HORIZON_BORDERS = {
    "#0070F2",  # BTP border
    "#475E75",  # non-SAP border
    "#188918",  # positive
    "#C35500",  # critical
    "#D20A0A",  # negative
    "#07838F",  # accent teal
    "#5D36FF",  # accent purple
    "#CC00DC",  # accent pink
}
HORIZON_FILLS = {
    "#EBF8FF",  # BTP fill
    "#F5F6F7",  # non-SAP fill
    "#FFFFFF",  # white (allowed for inner nodes)
    "#F5FAE5",  # positive fill
    "#FFF8D6",  # critical fill
    "#FFEAF4",  # negative fill
    "#DAFDF5",  # accent teal fill
    "#F1ECFF",  # accent purple fill
    "#FFF0FA",  # accent pink fill
    "none",
}
HORIZON_TEXT = {"#1D2D3E", "#556B82"}

SEVERITY_RANK = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}


@dataclass
class Issue:
    severity: str  # CRITICAL | WARNING | INFO
    rule: str
    message: str
    cell_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "rule": self.rule,
            "message": self.message,
            "cell_id": self.cell_id,
        }


def _hex(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip()
    if v.lower() == "none":
        return "none"
    if not v.startswith("#"):
        return None
    return v.upper()


def _parse_style(style: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for chunk in style.split(";"):
        if "=" not in chunk:
            continue
        k, _, v = chunk.partition("=")
        out[k.strip()] = v.strip()
    return out


def validate(path: Path) -> list[Issue]:
    issues: list[Issue] = []
    try:
        tree = ET.parse(path)
    except (ET.ParseError, FileNotFoundError) as exc:
        return [Issue("CRITICAL", "PARSE", f"cannot parse XML: {exc}")]

    root = tree.getroot()
    if root.tag != "mxfile":
        issues.append(
            Issue("CRITICAL", "ROOT", f"expected <mxfile> root, got <{root.tag}>")
        )
        return issues

    diagrams = root.findall("diagram")
    if not diagrams:
        issues.append(Issue("CRITICAL", "STRUCTURE", "no <diagram> element found"))
        return issues

    cells: list[ET.Element] = []
    for d in diagrams:
        cells.extend(d.iter("mxCell"))

    if not cells:
        issues.append(Issue("WARNING", "EMPTY", "diagram has no mxCell elements"))
        return issues

    # ── Rule 1: palette compliance ───────────────────────────────────────────
    for cell in cells:
        style = cell.get("style") or ""
        cid = cell.get("id")
        if not style:
            continue
        kv = _parse_style(style)
        stroke = _hex(kv.get("strokeColor"))
        fill = _hex(kv.get("fillColor"))
        font = _hex(kv.get("fontColor"))

        if stroke and stroke not in HORIZON_BORDERS and stroke != "#FFFFFF" and stroke != "NONE":
            issues.append(
                Issue(
                    "WARNING",
                    "PALETTE_BORDER",
                    f"strokeColor {stroke} not in Horizon palette "
                    f"(allowed: {sorted(HORIZON_BORDERS)})",
                    cid,
                )
            )
        if fill and fill not in HORIZON_FILLS and fill != "NONE":
            issues.append(
                Issue(
                    "WARNING",
                    "PALETTE_FILL",
                    f"fillColor {fill} not in Horizon palette "
                    f"(allowed: {sorted(HORIZON_FILLS)})",
                    cid,
                )
            )
        if font and font not in HORIZON_TEXT:
            issues.append(
                Issue(
                    "INFO",
                    "PALETTE_TEXT",
                    f"fontColor {font} not in Horizon text palette "
                    f"(allowed: {sorted(HORIZON_TEXT)})",
                    cid,
                )
            )

    # ── Rule 2: line styles ──────────────────────────────────────────────────
    for cell in cells:
        if cell.get("edge") != "1":
            continue
        style = cell.get("style") or ""
        kv = _parse_style(style)
        dashed = kv.get("dashed", "0")
        dash_pattern = kv.get("dashPattern", "")
        stroke_w = kv.get("strokeWidth", "1")
        try:
            sw = float(stroke_w)
        except ValueError:
            sw = 1.0

        # Solid + thick=4 reserved for firewalls per the guideline.
        if sw >= 3.5 and dashed == "0":
            # Acceptable: firewall convention.
            pass
        if dashed == "1" and not dash_pattern:
            issues.append(
                Issue(
                    "INFO",
                    "EDGE_DASHED_NO_PATTERN",
                    "dashed edge without explicit dashPattern (visual ambiguity)",
                    cell.get("id"),
                )
            )

    # ── Rule 3: title presence ────────────────────────────────────────────────
    title_cells = [
        c for c in cells if c.get("style", "").startswith("text;") and c.get("value")
    ]
    if not title_cells:
        issues.append(
            Issue(
                "WARNING",
                "NO_TITLE",
                "no title text cell found — guideline requires a clear diagram title",
            )
        )

    # ── Rule 4: orphan edges (source/target missing) ─────────────────────────
    cell_ids = {c.get("id") for c in cells}
    for cell in cells:
        if cell.get("edge") != "1":
            continue
        src = cell.get("source")
        tgt = cell.get("target")
        if src and src not in cell_ids:
            issues.append(
                Issue(
                    "CRITICAL",
                    "ORPHAN_EDGE_SOURCE",
                    f"edge source {src!r} not found in diagram",
                    cell.get("id"),
                )
            )
        if tgt and tgt not in cell_ids:
            issues.append(
                Issue(
                    "CRITICAL",
                    "ORPHAN_EDGE_TARGET",
                    f"edge target {tgt!r} not found in diagram",
                    cell.get("id"),
                )
            )

    # ── Rule 5: spacing rule of thumb (overlapping geometries) ───────────────
    # mxCell coordinates are relative to the cell's `parent` attribute. Walk
    # the parent chain to compute absolute positions before comparing.
    cells_by_id = {c.get("id"): c for c in cells if c.get("id")}

    def _absolute_pos(cell_id: str, _seen: set | None = None) -> tuple[float, float]:
        if _seen is None:
            _seen = set()
        if cell_id in _seen or cell_id not in cells_by_id:
            return 0.0, 0.0
        _seen.add(cell_id)
        cell = cells_by_id[cell_id]
        geom = cell.find("mxGeometry")
        if geom is None:
            return 0.0, 0.0
        try:
            x = float(geom.get("x", "0"))
            y = float(geom.get("y", "0"))
        except ValueError:
            return 0.0, 0.0
        parent_id = cell.get("parent")
        if parent_id and parent_id not in ("0", "1"):
            px, py = _absolute_pos(parent_id, _seen)
            x += px
            y += py
        return x, y

    boxes: list[tuple[str, float, float, float, float]] = []
    for cell in cells:
        if cell.get("vertex") != "1":
            continue
        geom = cell.find("mxGeometry")
        if geom is None:
            continue
        try:
            w = float(geom.get("width", "0"))
            h = float(geom.get("height", "0"))
        except ValueError:
            continue
        cid = cell.get("id", "?")
        x, y = _absolute_pos(cid)
        boxes.append((cid, x, y, w, h))

    for i in range(len(boxes)):
        a_id, ax, ay, aw, ah = boxes[i]
        for j in range(i + 1, len(boxes)):
            b_id, bx, by, bw, bh = boxes[j]
            # Skip nested (parent containment): if B is fully inside A → not an overlap.
            if bx >= ax and by >= ay and bx + bw <= ax + aw and by + bh <= ay + ah:
                continue
            if ax >= bx and ay >= by and ax + aw <= bx + bw and ay + ah <= by + bh:
                continue
            # True overlap?
            if ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by:
                issues.append(
                    Issue(
                        "INFO",
                        "BOX_OVERLAP",
                        f"vertices {a_id} and {b_id} overlap — "
                        "spacing should be ≥ SAP-logo height",
                        f"{a_id}|{b_id}",
                    )
                )

    return issues


def render_text(issues: list[Issue], path: Path) -> str:
    if not issues:
        return f"✅ {path}: no issues — fully SAP-compliant 🎉\n"
    by_sev: dict[str, list[Issue]] = {}
    for it in issues:
        by_sev.setdefault(it.severity, []).append(it)

    lines = [f"📋 Validation report — {path}", ""]
    for sev in ("CRITICAL", "WARNING", "INFO"):
        bucket = by_sev.get(sev, [])
        if not bucket:
            continue
        marker = {"CRITICAL": "❌", "WARNING": "⚠️", "INFO": "ℹ️"}[sev]
        lines.append(f"{marker} {sev} ({len(bucket)})")
        for it in bucket:
            cell = f" [cell={it.cell_id}]" if it.cell_id else ""
            lines.append(f"  • [{it.rule}]{cell} {it.message}")
        lines.append("")
    counts = {sev: len(by_sev.get(sev, [])) for sev in ("CRITICAL", "WARNING", "INFO")}
    lines.append(
        f"Summary: {counts['CRITICAL']} critical, {counts['WARNING']} warnings, "
        f"{counts['INFO']} info"
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a .drawio file against the SAP BTP Solution Diagram Guideline."
    )
    parser.add_argument("path", help="Path to the .drawio file.")
    parser.add_argument(
        "--strict", action="store_true", help="Exit 1 if any CRITICAL issue is found."
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON instead of text."
    )
    args = parser.parse_args(argv)

    path = Path(args.path)
    if not path.exists():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 2

    issues = validate(path)
    if args.json:
        print(json.dumps([i.to_dict() for i in issues], indent=2))
    else:
        print(render_text(issues, path), end="")

    if args.strict and any(i.severity == "CRITICAL" for i in issues):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
