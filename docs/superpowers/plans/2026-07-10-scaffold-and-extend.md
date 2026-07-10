<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Scaffold-and-Extend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third hybrid path — *scaffold-and-extend* — that takes the closest SAP reference template as the base and surgically removes/relabels/adds components to reach the exact confirmed inventory, instead of discarding a good-partial-match template and generating from scratch.

**Architecture:** Three new deterministic, stdlib-only edit scripts (`remove-cell.py`, `add-node.py`, `add-edge.py`) operate in place on a scaffolded `.drawio` (each writes a `.bak`), reusing the engine's own style-contract / shape-index / geometry helpers so edits are indistinguishable from generated output. `select-template.py` gains a coverage report + 3-way decision. The SKILL orchestrates: Claude decides *what* to change; the tools do *how*; the existing gate + visual-rubric loop clean up. **The engine core is untouched — the four committed demos must regenerate byte-identical.**

**Tech Stack:** Python 3 (stdlib only: argparse, xml.etree, json, re), the existing `scripts/` engine (`_molecules.py`, `_channel_router.py`, `generate-drawio.py`'s `ShapeIndex`, `build-template-index.py`'s `kw_hit`), pytest via `tests/conftest.py::load_script`.

**Spec:** `docs/superpowers/specs/2026-07-10-scaffold-and-extend-design.md`

---

## File structure

| File | Responsibility |
|---|---|
| `scripts/_drawio_edit.py` (create) | Shared helpers for the edit tools: load/save a `.drawio` preserving formatting, write `.bak`, find cell by id/label, list children, parse/serialize `mxGeometry`, grid-snap. One home for the mechanics all three tools share (DRY). |
| `scripts/remove-cell.py` (create) | Remove a cell (subtree if container) + drop dangling edges. |
| `scripts/add-edge.py` (create) | Add one styled orthogonal edge (flowFamily/kind/pill), separator-aware pill. |
| `scripts/add-node.py` (create) | Add a node (icon resolved) in `--mode slot` or `--mode append` (local reflow). |
| `scripts/select-template.py` (modify) | Add `--components` coverage report (PRESENT/MISSING/EXTRA + light/heavy) + 3-way decision + `--json`. |
| `skills/sap-diagram-generate/SKILL.md` (modify) | Step 5.5 → three paths incl. scaffold-and-extend. |
| `packaging/claude-desktop-skill/SKILL.md` (modify) | Same Step 5.5 (Desktop-flavoured). |
| `skills/sap-diagram-generate/references/scaffold-workflow.md` (modify) | Coverage report + decision thresholds + extend workflow. |
| `packaging/claude-desktop-skill/build.sh` (modify) | Bundle the 3 new scripts + `_drawio_edit.py`. |
| `tests/test_drawio_edit.py`, `tests/test_remove_cell.py`, `tests/test_add_edge.py`, `tests/test_add_node.py`, `tests/test_select_template_coverage.py`, `tests/test_scaffold_extend_integration.py` (create) | Unit + integration coverage. |

**Test harness note:** load dashed-name scripts via `from conftest import load_script` then `load_script("remove-cell")`. Build small `.drawio` fixtures inline as strings, or copy one from `assets/templates/` into `tmp_path`. `Rect`/geometry come from `_geom_checks` / `_channel_router` as those modules already expose them.

---

## Task 1: `_drawio_edit.py` — shared edit helpers

**Files:**
- Create: `scripts/_drawio_edit.py`
- Test: `tests/test_drawio_edit.py`

- [ ] **Step 1: Write failing tests**

```python
from conftest import load_script
E = load_script("_drawio_edit")

MX = ('<mxfile><diagram><mxGraphModel><root>'
      '<mxCell id="0"/><mxCell id="1" parent="0"/>'
      '<mxCell id="g" value="Group" parent="1" vertex="1" style="rounded=1;">'
      '<mxGeometry x="10" y="20" width="100" height="80" as="geometry"/></mxCell>'
      '<mxCell id="n" value="Node" parent="g" vertex="1"><mxGeometry x="0" y="0" width="40" height="40" as="geometry"/></mxCell>'
      '<mxCell id="e" edge="1" source="n" target="g" parent="1"><mxGeometry as="geometry"/></mxCell>'
      '</root></mxGraphModel></diagram></mxfile>')

def test_load_find_children_geo(tmp_path):
    f = tmp_path/"d.drawio"; f.write_text(MX)
    doc = E.load(f)
    assert E.find_cell(doc, "g").get("value") == "Group"
    assert E.find_cell_by_label(doc, "Node").get("id") == "n"
    assert [c.get("id") for c in E.children(doc, "g")] == ["n"]
    assert E.geometry(E.find_cell(doc, "g")) == (10.0, 20.0, 100.0, 80.0)

def test_save_writes_bak(tmp_path):
    f = tmp_path/"d.drawio"; f.write_text(MX)
    doc = E.load(f); E.save(doc, f)
    assert (tmp_path/"d.drawio.bak").read_text() == MX

def test_grid_snap():
    assert E.snap(23) == 20 and E.snap(26) == 30   # 10px grid
```

