<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Scaffold-and-Extend — design

## Problem

Today the hybrid decision (SKILL Step 5.5) is **binary**:

- **scaffold** — copy the closest SAP reference `.drawio` and adapt it by
  **relabel only** (`relabel.py` changes visible text; it cannot add or remove
  nodes/edges); or
- **generate** — author an IR and render from scratch with the procedural engine.

When a template covers *a good part but not all* of the requested components — or
carries a few components the user did not ask for — relabel-only is not enough, so
the engine falls back to **generate from scratch** and throws away a high-fidelity,
hand-laid SAP reference. That is the wrong trade-off: a template that covers most
of the scenario should be **used as the base and then extended/modified**, not
discarded.

Observed on a real run (Build Work Zone + S/4HANA PCE + Cloud ALM + BPA +
Integration Suite): the selector recommended `ra0024-joule-s4pce` (score 58), but
it carried Joule components the user did not request and lacked Cloud ALM, so the
run generated from scratch — losing the template's fidelity.

## Goal

Add a third path, **scaffold-and-extend**: take the closest template as the base,
then **remove** out-of-scope components, **relabel** the matches, and **add** the
missing components — reaching the exact confirmed inventory while inheriting the
template's real SAP layout, palette, fonts and icons. Clean up with the existing
gate + visual-rubric loop.

### Non-goals

- No change to the procedural engine core (`_skeleton_layout.py`,
  `_channel_router.py`, `_molecules.py`, `generate-drawio.py`). The committed demos
  (NOVA L0/L1/L2, gold Task Center) MUST regenerate byte-identical.
- No full re-route of a scaffolded template (that would erase its hand-made
  routing — the very thing scaffolding preserves).
- No automatic re-layout of the whole template (defeats the purpose).

## A. Coverage report + decision (`select-template.py`, SKILL Step 5.5)

`select-template.py` gains a **coverage report** for the top candidate against the
confirmed component list (passed as `--components "<a>,<b>,…"`, the canonical names
from the interview). Using the same word-boundary matching as the selector, it
classifies:

- **PRESENT** — requested components matched in the template's `serviceTokens` /
  `labelTokens` / `scenarioAliases`.
- **MISSING** — requested components not found in the template → must be ADDED.
- **EXTRA** — template components not requested → candidates to REMOVE, each tagged
  **light** (a single node/leaf cell) or **heavy** (a whole zone / `subaccount` /
  frame with children).

`coverage = |PRESENT| / |requested|`.

**Decision (Step 5.5), in order:**

