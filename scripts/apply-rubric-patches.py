#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""
apply-rubric-patches.py — merge visual-rubric findings into an IR's layoutHints.

The SKILL's Step 8 vision loop (see
``skills/sap-diagram-generate/references/visual-rubric.md``) renders a diagram,
has a vision-capable Claude walk the 26 binary checks against the PNG, and emit
**findings JSON**: one object per failing check, shaped
``{rule, location, patch}``. This script is the mechanical half of that loop —
it takes the findings, validates every ``patch`` against the fixed 7-op
vocabulary, and MERGES the patch objects into the IR's ``diagram.layoutHints``
array, ready to hand back to generate-drawio.py for a regenerate.

Usage:
    python3 apply-rubric-patches.py <ir.json> --findings <findings.json> [--out <ir.json>]

``--out`` defaults to the input path (in-place). ``-`` reads/writes stdio.

Findings are a JSON list; each element is either a ``{rule, location, patch}``
object (the canonical rubric shape) or a bare patch object (``{op, ...}``).
A ``patch`` of ``null`` is a **manual** finding (its fix isn't a layout hint —
recolor, legend content, …): it is passed through untouched, never an error and
never a hint (see the rubric's "Manual findings").

Patch-op vocabulary (the ENTIRE contract — an eighth op is a hard error):
    {"op":"set_group_flow","group":"<id>","value":"row|col|grid"}
    {"op":"set_zone","group":"<id>","value":"left|center|right"}
    {"op":"order_override","group":"<id>","value":["nid","nid",...]}
    {"op":"nudge_label","edge":"<id>","value":"next-slot"}
    {"op":"channel_prefer","edge":"<id>","value":"<channel-id>"}
    {"op":"set_icon_size","value":"S|M|L"}
    {"op":"toggle_separator","value":true|false}

Idempotent: applying the same findings twice yields the same IR. Hints are
de-duplicated by ``(op, target)`` — where target is the group id, the edge id,
or (for the two global ops) nothing — and a later hint for the same key
supersedes an earlier one's VALUE while keeping its position (so a re-run is
byte-stable).