- [ ] **Step 2: Run — expect FAIL** (`pytest tests/test_drawio_edit.py -q`; module not found).

- [ ] **Step 3: Implement** `scripts/_drawio_edit.py` with SPDX header + shebang:
  - `load(path) -> ElementTree` (xml.etree; keep it simple — the drawio the engine writes has no comments/CDATA to preserve beyond attributes).
  - `save(doc, path)` — write `path.bak` with the CURRENT on-disk bytes first (read before overwrite), then `doc.write(path, encoding="utf-8", xml_declaration=False)`.
  - `root(doc)` → the `<root>` under the first `<mxGraphModel>`.
  - `find_cell(doc, cid)`, `find_cell_by_label(doc, label)` (exact then case-insensitive), `children(doc, cid)` (cells with `parent == cid`), `geometry(cell) -> (x,y,w,h)` floats (0 when absent), `set_geometry(cell, x,y,w,h)`, `snap(v, grid=10)`.
  - `iter_cells(doc)`, `add_cell(doc, attrib, geom=None) -> Element`, `remove_cell_element(doc, cell)`.
  - Match `relabel.py`'s SPDX/shebang/style conventions (read it first).

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit**

```bash
git add scripts/_drawio_edit.py tests/test_drawio_edit.py
git commit -m "feat(edit): _drawio_edit shared helpers for the edit tools"
```

---

## Task 2: `remove-cell.py`

**Files:**
- Create: `scripts/remove-cell.py`
- Test: `tests/test_remove_cell.py`

- [ ] **Step 1: Write failing tests** (reuse the `MX` fixture):
  - `test_remove_leaf_drops_incident_edges`: remove `--id n` → cells `n` and `e` (source=n) gone; `g` kept; `.bak` written.
  - `test_remove_container_drops_subtree`: remove `--id g` → `g`, its child `n`, and edge `e` (target=g / source=n) all gone.
  - `test_remove_by_label`: `--match "Node"` removes `n`.
  - `test_unknown_id_errors`: `--id zzz` → exit ≠ 0, nothing written.
  - `test_json_reports_removed`: `--json` prints `{"removed": ["n","e"]}` (sorted).

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement** using `_drawio_edit`:
  - Resolve target (id or label). Compute the removal set: the target + (recursively) all descendants via `parent`. Then add every edge whose `source` or `target` is in the removal set. Remove all; `save`. `--json` reports the sorted ids. Exit 2 on unknown target (before writing).

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit** `feat(edit): remove-cell (subtree + dangling edges)`.

---

## Task 3: `add-edge.py`

**Files:**
- Create: `scripts/add-edge.py`
- Test: `tests/test_add_edge.py`

