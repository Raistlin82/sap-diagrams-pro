#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""Surgical label edits on a scaffolded ``.drawio`` — the third step of the
hybrid *scaffold* path.

This is how the scaffold path adapts a copied SAP template without redrawing it:
change the visible text of cells while preserving every cell's geometry, style,
id, parent and the overall XML structure. Two edit modes (combine freely):

  --set <cellId>=<new label>    address a cell by its ``id`` (exact).
  --replace "<old>=<new>"       visible-text match: any cell whose *rendered*
                                label (HTML stripped, <br> → space, entities
                                unescaped, whitespace collapsed) equals <old>.

Both preserve a single simple inline wrapper (``<b>/<i>/<u>/<font>/<span>``) so
colour/formatting survive a text swap. Multiple ``--set`` / ``--replace`` flags
apply in order. Writes in place, saving a ``.bak`` of the original first (use
``--out`` to write elsewhere and skip the backup, or ``--no-backup``).

Only the ``value`` (and legacy ``label``) attribute of ``mxCell`` / ``object``
elements is touched; ``mxGeometry``, ``style``, ``id``, ``source``/``target``
and everything else are left byte-for-byte as parsed.

Usage:
  relabel.py diagram.drawio --set node-3="SAP Build Apps"
  relabel.py diagram.drawio --replace "S/4HANA=S/4HANA Cloud" --replace "Old=New"
  relabel.py diagram.drawio --set id7="X" --out adapted.drawio
"""
from __future__ import annotations

import argparse
import html
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

# Attributes that can carry a visible label, in priority order.
LABEL_ATTRS = ("value", "label")

_BR_RE = re.compile(r"<br\s*/?>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_SIMPLE_WRAP_RE = re.compile(
    r"(?P<prefix><(?P<tag>b|i|u|font|span)\b[^>]*>)(?P<body>.*)(?P<suffix></(?P=tag)>)",
    re.I | re.S,
)


@dataclass(frozen=True)
class Change:
    cell_id: str
    attr: str
    old: str
    new: str
    matched_by: str


def clean_label(value: str) -> str:
    """Rendered/visible form of a draw.io label value."""
    if value is None:
        return ""
    txt = html.unescape(value)
    txt = _BR_RE.sub(" ", txt)
    txt = _TAG_RE.sub(" ", txt)
    txt = txt.replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", txt).strip()


def _as_drawio_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def set_inner(raw: str, new_value: str) -> str:
    """Replace the text of ``raw`` with ``new_value``, keeping a single simple
    inline wrapper (``<b>``/``<font …>``/…) if the whole value is wrapped."""
    replacement = _as_drawio_text(new_value)
    m = _SIMPLE_WRAP_RE.fullmatch(raw or "")
    if m:
        return f"{m.group('prefix')}{replacement}{m.group('suffix')}"
    return replacement


def parse_set(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or ():
        if "=" not in item:
            raise ValueError(f"--set expects <cellId>=<label>, got {item!r}")
        key, val = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"--set has an empty cell id: {item!r}")
        out[key] = val
    return out


def parse_replace(items: list[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for item in items or ():
        if "=" not in item:
            raise ValueError(f"--replace expects \"<old>=<new>\", got {item!r}")
        old, new = item.split("=", 1)
        old = clean_label(old.strip())
        if not old:
            raise ValueError(f"--replace has an empty <old>: {item!r}")
        out.append((old, new))
    return out


def apply_changes(root: ET.Element, id_map: dict[str, str],
                  replace_map: list[tuple[str, str]]) -> list[Change]:
    replace_lookup = dict(replace_map)
    changes: list[Change] = []
    for elem in root.iter():
        # An <object> wraps an mxCell and carries the label on itself; mxCell
        # carries it on `value`. Handle whichever element holds the attribute.
        attr = next((a for a in LABEL_ATTRS if elem.get(a) is not None), None)
        if attr is None:
            continue
        raw = elem.get(attr) or ""
        elem_id = elem.get("id") or ""

        new_value: str | None = None
        matched_by = ""
        if elem_id and elem_id in id_map:
            new_value = id_map[elem_id]
            matched_by = f"id:{elem_id}"
        else:
            visible = clean_label(raw)
            if visible and visible in replace_lookup:
                new_value = replace_lookup[visible]
                matched_by = f"label:{visible}"

        if new_value is None:
            continue
        updated = set_inner(raw, new_value)
        if updated == raw:
            continue
        elem.set(attr, updated)
        changes.append(Change(elem_id, attr, clean_label(raw),
                              clean_label(updated), matched_by))
    return changes


def relabel_file(source: Path, id_map: dict[str, str],
                 replace_map: list[tuple[str, str]],
                 destination: Path | None = None,
                 backup: bool = True) -> list[Change]:
    tree = ET.parse(source)
    changes = apply_changes(tree.getroot(), id_map, replace_map)
    target = destination or source
    if destination is None and backup:
        shutil.copyfile(source, source.with_suffix(source.suffix + ".bak"))
    tree.write(target, encoding="unicode", xml_declaration=False)
    return changes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("drawio", type=Path)
    ap.add_argument("--set", dest="sets", action="append", default=[],
                    metavar="ID=LABEL", help="set a cell's label by id (repeatable)")
    ap.add_argument("--replace", dest="replaces", action="append", default=[],
                    metavar="OLD=NEW", help="replace by visible label (repeatable)")
    ap.add_argument("-o", "--out", type=Path,
                    help="write to a new file (no .bak; leaves the source intact)")
    ap.add_argument("--no-backup", action="store_true",
                    help="in-place edit without writing a .bak")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if not args.sets and not args.replaces:
        print("nothing to do: pass --set and/or --replace", file=sys.stderr)
        return 2
    if not args.drawio.exists():
        print(f"{args.drawio}: file not found", file=sys.stderr)
        return 2

    try:
        id_map = parse_set(args.sets)
        replace_map = parse_replace(args.replaces)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        changes = relabel_file(args.drawio, id_map, replace_map,
                               destination=args.out, backup=not args.no_backup)
    except (ET.ParseError, OSError) as exc:
        print(f"relabel failed: {exc}", file=sys.stderr)
        return 1

    target = args.out or args.drawio
    print(f"relabel: {len(changes)} label(s) changed in {target}", file=sys.stderr)
    for c in changes:
        print(f"  {c.matched_by}: {c.old!r} -> {c.new!r}", file=sys.stderr)
    # Report requested edits that matched nothing, so silent typos surface.
    matched_ids = {c.cell_id for c in changes if c.matched_by.startswith("id:")}
    for cell_id in id_map:
        if cell_id not in matched_ids:
            print(f"  warning: --set id {cell_id!r} matched no cell", file=sys.stderr)
    matched_labels = {c.old for c in changes if c.matched_by.startswith("label:")}
    for old, _ in replace_map:
        if old not in matched_labels:
            print(f"  warning: --replace {old!r} matched no visible label", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
