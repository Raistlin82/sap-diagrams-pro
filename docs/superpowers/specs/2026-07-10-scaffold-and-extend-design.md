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
from the interview). Component enumeration uses the **`serviceTokens` +
`scenarioAliases`** fields ONLY — `labelTokens` is deliberately excluded because it
is noisy (CSS/px fragments like `2016px`, `20font-size`). Word-boundary matching
(the selector's own `kw_hit`) classifies each into:

- **PRESENT** — requested components matched in the template's tokens.
- **MISSING** — requested components not found in the template → must be ADDED.
- **EXTRA** — template components not requested → candidates to REMOVE, each tagged
  **light** (a leaf cell) or **heavy** (a container: a frame/`subaccount`/zone that
  has children).

**Structural (light/heavy) tagging requires reading the candidate `.drawio`, not
just the index.** The index carries no per-component role, so the coverage report
**opens the candidate template file** and, for each EXTRA, finds the cell whose
label matches the component and marks it *heavy* when any other cell has
`parent == that cell` (i.e. it is a container with children), else *light*. This is
the only place `select-template.py` reads a template file; it does so only for the
top candidate(s) it reports coverage on.

`coverage = |PRESENT| / |requested|`.

**Decision (Step 5.5), evaluated in this exact order (first match wins):**

1. **scaffold (relabel-only)** — the existing pure path — when the top candidate is
   `★ recommended`, `MISSING` is empty (template already covers every requested
   component), and there are **no EXTRA** components to remove. Pure relabel suffices.
2. **scaffold-and-extend** when the top candidate is `★ recommended` (score ≥
   `RECOMMEND_THRESHOLD` = 14) **AND** `coverage ≥ COVERAGE_MIN` (default **0.4**)
   **AND** the extras are not dominated by *heavy* structural mismatches. Reached
   only when path 1 did not match — i.e. there is at least one MISSING component to
   ADD or at least one EXTRA to REMOVE. Heavy-extra guard (BOTH must hold to allow
   the path; failing either sends it to generate): heavy extras ≤ `HEAVY_EXTRA_MAX`
   (= 1) **AND** heavy extras ≤ ⅓ of the template's zones. Emit an explicit **delta
   plan**: `REMOVE […]`, `RELABEL/KEEP […]`, `ADD […]`.
3. **generate** otherwise (no template clears the bar) — the current procedural path.

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

- **scaffold-and-extend** — `scaffold-diagram.py` the chosen template → **take a
  pre-delta snapshot** (`<file>.pre-extend.bak`, the whole starting template) →
  apply the delta as an ordered chain: `remove-cell.py` each EXTRA (heavy first) →
  `relabel.py` the RELABEL set → `add-node.py` + `add-edge.py` each MISSING
  component. **Run `check-composition.py` after each structural edit** (add-node /
  add-edge / heavy remove) so a new FAIL is attributed to the edit that caused it
  (the per-edit `.bak` reverts exactly that step; the pre-delta snapshot reverts the
  whole chain). Then the full gate (`validate-drawio --strict` +
  `check-composition` + `score-diagram`) + visual-rubric loop (≤3) → deliver. On a
  gate failure: revert the offending edit via its `.bak` and retry it with a
  different placement/port hint (max 2 mechanical retries per edit); never hand-edit
  geometry. If the chain cannot converge, restore the pre-delta snapshot and fall
  back to the generate path.
- **scaffold** (pure relabel) — unchanged.
- **generate** — unchanged.

**Score gate (authoritative, applied by BOTH the workflow and the tests):** an
artifact must clear **both** `score-diagram --sap-like ≥ 85` (reference-free) **and**
`score-diagram --corpus assets/templates --min-score 82` (corpus similarity). A
scaffolded+extended diagram keeps the template's structure, so both should pass
comfortably; the two are enforced identically in Step 5.5 and in the integration
test so nothing can pass one and fail the other.

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
  add 2 missing + relabel 1), assert the result clears the **same authoritative
  gate as the workflow**: `validate-drawio --strict` exit 0, `check-composition`
  0 FAIL, `score-diagram --sap-like` ≥ 85 **and** `score-diagram --corpus
  assets/templates --min-score 82`.
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