1. **scaffold-and-extend** when the top candidate is `★ recommended` (score ≥
   `RECOMMEND_THRESHOLD` = 14) **AND** `coverage ≥ COVERAGE_MIN` (default **0.4**)
   **AND** the extras are not dominated by *heavy* structural mismatches (heuristic:
   no more than `HEAVY_EXTRA_MAX` = 1 heavy extra zone, or heavy extras ≤ ⅓ of the
   template's zones). Emit an explicit **delta plan**: `REMOVE […]`,
   `RELABEL/KEEP […]`, `ADD […]`.
2. **scaffold (relabel-only)** when coverage is ~1.0 and MISSING is empty and no
   heavy extras — the current pure path.
3. **generate** otherwise (no template clears the bar) — the current procedural
   path.

The thresholds live as named constants (tunable); the report is printed
human-readably and available as `--json`.

## B. Edit tools

All three are stdlib-only, edit a `.drawio` **in place** with a `.bak`, print what
they changed, and **reuse the engine's own style-contract / shape-index / geometry
helpers** so edited elements are indistinguishable from generated ones. They are
new files under `scripts/`; they do not modify the engine core.

### `remove-cell.py`
`remove-cell.py <file> (--id <cellId> | --match "<label>") [--json]`

- Removes the target cell. If it is a container (a frame/group/subaccount — has
  children via `parent`), removes the whole subtree.
- Removes any edge whose `source` or `target` was removed (no dangling edges).
- Reports the removed cell ids. Errors (exit ≠ 0) on an unknown id/label.

### `add-node.py`
`add-node.py <file> --group <groupId> --label "<…>" [--service <name>] [--genericIcon <k>] [--subtitle "<…>"] [--type product|chip|db] [--capabilities "<a;b;c>"] --mode (append | slot) [--near <cellId>] [--json]`

Resolves the icon from `shape-index.json` (same resolver as the engine). Two
placement modes — **Claude chooses per case**:

- `--mode append` — insert as a child of `<group>` and **reflow only that group's
  children** (reuse the molecule packing / `footprint`), growing the group frame as
  needed. The rest of the template is untouched; sibling groups do not move unless
  the frame's growth would overlap them, in which case the tool shifts the immediate
  neighbour and reports it (bounded, one-level).
- `--mode slot --near <cellId>` — place the new node in the nearest free,
  grid-snapped rectangle next to `<cellId>` (10px grid; scan outward; reject any
  position overlapping an existing cell). Less invasive; may leave whitespace.

Returns the new cell id (for wiring edges).

### `add-edge.py`
`add-edge.py <file> --source <id> --target <id> [--flowFamily <f>] [--kind <k>] [--pill "<…>"] [--label "<…>"] [--style solid|dashed|dotted|thick] [--json]`

- Adds one edge with the correct style-contract style (colour/dash by
  `flowFamily`/`kind`), endpoints attached at sensible ports (nearest sides), and a
  **local orthogonal path** (a single elbow) — it does NOT invoke the full router.
- Places the optional pill/label off the NETWORK separator (reuse the router's
  `_sep_obstacle_rects` guard).

## C. SKILL workflow (Step 5.5 rewrite)

Three paths reach the same downstream gate (Step 8):

- **scaffold-and-extend** — `scaffold-diagram.py` the chosen template →
  `remove-cell.py` each EXTRA (heavy first) → `relabel.py` the RELABEL set →
  `add-node.py` + `add-edge.py` each MISSING component → gate (`validate-drawio` +
  `check-composition` + `score-diagram --corpus --min-score 82`) + visual-rubric
  loop (≤3) → deliver. On a gate failure, undo via `.bak` and retry the specific
  edit (max 2 mechanical retries), never hand-edit geometry.
- **scaffold** (pure relabel) — unchanged.
- **generate** — unchanged.

`references/scaffold-workflow.md` documents the coverage report, the decision
thresholds, and the extend workflow. Both SKILL.md files (Claude Code + Desktop)
get the updated Step 5.5.

## D. Testing

- `remove-cell`: removing a leaf drops it + its edges; removing a frame drops the
  subtree + all incident edges; unknown id errors.
- `add-node --mode append`: the new node is a child of the group; the group frame
  grows; **existing sibling groups keep their coordinates** (or the one shifted
  neighbour is reported); result has no overlap.
- `add-node --mode slot`: the new node is grid-snapped and overlaps nothing.
- `add-edge`: the edge has the right style for its family, both endpoints resolve,
  the path is orthogonal, and any pill clears the separator.
- **Integration**: scaffold a real template, apply a delta (remove 1 heavy extra +
  add 2 missing + relabel 1), assert the result: `validate-drawio --strict` exit 0,
  `check-composition` 0 FAIL, `score-diagram --sap-like` ≥ 85.
- **Regression**: the four committed demos regenerate byte-identical (engine core
  untouched).

## Risks & mitigations

1. **`add-node --mode append` reflow shifts the template.** Mitigation: reflow is
   scoped to the target group's own children; the frame grows in place; only a
   direct neighbour may be shifted, and only when growth would overlap it — and that
   shift is reported. If a clean localized reflow is not possible, the tool errors
   and the workflow falls back to `--mode slot`.
2. **`add-edge` local path crosses a node.** Mitigation: the gate
   (`check-composition` PIERCING) catches it; the workflow then re-runs `add-edge`
   with an explicit `--near`/port hint or routes the one edge around via a second
   elbow. Bounded to the single new edge.
3. **Heavy-extra removal leaves a hole.** Mitigation: after removing a heavy zone,
   the workflow may `--mode append` the replacements into a sibling group rather
   than leave the vacated area empty; the visual-rubric loop flags large voids.

## Backward compatibility

New scripts + SKILL docs only. Engine core untouched → demos byte-identical. The
Desktop bundle adds the three scripts (small); the curated `templates-pack.json`
already ships, so scaffold-and-extend works on Desktop for the packed subset.
