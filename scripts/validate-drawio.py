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
import html
import json
import re
import shutil
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

# Horizon palette. Sources:
#   - btp-solution-diagrams/guideline/docs/btp_guideline/foundation.md
#   - btp-solution-diagrams/.../annotations_and_interfaces.xml
#   - btp-solution-diagrams/.../connectors.xml
#
# SAP's own libraries use minor variants (e.g. #475E75 in the guideline doc
# vs #475F75 in connectors.xml — same intent, 1-digit-off green channel).
# We accept both so generated diagrams using the live library colours
# don't get spurious WARNING reports.
HORIZON_BORDERS = {
    "#0070F2", "#0070F3",                  # BTP border (guideline + Interface SAP variant)
    "#475E75", "#475F75",                  # non-SAP border (guideline + connectors variant)
    "#188918",                             # positive
    "#C35500",                             # critical
    "#D20A0A",                             # negative
    "#07838F",                             # accent teal
    "#5D36FF", "#470BED", "#4628EC",       # accent purple (3 variants in SAP libraries)
    "#CC00DC",                             # accent pink (Trust)
    "NONE", "none",                        # step number circles use no stroke (intentional)
}
HORIZON_FILLS = {
    "#EBF8FF",                              # BTP fill
    "#F5F6F7",                              # non-SAP fill
    "#FFFFFF", "default",                   # white / drawio default
    "#F5FAE5",                              # positive fill (Authenticate pill)
    "#FFF8D6",                              # critical fill
    "#FFEAF4",                              # negative fill
    "#DAFDF5",                              # accent teal fill
    "#F1ECFF", "#F1EDFF",                   # accent purple fill (Authorize pill)
    "#FFF0FA",                              # accent pink fill (Trust pill)
    # Step number circle fills (numbers.xml — 30×30 ellipse with gradient).
    # Each corresponds to a stepKind colour variant.
    "#5B738B",                              # default (grey) step
    "#E0B400",                              # yellow step
    # Step circles can also reuse the border palette as their fill
    # (the white digit stays legible on any of these saturated colours).
    "#0070F2", "#0070F3",                  # blue step (BTP)
    "#5D36FF", "#470BED",                  # purple step (Authorize family)
    "#CC00DC",                             # pink step (Trust family)
    "#188918",                             # green step (Authenticate family)
    "#07838F",                             # teal step
    "none",
}
# Text colors: title/body grey + the 4 pill kinds whose label uses the
# stroke color (Trust pink, Authenticate green, Authorize purple, Generic
# Protocol grey).
HORIZON_TEXT = {
    "#1D2D3E", "#1D2D3D",                   # title (guideline + 1-off variant)
    "#0070F2", "#0070F3",                   # title / SAP-blue label (canonical)
    "#556B82",                              # body
    "#266F3A",                              # SAP pill body green
    "#188918",                              # Authenticate pill text
    "#CC00DC",                              # Trust pill text
    "#470BED",                              # Authorize pill text
    "#475F75", "#475E75",                   # Generic Protocol pill text
    "#FFFFFF",                              # step number circles use white text on coloured fill
}

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


# Tolerance (px) within which two endpoint centers count as sharing an axis:
# below this an orthogonalEdgeStyle edge with no explicit routing draws
# straight; above it draw.io inserts a 90° kink.
_AXIS_ALIGN_TOL = 2.0


def _edge_has_waypoints(cell: ET.Element) -> bool:
    """True when the edge carries at least one explicit interior waypoint
    (``<Array as="points"><mxPoint/>``). Such an edge is already routed by
    the emitter, so its shape is authorial — never a spurious bent-edge."""
    geom = cell.find("mxGeometry")
    if geom is None:
        return False
    arr = geom.find("Array[@as='points']")
    return arr is not None and arr.find("mxPoint") is not None


def _edge_has_anchor(kv: dict[str, str]) -> bool:
    """True when the edge docks at an explicit fractional port
    (exitX/exitY/entryX/entryY). The author picked the docking side, so
    draw.io's orthogonal route between the two ports is intentional."""
    return any(k in kv for k in ("exitX", "exitY", "entryX", "entryY"))


def _is_capsule_arc(kv: dict[str, str]) -> bool:
    """The SAP flow-pill / interface capsule idiom: ``rounded=1;arcSize=50``.
    Here arcSize is *meant* as a percentage (50% ⇒ fully rounded ends), so
    ``absoluteArcSize=1`` must NOT be forced on — doing so would give a 22px
    pill a 50px radius. Every arcSize cell in the shipped demos is one of
    these, which is why the ARC_SIZE_ABS check/​autofix exempt them."""
    return kv.get("rounded") == "1" and kv.get("arcSize") == "50"