Exit codes (mirroring validate-ir.py's style):
    0 — patches applied, IR written. Prints a one-line summary to stderr.
    2 — an unknown op or malformed patch. Prints one
        ``ERROR <where>: <what>. Allowed: <ops>`` line per problem.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# ── The fixed 7-op vocabulary (order matches visual-rubric.md's table) ────────
ALLOWED_OPS = (
    "set_group_flow",
    "set_zone",
    "order_override",
    "nudge_label",
    "channel_prefer",
    "set_icon_size",
    "toggle_separator",
)

_FLOW_VALUES = ("row", "col", "grid")
_ZONE_VALUES = ("left", "center", "right")
_ICON_VALUES = ("S", "M", "L")


class PatchError(Exception):
    """One actionable patch-validation failure.

    Renders as ``ERROR <where>: <what>.`` plus an ``Allowed: <...>`` suffix
    whenever there is a fixed vocabulary to point at — the exact format
    validate-ir.py's ``IRError`` uses, so the two tools read alike."""

    def __init__(self, where: str, what: str, allowed: list[str] | None = None):
        self.where = where
        self.what = what
        self.allowed = allowed
        super().__init__(str(self))

    def __str__(self) -> str:
        msg = f"ERROR {self.where}: {self.what}."
        if self.allowed is not None:
            msg += f" Allowed: {','.join(self.allowed)}"
        return msg


def _require(cond: bool, where: str, what: str, allowed: list[str] | None = None) -> None:
    if not cond:
        raise PatchError(where, what, allowed)


def validate_patch(patch: Any, where: str) -> None:
    """Validate one patch object against the 7-op vocabulary. Raises
    ``PatchError`` on anything malformed; returns ``None`` when valid."""
    _require(isinstance(patch, dict), where,
             f"patch must be an object (got {type(patch).__name__})")
    op = patch.get("op")
    _require(op in ALLOWED_OPS, where,
             f"unknown op {op!r}", list(ALLOWED_OPS))

    if op == "set_group_flow":
        _require(isinstance(patch.get("group"), str) and patch["group"], where,
                 "set_group_flow requires a 'group' id")
        _require(patch.get("value") in _FLOW_VALUES, where,
                 f"set_group_flow value {patch.get('value')!r} not recognized",
                 list(_FLOW_VALUES))
    elif op == "set_zone":
        _require(isinstance(patch.get("group"), str) and patch["group"], where,
                 "set_zone requires a 'group' id")
        _require(patch.get("value") in _ZONE_VALUES, where,
                 f"set_zone value {patch.get('value')!r} not recognized",
                 list(_ZONE_VALUES))
    elif op == "order_override":
        _require(isinstance(patch.get("group"), str) and patch["group"], where,
                 "order_override requires a 'group' id")
        val = patch.get("value")
        _require(isinstance(val, list) and all(isinstance(x, str) for x in val),
                 where, "order_override value must be a list of node ids")
    elif op == "nudge_label":
        _require(isinstance(patch.get("edge"), str) and patch["edge"], where,
                 "nudge_label requires an 'edge' id")
        _require(patch.get("value") == "next-slot", where,
                 f"nudge_label value {patch.get('value')!r} not recognized",
                 ["next-slot"])
    elif op == "channel_prefer":
        _require(isinstance(patch.get("edge"), str) and patch["edge"], where,
                 "channel_prefer requires an 'edge' id")
        _require(isinstance(patch.get("value"), str) and patch["value"], where,
                 "channel_prefer requires a 'value' channel id (e.g. V0, V1, Htop, Hbot)")
    elif op == "set_icon_size":
        _require(patch.get("value") in _ICON_VALUES, where,
                 f"set_icon_size value {patch.get('value')!r} not recognized",
                 list(_ICON_VALUES))
    elif op == "toggle_separator":
        _require(isinstance(patch.get("value"), bool), where,
                 "toggle_separator value must be true or false")


def _patch_key(patch: dict) -> tuple:
    """Identity of a hint for dedupe/supersede: ``(op, target)``. The two
    global ops (icon size, separator) key on the op alone (only one can be in
    effect); group ops key on the group id, edge ops on the edge id. The VALUE
    is deliberately NOT part of the key, so a later hint for the same target
    supersedes an earlier one's value."""
    op = patch["op"]
    if op in ("set_group_flow", "set_zone", "order_override"):
        return (op, patch.get("group"))
    if op in ("nudge_label", "channel_prefer"):
        return (op, patch.get("edge"))
    return (op,)


def _collect_patches(findings: Any) -> list[dict]:
    """Extract the non-null patch objects from a findings list, in order.

    Each finding is either a ``{rule, location, patch}`` wrapper or a bare
    patch (``{op, ...}``). A finding whose ``patch`` is ``null`` is a manual
    finding — passed through (dropped from the patch stream), never an error.
    Every extracted patch is validated before it is returned."""
    _require(isinstance(findings, list), "findings",
             f"findings must be a JSON list (got {type(findings).__name__})")
    patches: list[dict] = []
    for i, f in enumerate(findings):
        where = f"finding[{i}]"
        _require(isinstance(f, dict), where,
                 f"finding must be an object (got {type(f).__name__})")
        if "patch" in f:
            patch = f["patch"]
            if patch is None:            # manual finding — pass through
                continue
        elif "op" in f:                  # a bare patch object
            patch = f
        else:
            raise PatchError(where, "finding has neither a 'patch' nor an 'op' key")
        validate_patch(patch, where)
        # normalise to the canonical hint object (drop any extra finding keys)
        patches.append({k: patch[k] for k in ("op", "group", "edge", "value") if k in patch})
    return patches


def _merge(existing: list[dict], patches: list[dict]) -> list[dict]:
    """Merge ``patches`` into ``existing`` layoutHints, idempotently. Keyed by
    ``_patch_key``: an existing key keeps its POSITION but takes the newer
    VALUE; an unseen key is appended in encounter order. Re-running with the
    same inputs is therefore byte-stable."""
    order: list[tuple] = []
    by_key: dict[tuple, dict] = {}
    for h in list(existing) + list(patches):
        if not isinstance(h, dict) or "op" not in h:
            continue
        key = _patch_key(h)
        if key not in by_key:
            order.append(key)
        by_key[key] = h
    return [by_key[k] for k in order]


def apply(ir: dict, findings: Any) -> dict:
    """Merge ``findings`` into ``ir['layoutHints']`` and return the IR (mutated
    in place and also returned). Raises ``PatchError`` on any bad patch."""
    patches = _collect_patches(findings)
    existing = ir.get("layoutHints") or []
    ir["layoutHints"] = _merge(existing, patches)
    return ir


# ── CLI ──────────────────────────────────────────────────────────────────────
def _read_json(path: str) -> Any:
    if path == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(obj: Any, path: str) -> None:
    text = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    if path == "-":
        sys.stdout.write(text)
    else:
        Path(path).write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Merge visual-rubric findings into an IR's layoutHints."
    )
    parser.add_argument("ir", help="Path to the IR JSON ('-' for stdin).")
    parser.add_argument("--findings", required=True,
                        help="Path to the findings JSON ('-' for stdin).")
    parser.add_argument("--out", default=None,
                        help="Output path ('-' for stdout). Default: in place.")
    args = parser.parse_args(argv)

    try:
        ir = _read_json(args.ir)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR {args.ir}: cannot read IR: {exc}.", file=sys.stderr)
        return 2
    try:
        findings = _read_json(args.findings)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR {args.findings}: cannot read findings: {exc}.", file=sys.stderr)
        return 2

    if not isinstance(ir, dict):
        print(f"ERROR {args.ir}: IR root must be a JSON object.", file=sys.stderr)
        return 2

    try:
        before = len(ir.get("layoutHints") or [])
        apply(ir, findings)
    except PatchError as exc:
        print(str(exc))
        return 2

    out = args.out or args.ir
    _write_json(ir, out)
    after = len(ir.get("layoutHints") or [])
    print(f"✅ layoutHints: {before} → {after} "
          f"(wrote {'stdout' if out == '-' else out})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