- [ ] **Step 1: Write failing tests:**
  - `test_add_edge_styles_by_family`: `--source n --target g --flowFamily identity --label "Login" --pill "SAML2/OIDC"` → a new `edge="1"` cell exists with `source=n target=g`; its style starts with the `edge-identity` style-contract molecule (compare to `_molecules._style(contract, "edge-identity")` prefix or the family colour `#188918`); a pill cell (`arcSize=50`) with value `SAML2/OIDC` exists; a label cell with `Login` exists.
  - `test_add_edge_orthogonal_single_elbow`: the emitted geometry has an `Array as="points"` with exactly one interior waypoint (an L), OR exit/entry anchors that force an orthogonal render (assert `orthogonalEdgeStyle` present and centers-not-aligned handled).
  - `test_add_edge_unknown_endpoint_errors`: bad `--source` → exit ≠ 0.
  - `test_pill_clears_separator`: with a `netsep`-style cell present, the pill rect does not overlap `_channel_router._sep_obstacle_rects(...)` bands.

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement**:
  - Load contract via `_molecules.load_contract()` (check the real accessor name in `_molecules.py`). Map `flowFamily`→`edge-<family>` molecule style; `kind`→canonical pill; default `edge-default`. Pick ports by relative position of source vs target centers (exit on the facing side, entry on the opposite) and emit `exitX/exitY/entryX/entryY` + `orthogonalEdgeStyle`. Emit ONE interior waypoint so the path is a clean L. Emit pill/label as separate `arcSize=50` / text cells positioned on the longest segment, shifted off the separator using `_channel_router._sep_obstacle_rects` (reuse, don't reimplement). Stable ids via a short hash of `(source,target)`. `save`.

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit** `feat(edit): add-edge (styled orthogonal edge, separator-aware pill)`.

---

## Task 4: `add-node.py` — `--mode slot`

**Files:**
- Create: `scripts/add-node.py`
- Test: `tests/test_add_node.py`

- [ ] **Step 1: Write failing tests:**
  - `test_add_node_slot_resolves_icon`: `--group g --label "Cloud ALM" --service "Cloud ALM" --mode slot --near n` → a new vertex child of `g` exists with an `image=` in its style (icon resolved via the engine `ShapeIndex`), value `Cloud ALM`.
  - `test_add_node_slot_grid_snapped_no_overlap`: the new node's geometry is on the 10px grid and its rect overlaps no existing cell in `g`.
  - `test_add_node_returns_id_json`: `--json` prints `{"id": "<newid>"}`.

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement `--mode slot`:**
  - Resolve icon: import `generate-drawio`'s `ShapeIndex` (`load_script("generate-drawio").ShapeIndex.load()`), `.resolve(service)`. Build the node cell with the resolved `image=` style (mirror how `generate-drawio` builds a service node — factor the smallest reusable piece; if not cleanly reusable, replicate the `shape=image;...;image=<uri>;` style with label).
  - Placement: start from `--near`'s rect (or the group's content box); scan outward on the 10px grid for the first W×H rectangle inside the group that overlaps no existing child; snap; set geometry; `parent = group`.
  - Default node W/H from the contract's product/box geometry.

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit** `feat(edit): add-node --mode slot (icon + free-slot placement)`.

---

## Task 5: `add-node.py` — `--mode append` (local reflow)

**Files:**
- Modify: `scripts/add-node.py`
- Test: `tests/test_add_node.py`

- [ ] **Step 1: Write failing tests:**
  - `test_add_node_append_reflows_group_only`: a group `g` with two children laid in a row; `--mode append` a third → all three children are inside `g`, non-overlapping, packed by the same rule as `_molecules` packing; `g`'s frame grew to contain them.
  - `test_add_node_append_keeps_siblings`: a second top-level group `g2` to the right of `g`; after append to `g`, `g2`'s (x,y) are unchanged **unless** `g` grew into it — in which case the tool reports the one shifted neighbour in `--json` (`{"shifted": ["g2"]}`) and `g2` moved by exactly the overlap.
  - `test_add_node_append_errors_when_no_clean_reflow`: a contrived case where growth would overlap two neighbours → exit ≠ 0 with a message telling the caller to use `--mode slot`.

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement `--mode append`:**
  - Append the new cell as a child of the group. Re-pack the group's children using `_molecules`' packing/`footprint` (reuse `_pack`/footprint helpers — read `_molecules.py`/`_skeleton_layout.py` for the exact function; do NOT reimplement packing). Recompute the group frame size from the packed children + insets; `set_geometry`. Compute the frame's growth delta; if the grown frame overlaps exactly one top-level sibling, shift that sibling by the delta and record it; if it would overlap ≥2 siblings, restore and exit 2.

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit** `feat(edit): add-node --mode append (localized group reflow)`.

---

## Task 6: `select-template.py` — coverage report + decision

**Files:**
- Modify: `scripts/select-template.py`
- Test: `tests/test_select_template_coverage.py`

