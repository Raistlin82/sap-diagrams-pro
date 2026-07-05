#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""
validate-ir.py — validate a SAP diagram JSON intermediate representation (IR)
against the grammar accepted by generate-drawio.py (v1 fields + the IR v2
additions from Task 4: subaccount/governance/cloud-tier/custom-app groups,
product/chip/db nodes with capabilities, edge pill/flowFamily, metadata
branding/badges).

It reuses generate-drawio.py's own `parse_json` (imported via the same
dashed-module technique tests/conftest.py's `load_script` uses, so both
scripts always agree on what "parses" means) and then re-checks everything
`parse_json` deliberately leaves unchecked because it is a *new*, optional
v2 field: enum membership, group `parent` references/cycles, and capability
shape. v1 fields that `parse_json` itself already validates (edge `style`,
`kind`, `pillColor`) surface here too — any parse failure is reported the
same way, so this tool is a strict superset of "does it parse".

Usage:
    python3 validate-ir.py path/to/diagram.json

Exit codes:
    0 — IR is valid. Prints "OK".
    2 — IR is invalid or unreadable. Prints one "ERROR <where>: <what>."
        line per problem (with an ", Allowed: <v1,v2,...>" suffix whenever
        there is a fixed vocabulary to point at).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent


def _load_sibling(name: str):
    """Import a scripts/ module even when its filename contains dashes.

    Mirrors tests/conftest.py's `load_script` exactly (memoize via
    sys.modules first, only then exec from an explicit file path) so that
    whichever caller imports "generate-drawio" first — this CLI running
    standalone, or a test harness calling `load_script("generate-drawio")`
    directly — the other reuses the SAME module object. Re-executing the
    module a second time would clobber sys.modules and break dataclass
    identity (isinstance checks) between the two call sites.
    """
    mod_name = name.replace("-", "_")
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


_gen = _load_sibling("generate-drawio")
parse_json = _gen.parse_json
Diagram = _gen.Diagram
Group = _gen.Group


# ─────────────────────────────────────────────────────────────────────────────
# Allowed-value vocabulary.
#
# Kept in sync BY HAND with assets/style-contract.json's molecule keys (30
# entries, including the subaccount/governance/cloud-tier/custom-app group
# molecules and the product node molecule) — intentionally NOT read from the
# contract at runtime here; a later task wires that cross-check. See the
# Task 4 plan notes for the rationale.
# ─────────────────────────────────────────────────────────────────────────────
V1_GROUP_TYPES = {"user", "third-party", "btp-layer", "sap-app", "non-sap", "external"}
V2_GROUP_TYPES = {"subaccount", "governance", "cloud-tier", "custom-app"}
ALLOWED_GROUP_TYPES = V1_GROUP_TYPES | V2_GROUP_TYPES

ALLOWED_CLOUD_TIER_KINDS = {"public", "private", "any-premise"}
ALLOWED_NODE_TYPES = {"product", "chip", "db"}
ALLOWED_FLOW_FAMILIES = {"identity", "provisioning", "master-data", "transport", "default"}

CAPABILITY_SHAPE = "{label: str, icon?: str}"


class IRError(Exception):
    """One actionable validation failure.

    Renders as ``ERROR <where>: <what>.`` plus an ``Allowed: <v1,v2,...>``
    suffix whenever `allowed` is given — the format the Task 4 plan requires
    ("printing `ERROR <where>: <what>. Allowed: <values>`").
    """

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


def _check_enum(
    errors: list[IRError], where: str, field: str, value: str | None, allowed: set[str]
) -> None:
    """Validate an optional enum field. None (field absent/not applicable)
    always passes — the field is optional by IR v2 contract; only a value
    that IS present and NOT in the allowed set is an error."""
    if value is not None and value not in allowed:
        errors.append(IRError(where, f"{field} {value!r} not recognized", sorted(allowed)))


