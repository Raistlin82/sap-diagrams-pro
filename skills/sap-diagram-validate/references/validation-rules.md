<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Validation Rules — Catalogue

Source of truth: [`scripts/validate-drawio.py`](../../../scripts/validate-drawio.py). Whenever a rule is added or modified there, update this document accordingly.

## Severity model

| Severity | Meaning | Block submission? |
|---|---|---|
| **CRITICAL** | The diagram has a structural defect: unparseable XML, missing root, orphan edges referencing non-existent cells. | Yes — cannot be opened/used. |
| **WARNING** | The diagram opens but breaks an explicit guideline rule (off-palette colors, no title, missing required organism). | Yes for SAP Architecture Center submissions; may be acceptable for internal use. |
| **INFO** | Cosmetic / borderline observation (small overlap, missing dash pattern). | No — always acceptable; useful as polish hints. |

Exit codes (when `--strict` is passed): exit 1 if any CRITICAL is present.

## Rule catalogue

### CRITICAL rules

| Rule code | Trigger | Fix |
|---|---|---|
| `PARSE` | XML cannot be parsed (malformed file). | Re-export from draw.io desktop with the standard XML serializer. |
| `ROOT` | Root element is not `<mxfile>`. | The file is not a draw.io diagram — confirm the input. |
| `STRUCTURE` | No `<diagram>` element under `<mxfile>`. | The file is empty or corrupt. Recreate. |
| `ORPHAN_EDGE_SOURCE` | Edge references a `source` cell ID that doesn't exist in the diagram. | Reconnect or delete the edge. |
| `ORPHAN_EDGE_TARGET` | Edge references a `target` cell ID that doesn't exist. | Reconnect or delete the edge. |

### WARNING rules

| Rule code | Trigger | Fix |
|---|---|---|
| `PALETTE_BORDER` | An mxCell has `strokeColor` outside the Horizon border palette. | Use `#0070F2` (BTP), `#475E75` (non-SAP), or one of the semantic/accent borders. |
| `PALETTE_FILL` | An mxCell has `fillColor` outside the Horizon fill palette. | Use `#EBF8FF` (BTP), `#F5F6F7` (non-SAP), `#FFFFFF` (inner), or one of the semantic/accent fills. |
| `NO_TITLE` | No text cell with a `text;` style is found in the diagram. | Add a title cell with font size 18, color `#1D2D3E`, top-left of the canvas. |
| `EMPTY` | Diagram has no `mxCell` elements. | Diagram is empty — populate it. |

### INFO rules

| Rule code | Trigger | Fix (optional) |
|---|---|---|
| `PALETTE_TEXT` | An mxCell has `fontColor` outside the Horizon text palette (`#1D2D3E`, `#556B82`). | Switch to one of the standard text colors for consistency. |
| `EDGE_DASHED_NO_PATTERN` | An edge is dashed but no `dashPattern` is set, leading to a default pattern that may differ across renderers. | Set `dashPattern=8 4` (async) or `dashPattern=1 4` (optional). |
| `BOX_OVERLAP` | Two vertices overlap geometrically (and neither is fully contained in the other). | Reposition with ≥ 32px gap (the SAP-logo rule of thumb). |

## Rule design principles

When adding a new rule, follow these conventions:

1. **One rule, one concern.** Don't combine palette + spacing checks in one rule.
2. **Cell-level when possible.** Rules that can identify a specific offending cell ID help users navigate to the issue in draw.io.
3. **Severity discipline.**
   - CRITICAL ⇔ "diagram is broken or cannot be parsed".
   - WARNING ⇔ "guideline says do X, this does Y; user should fix".
   - INFO ⇔ "best-practice nudge or borderline cosmetic issue".
4. **Document the rationale.** Each rule must reference a specific section of the SAP guideline (or this file). No silent rules.
5. **Idempotent classification.** Running the validator twice on the same file must produce the exact same report.

## Future rules (not yet implemented)

These are tracked for future versions:

- `LEVEL_BUDGET` — warn if element count exceeds the budget for the declared level (L0 ≤ 10, L1 ≤ 30, L2 ≤ 80). Requires reading metadata.
- `MISSING_LEGEND` — warn if a diagram uses ≥ 2 line styles without a legend molecule.
- `INCONSISTENT_STROKE_WIDTH` — flag when the diagram uses more than 2 distinct stroke widths (1.5 normal + 4 for firewall is the only sanctioned pattern).
- `UNNAMED_BTP_SERVICE` — flag a node inside a `btp-layer` group that has no `service` mapping in `shape-index.json` and no explicit label match.
- `MULTI_PAGE_NOT_LABELLED` — warn if a multi-page `.drawio` has any unnamed page.

## Running the validator manually

```bash
# Plain text report
python3 scripts/validate-drawio.py path/to/diagram.drawio

# Strict mode (exit 1 on CRITICAL)
python3 scripts/validate-drawio.py path/to/diagram.drawio --strict

# Machine-readable JSON
python3 scripts/validate-drawio.py path/to/diagram.drawio --json
```

CI integration example (GitHub Actions step):

```yaml
- name: Validate SAP diagrams
  run: |
    for f in diagrams/*.drawio; do
      python3 ./.claude/plugins/sap-diagrams-pro/scripts/validate-drawio.py "$f" --strict
    done
```

## Limitations

- **Renderer-specific styles** are not validated. drawio supports many style attributes the validator ignores (e.g. `glass=1`, `shadow=1`). The guideline doesn't sanction these but doesn't forbid them either; we treat them as out-of-scope.
- **Custom shape libraries** are not detected. If you embedded a non-SAP icon (e.g. PostgreSQL elephant), the validator only checks color compliance, not icon semantics.
- **Cross-page consistency** is not checked. Multi-page diagrams are validated page-by-page in isolation.
- **Group containment** is not strictly enforced. The plugin's generator places groups visually but doesn't use draw.io's `parent` mechanism for membership; the validator is permissive about this.