def validate(path: Path) -> list[Issue]:
    issues: list[Issue] = []
    try:
        tree = _xml_parse(path)
    except Exception as exc:  # ParseError, FileNotFoundError, or defusedxml guards
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

    # ── Rule 6: bent orthogonal edges (the highest-leverage polish check) ────
    # An ``orthogonalEdgeStyle`` edge with NO explicit waypoints renders
    # straight ONLY when its endpoints' centers share an axis (centerX≈centerX
    # OR centerY≈centerY). Otherwise draw.io inserts a visible 90° kink through
    # empty space. Edges the emitter already routed (explicit waypoints) or
    # docked (explicit exit/entry ports) are authorial and skipped — our own
    # engine always emits one or the other, so this never fires on our demos.
    def _center(cid: str) -> tuple[float, float] | None:
        cell = cells_by_id.get(cid)
        if cell is None:
            return None
        geom = cell.find("mxGeometry")
        if geom is None:
            return None
        try:
            w = float(geom.get("width", "0") or 0)
            h = float(geom.get("height", "0") or 0)
        except ValueError:
            return None
        x, y = _absolute_pos(cid)
        return (x + w / 2.0, y + h / 2.0)

    for cell in cells:
        if cell.get("edge") != "1":
            continue
        kv = _parse_style(cell.get("style") or "")
        if kv.get("edgeStyle") != "orthogonalEdgeStyle":
            continue
        if _edge_has_waypoints(cell) or _edge_has_anchor(kv):
            continue
        src = cell.get("source")
        tgt = cell.get("target")
        if not src or not tgt:
            continue
        cs = _center(src)
        ct = _center(tgt)
        if cs is None or ct is None:
            continue  # orphan endpoints already flagged by Rule 4
        dx = cs[0] - ct[0]
        dy = cs[1] - ct[1]
        if abs(dx) > _AXIS_ALIGN_TOL and abs(dy) > _AXIS_ALIGN_TOL:
            issues.append(
                Issue(
                    "CRITICAL",
                    "EDGE_BENT",
                    "orthogonal edge with no waypoints will kink 90° — "
                    f"source/target centers align on neither axis "
                    f"(Δx={dx:.0f}px, Δy={dy:.0f}px); align an axis or emit an "
                    "explicit orthogonal waypoint",
                    cell.get("id"),
                )
            )

    # ── Rule 7: edge label without labelBackgroundColor ──────────────────────
    # A labelled edge crossing the #EBF8FF BTP zone fill needs an opaque label
    # backing or the text bleeds into the fill and becomes unreadable.
    for cell in cells:
        if cell.get("edge") != "1":
            continue
        if not (cell.get("value") or "").strip():
            continue
        kv = _parse_style(cell.get("style") or "")
        if not kv.get("labelBackgroundColor"):
            issues.append(
                Issue(
                    "WARNING",
                    "EDGE_LABEL_BG",
                    "edge has a label but no labelBackgroundColor=default — the "
                    "text will bleed into the #EBF8FF zone fill",
                    cell.get("id"),
                )
            )

    # ── Rule 8: arcSize without absoluteArcSize=1 ────────────────────────────
    # Without absoluteArcSize=1, arcSize is a PERCENTAGE of the shape, so a
    # 700px-wide rounded zone with arcSize=16 gets a 112px radius instead of
    # 16px. The capsule pill idiom (rounded=1;arcSize=50) is intentionally a
    # percentage and is exempt (see _is_capsule_arc).
    for cell in cells:
        kv = _parse_style(cell.get("style") or "")
        if "arcSize" not in kv or kv.get("absoluteArcSize") == "1":
            continue
        if _is_capsule_arc(kv):
            continue
        issues.append(
            Issue(
                "WARNING",
                "ARC_SIZE_ABS",
                f"arcSize={kv.get('arcSize')} without absoluteArcSize=1 is "
                "treated as a percentage of the shape — a large rounded box "
                "renders an oversized radius; add absoluteArcSize=1",
                cell.get("id"),
            )
        )

    return issues


# ── Autofix (--fix) ──────────────────────────────────────────────────────────
# Only the mechanical, geometry-free repairs are safe to apply automatically:
#   • add labelBackgroundColor=default to a labelled edge missing it
#   • add absoluteArcSize=1 where arcSize is set on a NON-capsule shape
# Bent edges are deliberately NOT auto-fixed here — moving geometry belongs in
# the layout engine, not a text rewrite. Operates on each <mxCell> start tag as
# a unit so formatting is otherwise preserved verbatim.
_MXCELL_TAG_RE = re.compile(r"<mxCell\b[^>]*?/?>")


def _fix_cell_tag(tag: str, stats: dict[str, int]) -> str:
    m_style = re.search(r'style="([^"]*)"', tag)
    if not m_style:
        return tag
    style = m_style.group(1)
    kv = _parse_style(style)
    additions: list[str] = []

    if "arcSize" in kv and kv.get("absoluteArcSize") != "1" and not _is_capsule_arc(kv):
        additions.append("absoluteArcSize=1")
        stats["absoluteArcSize"] += 1

    if re.search(r'\bedge="1"', tag):
        m_val = re.search(r'\bvalue="([^"]*)"', tag)
        value = html.unescape(m_val.group(1)).strip() if m_val else ""
        if value and not kv.get("labelBackgroundColor"):
            additions.append("labelBackgroundColor=default")
            stats["labelBackgroundColor"] += 1

    if not additions:
        return tag
    sep = "" if (style == "" or style.endswith(";")) else ";"
    new_style = style + sep + ";".join(additions) + ";"
    return tag[: m_style.start(1)] + new_style + tag[m_style.end(1) :]


def apply_fixes(text: str) -> tuple[str, dict[str, int]]:
    stats = {"absoluteArcSize": 0, "labelBackgroundColor": 0}
    fixed = _MXCELL_TAG_RE.sub(lambda m: _fix_cell_tag(m.group(0), stats), text)
    return fixed, stats


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
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Repair mechanical issues in place (labelBackgroundColor on labelled "
        "edges; absoluteArcSize=1 on non-capsule arcSize shapes). Backs up to .bak.",
    )
    args = parser.parse_args(argv)

    path = Path(args.path)
    if not path.exists():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 2

    if args.fix:
        original = path.read_text(encoding="utf-8")
        fixed, stats = apply_fixes(original)
        total = sum(stats.values())
        if total == 0:
            print(f"{path}: no mechanical fixes needed")
        else:
            shutil.copyfile(path, path.with_suffix(path.suffix + ".bak"))
            path.write_text(fixed, encoding="utf-8")
            summary = ", ".join(f"{k}={v}" for k, v in stats.items() if v)
            print(f"{path}: applied {total} fix(es) — {summary}; backup at {path.name}.bak")
        return 0

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