def _check_capabilities(errors: list[IRError], where: str, capabilities: Any) -> None:
    if capabilities is None:
        return
    if not isinstance(capabilities, list):
        errors.append(
            IRError(
                where,
                f"capabilities must be a list (got {type(capabilities).__name__})",
                [f"[{CAPABILITY_SHAPE}, ...]"],
            )
        )
        return
    for i, cap in enumerate(capabilities):
        cap_where = f"{where} capabilities[{i}]"
        if not isinstance(cap, dict):
            errors.append(
                IRError(cap_where, f"must be an object (got {type(cap).__name__})", [CAPABILITY_SHAPE])
            )
            continue
        label = cap.get("label")
        if not isinstance(label, str) or not label.strip():
            errors.append(
                IRError(cap_where, "missing required non-empty 'label'", [CAPABILITY_SHAPE])
            )
        if "icon" in cap and cap["icon"] is not None and not isinstance(cap["icon"], str):
            errors.append(
                IRError(
                    cap_where,
                    f"'icon' must be a string when present (got {type(cap['icon']).__name__})",
                    [CAPABILITY_SHAPE],
                )
            )


def _check_group_parents(errors: list[IRError], groups: list[Group]) -> None:
    """Group `parent` refs must point at an existing group id, and the
    parent chain must never cycle back on itself."""
    ids = {g.id for g in groups}
    parent_of = {g.id: g.parent for g in groups}

    for g in groups:
        if g.parent is not None and g.parent not in ids:
            errors.append(
                IRError(f"group {g.id!r}", f"parent {g.parent!r} does not exist", sorted(ids))
            )

    for g in groups:
        chain: list[str] = []
        cur: str | None = g.id
        while cur is not None:
            if cur in chain:
                cycle = " -> ".join(chain + [cur])
                errors.append(
                    IRError(
                        f"group {g.id!r}",
                        f"parent chain forms a cycle ({cycle})",
                        ["a non-cyclic parent chain"],
                    )
                )
                break
            chain.append(cur)
            nxt = parent_of.get(cur)
            if nxt is not None and nxt not in ids:
                break  # dangling ref already reported above; stop walking
            cur = nxt


def validate_diagram(diagram: Diagram) -> list[IRError]:
    """Walk an already-parsed Diagram and re-check everything `parse_json`
    leaves unchecked for the new, optional IR v2 fields."""
    errors: list[IRError] = []

    for g in diagram.groups:
        _check_enum(errors, f"group {g.id!r}", "type", g.type, ALLOWED_GROUP_TYPES)
        _check_enum(errors, f"group {g.id!r}", "kind", g.kind, ALLOWED_CLOUD_TIER_KINDS)
    _check_group_parents(errors, diagram.groups)

    for n in diagram.nodes:
        _check_enum(errors, f"node {n.id!r}", "type", n.type, ALLOWED_NODE_TYPES)
        _check_capabilities(errors, f"node {n.id!r}", n.capabilities)

    for e in diagram.edges:
        _check_enum(errors, f"edge {e.id!r}", "flowFamily", e.flowFamily, ALLOWED_FLOW_FAMILIES)

    return errors


def validate_payload(payload: dict[str, Any]) -> list[IRError]:
    """Parse + validate a raw IR dict. Parse failures (malformed structure,
    or a v1 field `parse_json` itself rejects, e.g. an unknown edge style)
    are reported as a single IRError rather than raising, so the CLI always
    exits cleanly with an actionable message."""
    try:
        diagram = parse_json(payload)
    except (KeyError, ValueError, TypeError) as exc:
        return [IRError("IR", f"failed to parse: {exc}")]
    return validate_diagram(diagram)


def _read_json(path: str) -> Any:
    if path == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a SAP diagram IR JSON file (v1 fields + IR v2 additions)."
    )
    parser.add_argument("path", help="Path to the IR JSON file ('-' for stdin).")
    args = parser.parse_args(argv)

    try:
        payload = _read_json(args.path)
    except OSError as exc:
        print(f"ERROR {args.path}: cannot read file: {exc}.", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"ERROR {args.path}: invalid JSON: {exc}.", file=sys.stderr)
        return 2

    errors = validate_payload(payload)
    if errors:
        for err in errors:
            print(str(err))
        return 2

    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
