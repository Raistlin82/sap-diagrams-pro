---
name: sap-diagram-validate
description: Validate an existing .drawio file against the SAP BTP Solution Diagram Guideline. Reports CRITICAL / WARNING / INFO issues covering Horizon palette, line styles, atomic-design conventions, spacing, edge integrity, and labelling. Use when the user provides an existing diagram for review, asks to check a draw.io file, validate a SAP architecture diagram, lint a solution diagram, or check compliance.
argument-hint: "<path/to/diagram.drawio> [--strict] [--json]"
allowed-tools: Read, Bash, Glob, Grep
version: 0.1.0
---

# Validate a SAP-Compliant Architecture Diagram

Inspect an existing `.drawio` file and produce a compliance report against the [SAP BTP Solution Diagram Guideline](https://sap.github.io/btp-solution-diagrams/) (Horizon palette + atomic design + line-style conventions + spacing rules).

## When to invoke this skill

Trigger on user requests like:

- "Validate this diagram: …"
- "Check if my drawio is SAP-compliant"
- "Lint my architecture diagram"
- "Is this diagram ready for SAP Architecture Center?"
- "Does this follow the Horizon palette?"

Do **not** invoke for: generating new diagrams (use `sap-diagram-generate`), editing diagrams (the validator is read-only), or fixing reported issues automatically (the user must edit).

## Inputs

- **Path** to the `.drawio` file to validate. Required.
- **`--strict`** flag (optional) — exit code 1 if any CRITICAL issue is found. Useful in CI pipelines.
- **`--json`** flag (optional) — emit machine-readable JSON instead of human-friendly text.

## Procedure

### Step 1 — Verify the file exists and is readable

```bash
test -f "<path>" || echo "ERROR: file not found"
```

If the path is a URL or a `gh` reference, ask the user to download / clone first — the validator reads local files only.

### Step 2 — Run the validator

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/validate-drawio.py" "<path>"
```

Add `--strict` and/or `--json` per the user's request.

### Step 3 — Interpret the report

The validator emits issues in three severity levels:

- **CRITICAL** — structural problems that prevent a diagram from being SAP-compliant (orphan edges, unparseable XML, missing root element).
- **WARNING** — guideline violations (off-palette colors, missing title, inconsistent stroke widths) that should be fixed before publication.
- **INFO** — cosmetic / borderline issues (tiny overlaps, dashed without explicit pattern, font color in non-standard text palette).

Validation rules are documented in [`references/validation-rules.md`](references/validation-rules.md).

### Step 4 — Report to the user

Present the validator's output verbatim, then add an interpretation:

- **0 CRITICAL, 0 WARNING** → "✅ SAP-compliant. Ready for proposals / blogs / SAP Architecture Center submission."
- **0 CRITICAL, 1-3 WARNING** → "🟡 Mostly compliant. Fix the warnings before submitting to SAP Architecture Center."
- **0 CRITICAL, 4+ WARNING** → "🟠 Compliance gaps. Worth running through `sap-diagram-generate` from scratch."
- **1+ CRITICAL** → "❌ Structural issue. Cannot be opened or parsed correctly. See specific cell IDs in the report."

For each WARNING / CRITICAL, suggest the fix:

| Rule | Fix suggestion |
|---|---|
| `PALETTE_BORDER` | Replace stroke color with `#0070F2` (BTP) or `#475E75` (non-SAP). See [`horizon-palette.md`](../sap-diagram-generate/references/horizon-palette.md). |
| `PALETTE_FILL` | Replace fill with `#EBF8FF` (BTP), `#F5F6F7` (non-SAP), or `#FFFFFF` (inner element). |
| `NO_TITLE` | Add a text cell with the diagram title at the top-left, font size 18, color `#1D2D3E`. |
| `ORPHAN_EDGE_*` | Either reconnect the edge to a valid cell or delete it. |
| `BOX_OVERLAP` | Reposition vertices so they don't overlap. Maintain ≥ 32px gap between organism boundaries. |
| `EDGE_DASHED_NO_PATTERN` | Set `dashPattern=8 4` (async) or `dashPattern=1 4` (optional) on the edge style. |

### Step 5 — Offer next steps

- If the user wants to fix issues, suggest opening the file in draw.io desktop or [drawio.com](https://drawio.com) and pointing to the cell IDs reported.
- If the user wants a clean rebuild, suggest invoking `sap-diagram-generate` with the same description.
- If the user asks for auto-fix, **decline** — auto-fix is not implemented in v0.1 (regeneration is the safer path).

## Configuration

Read project-local settings from `.claude/sap-diagrams-pro.local.md`:

- `validation_strictness` — default `informational`. If set to `strict`, treat every WARNING as critical for exit-code purposes.

Command-line `--strict` flag overrides the config setting.

## References

- [`references/validation-rules.md`](references/validation-rules.md) — exhaustive catalogue of validation rules with rationale.
- [`../sap-diagram-generate/references/horizon-palette.md`](../sap-diagram-generate/references/horizon-palette.md) — palette reference.
- [`../sap-diagram-generate/references/line-styles-spacing.md`](../sap-diagram-generate/references/line-styles-spacing.md) — line-style + spacing reference.

## Quality bar

A "good" validation run:

- Always invokes the standalone Python validator (does not re-implement rules in skill markdown).
- Always reports cell IDs for actionable issues.
- Always classifies issues by severity.
- Provides a 1-line interpretation of overall compliance ("ready" / "fix warnings" / "structural issue").
- Suggests fixes inline (mapping rule → fix).
- Does not auto-modify the user's file.

If any of these are missed, redo the report.