- [ ] **Step 1: Write failing tests** (use a real template id from `template-index.json`, e.g. `sap-build-process-automation-l2`):
  - `test_coverage_present_missing`: `--components "Build Process Automation,Integration Suite,Cloud ALM"` → report lists BPA + Integration Suite as PRESENT and Cloud ALM as MISSING (Cloud ALM isn't in that template).
  - `test_extra_light_vs_heavy`: an EXTRA that is a container in the template's `.drawio` (a cell with children) is tagged `heavy`; a leaf EXTRA is `light`.
  - `test_decision_pure_relabel`: components fully covered, no extras → decision `scaffold` (relabel-only).
  - `test_decision_scaffold_extend`: recommended + coverage ≥0.4 + ≥1 missing/extra + heavy guard ok → decision `scaffold-extend` with a delta plan (`remove`/`relabel`/`add` lists).
  - `test_decision_generate_when_low_coverage`: coverage <0.4 → `generate`.
  - `test_enumeration_ignores_labelTokens`: a template whose `labelTokens` contain a `"…px"` fragment does not surface that as a component.

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement:**
  - New `--components "<csv>"` and reuse `build-template-index`'s `kw_hit` for matching. Enumerate template components from `serviceTokens + scenarioAliases` ONLY.
  - PRESENT/MISSING (requested vs template), EXTRA (template minus requested). For light/heavy: open the candidate `assets/templates/<file>` (via `_drawio_edit.load`), find the cell whose label matches the extra, tag `heavy` iff another cell has `parent == that cell`, else `light`.
  - Decision constants `COVERAGE_MIN = 0.4`, `HEAVY_EXTRA_MAX = 1`; evaluate paths in the spec's order (relabel → extend → generate). Emit human-readable + `--json` (`{"decision": …, "coverage": …, "delta": {"remove":[],"relabel":[],"add":[]}}`).

- [ ] **Step 4: Run — expect PASS**; also run the existing `tests/test_select_template.py` (unchanged behaviour when `--components` absent).

- [ ] **Step 5: Commit** `feat(select): coverage report + scaffold/extend/generate decision`.

---

## Task 7: SKILL Step 5.5 rewrite + reference doc

**Files:**
- Modify: `skills/sap-diagram-generate/SKILL.md` (Step 5.5)
- Modify: `packaging/claude-desktop-skill/SKILL.md` (Step 5.5, Desktop-flavoured — relative paths, degrade note)
- Modify: `skills/sap-diagram-generate/references/scaffold-workflow.md`

- [ ] **Step 1:** Rewrite Step 5.5 to the three paths (spec Section C): run `select-template --components …`; branch on `decision`; for `scaffold-extend` document the ordered chain (snapshot → remove heavy-first → relabel → add-node/add-edge → per-edit `check-composition` → full gate incl. BOTH `--sap-like ≥85` and `--corpus --min-score 82` → visual-rubric loop → deliver; on failure revert offending edit, ≤2 retries, else restore snapshot → generate). Keep `scaffold` and `generate` as the other two branches.
- [ ] **Step 2:** Update `references/scaffold-workflow.md` with the coverage report, thresholds, and the extend workflow.
- [ ] **Step 3:** Smoke: `python3 scripts/_ci_check_skills.py` passes (frontmatter intact).
- [ ] **Step 4: Commit** `docs(skill): Step 5.5 scaffold-and-extend workflow`.

---

## Task 8: Desktop bundle + integration + regression

**Files:**
- Modify: `packaging/claude-desktop-skill/build.sh`
- Create: `tests/test_scaffold_extend_integration.py`

- [ ] **Step 1: Write failing integration test:** scaffold `assets/templates/sap-build-process-automation-l2.drawio` into `tmp_path`; snapshot; `remove-cell` one heavy extra; `relabel` one match; `add-node --mode append` + `add-edge` two missing components (e.g. Cloud ALM + an edge); then assert the SAME authoritative gate as the workflow:
  - `validate-drawio --strict` exit 0,
  - `check-composition` 0 FAIL,
  - `score-diagram --sap-like` ≥ 85 **and** `score-diagram --corpus assets/templates --min-score 82`.
- [ ] **Step 2: Run — expect FAIL** (tools not yet wired end-to-end / thresholds).
- [ ] **Step 3:** Make it pass by fixing whatever the chain surfaces (placement/port hints only — never the engine core).
- [ ] **Step 4: Regression:** add `test_demos_byte_identical` — regenerate the four demos and diff (ignoring the `modified=` timestamp) against `git show HEAD:`; assert identical. Then add the 3 scripts + `_drawio_edit.py` to `build.sh`'s `SCRIPTS=(…)` and run `bash packaging/claude-desktop-skill/build.sh`; assert the zip contains them and stays ≤ 200 files.
- [ ] **Step 5:** Run the FULL suite (`python3 -m pytest -q`) — all green; then bump plugin to 0.6.0 + CHANGELOG.
- [ ] **Step 6: Commit** `feat(hybrid): scaffold-and-extend end-to-end + bundle + 0.6.0`.

---

## Definition of done

- `remove-cell` / `add-node` (both modes) / `add-edge` / `_drawio_edit` implemented + unit-tested.
- `select-template --components` emits coverage + the 3-way decision + delta plan.
- Both SKILL.md files + `scaffold-workflow.md` document the extend path with the authoritative dual score gate.
- Integration test: a scaffolded template extended to a target inventory clears the full gate.
- **Regression: the four demos regenerate byte-identical; full suite green.**
- Desktop bundle ships the new scripts, ≤ 200 files. Plugin 0.6.0, CHANGELOG updated, merged to `main` per repo flow.
