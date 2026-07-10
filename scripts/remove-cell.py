#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""Surgically remove a cell (and everything it holds) from a scaffolded
``.drawio`` — part of the hybrid *scaffold* path's edit toolkit.

Removing a single cell from a real SAP template is never just one cell: a
container carries children, and any node has edges wired to it. Deleting the
target alone would leave orphaned children floating and edges dangling into
nothing. So this removes the *transitive closure* of the target:

  1. the target itself (addressed by ``--id`` or by visible label ``--match``),
  2. every descendant — any cell whose ``parent`` chain reaches the target,
  3. every edge whose ``source`` or ``target`` points at anything removed above
     (repeated to a fixpoint, so an edge-to-edge chain can't leave a dangling
     stub behind).

The target must resolve BEFORE anything is written; an unknown id/label exits
non-zero and touches no files. On success the graph is saved via
:func:`_drawio_edit.save`, which first backs up the prior file to ``<file>.bak``.

Usage:
  remove-cell.py diagram.drawio --id node-7
  remove-cell.py diagram.drawio --match "SAP Build Apps"
  remove-cell.py diagram.drawio --id grp-2 --json

Exit codes:
  0 — removed
  1 — error (parse/IO failure)
  2 — usage (missing file, or target id/label not found)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load_edit():
    """Import the shared ``_drawio_edit`` helper the repo's guarded, path-based
    way (check ``sys.modules`` first, then ``spec_from_file_location``) so this
    process and the test harness share one module identity."""
    if "_drawio_edit" in sys.modules:
        return sys.modules["_drawio_edit"]
    spec = importlib.util.spec_from_file_location(
        "_drawio_edit", _HERE / "_drawio_edit.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_drawio_edit"] = mod
    spec.loader.exec_module(mod)
    return mod


edit = _load_edit()


def _descendants(doc: ET.ElementTree, root_id: str) -> set[str]:
    """``root_id`` plus every cell reachable through the ``parent`` attribute."""
    found = {root_id}
    stack = [root_id]
    while stack:
        cur = stack.pop()
        for child in edit.children(doc, cur):
            cid = child.get("id")
            if cid and cid not in found:
                found.add(cid)
                stack.append(cid)
    return found


def _with_incident_edges(doc: ET.ElementTree, removal: set[str]) -> set[str]:
    """Grow ``removal`` to include every edge wired to a removed cell.

    Repeats to a fixpoint so edges pointing at other soon-to-be-removed edges
    are swept up too — no dangling ``source``/``target`` can survive.
    """
    changed = True
    while changed:
        changed = False
        for cell in edit.iter_cells(doc):
            cid = cell.get("id")
            if not cid or cid in removal:
                continue
            if cell.get("source") in removal or cell.get("target") in removal:
                removal.add(cid)
                changed = True
    return removal


def remove_target(doc: ET.ElementTree, target: ET.Element) -> list[str]:
    """Remove ``target``, its subtree and all incident edges. Returns the sorted
    ids removed."""
    removal = _descendants(doc, target.get("id"))
    removal = _with_incident_edges(doc, removal)
    for cell in list(edit.iter_cells(doc)):
        if cell.get("id") in removal:
            edit.remove_cell_element(doc, cell)
    return sorted(removal)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("drawio", type=Path)
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--id", dest="cell_id", metavar="CELL_ID",
                     help="remove the cell with this exact id")
    grp.add_argument("--match", dest="label", metavar="LABEL",
                     help="remove the first cell with this visible label")
    ap.add_argument("--json", action="store_true",
                    help="print {\"removed\": [ids]} to stdout")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if not args.drawio.exists():
        print(f"{args.drawio}: file not found", file=sys.stderr)
        return 2

    try:
        doc = edit.load(args.drawio)
    except (ET.ParseError, OSError) as exc:
        print(f"remove-cell failed to parse {args.drawio}: {exc}", file=sys.stderr)
        return 1

    if args.cell_id is not None:
        target = edit.find_cell(doc, args.cell_id)
        if target is None:
            print(f"no cell with id {args.cell_id!r}", file=sys.stderr)
            return 2
    else:
        target = edit.find_cell_by_label(doc, args.label)
        if target is None:
            print(f"no cell with label {args.label!r}", file=sys.stderr)
            return 2

    removed = remove_target(doc, target)

    try:
        edit.save(doc, args.drawio)
    except OSError as exc:
        print(f"remove-cell failed to write {args.drawio}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"removed": removed}))
    else:
        print(f"remove-cell: {len(removed)} cell(s) removed from {args.drawio} "
              f"({', '.join(removed)})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
