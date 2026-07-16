# Hybrid scaffold / scaffold-extend / generate workflow

The engine can produce a SAP diagram three ways. This reference documents the
decision, the selector's scoring formula, the coverage report + thresholds that
route between them, and the ordered extend workflow.

## The three paths

| Path | How | When |
|---|---|---|
| **Scaffold** (relabel-only) | Copy the closest real SAP reference `.drawio`, then edit it surgically — relabel cells, swap icons; **no add/remove**. Inherits the exact canvas, zones, Horizon palette, fonts and icons of a real SAP diagram → highest fidelity. | A real template is *close enough* AND already covers every requested component with nothing extra. |
| **Scaffold-extend** | Start from that template, then **remove** out-of-scope cells, **relabel** the matches, and **add** the missing components — reaching the exact confirmed inventory while inheriting the template's real SAP layout, palette, fonts and icons. | A template covers *a good part but not all* of the request (or carries a few extras), and the mismatch is not dominated by *heavy* structural extras. |
| **Generate** | Author an IR v2 and render it procedurally with `generate-drawio.py`, after extracting a pattern brief from the closest SAP references. Full control, arbitrary topology, but still reference-informed. | No template is close enough — the request is novel or a combination no single template covers. |

All three paths converge on the **same downstream gate**: `validate-drawio.py --strict`
+ `check-composition.py` + the **per-path score gate** (below) + the visual-rubric
vision loop. A diagram is only delivered when that gate is green.

## Decision procedure

Feed the selector the **confirmed canonical component list** (the interview
inventory) via `--components`; it ranks the entries in `assets/template-index.json`
and, for the top candidate, emits a coverage report + a `decision`:

```bash
select-template.py "<request>" --components "<a>,<b>,…" --json [--level L2]
```

The `--json` payload carries `decision` ∈ {`scaffold`, `scaffold-extend`,
`generate`}, `coverage`, `present`/`missing`/`extra`, `heavyGuardOk`, the top
`template` id, `recommended`, and a bounded `delta`
= `{remove: ["<label>", …], relabel: [{from, to}, …], add: ["<label>", …]}`.
The decision is evaluated in this exact order (first match wins):

1. **scaffold** (relabel-only) — top candidate is `★ recommended`, `missing` is
   empty, and there are **no** `extra`. Pure relabel suffices.
2. **scaffold-extend** — top candidate is `★ recommended`, `coverage ≥
   COVERAGE_MIN`, there is at least one `missing` or `extra`, **and** the
   heavy-extra guard holds. Apply the `delta`.
3. **generate** — anything else (nothing clears the bar); `scaffold-diagram.py`
   exits `3`.

## Coverage report

Component enumeration uses the candidate's **`serviceTokens` + `scenarioAliases`**
only — `labelTokens` is deliberately excluded (it is noisy: CSS/px fragments like
`2016px`, `20font-size`). Word-boundary matching (the selector's own `kw_hit`)
classifies each requested/template component into:

- **PRESENT** — requested components matched in the template's tokens.
- **MISSING** — requested components not found → must be **ADDED**.
- **EXTRA** — template components not requested → candidates to **REMOVE**, each
  tagged **light** (a leaf cell) or **heavy** (a container: a frame /
  `subaccount` / zone that has children).

`coverage = |PRESENT| / |requested|`.

The **light/heavy tag requires reading the candidate `.drawio`**, not just the
index (the index carries no per-component role): for each EXTRA, the report finds
the cell whose label matches the component and marks it *heavy* when any other
cell has `parent == that cell` (a container with children), else *light*. This is
the only place `select-template.py` opens a template file, and only for the top
candidate it reports coverage on.

## Decision thresholds

Named, tunable constants in `select-template.py`:

- `RECOMMEND_THRESHOLD = 14.0` — the top candidate's score must clear this to be
  `★ recommended` (a prerequisite for both scaffold paths).
- `COVERAGE_MIN = 0.4` — scaffold-extend requires `coverage ≥ 0.4`; below it the
  template covers too little to be worth extending → generate.
- `HEAVY_EXTRA_MAX = 1` + `zoneCount/3` clause — the **heavy-extra guard**: BOTH
  must hold to allow scaffold-extend, failing either sends it to generate:
  - `heavy extras ≤ HEAVY_EXTRA_MAX` (= 1), **AND**
  - `heavy extras ≤ zoneCount / 3` (the candidate's `zoneCount` from
    `template-index.json`) — a template with few zones can't absorb even one heavy
    structural removal.

## Ordered extend workflow (`scaffold-extend`)

1. `scaffold-diagram.py --template <template> --out <out>.drawio` copies the
   chosen template.
2. **Snapshot the whole starting template**: `cp <out>.drawio <out>.drawio.pre-extend.bak`.
3. Apply the `delta` as an ordered chain:
   - **Remove** each `delta.remove` with `remove-cell.py` — **heavy extras first**
     (they free the most space; removing a container drops its subtree + incident
     edges).
   - **Relabel** each `delta.relabel` with `relabel.py` (see below).
   - **Add** each `delta.add` — `add-node.py` (Claude picks `--mode append` for a
     group member with localized reflow, or `--mode slot --near <ref>` for the
     nearest free slot) then `add-edge.py` to wire it in.
4. **Run `check-composition.py` after each structural edit** (add-node /
   add-edge / heavy remove) so a new FAIL is attributed to the edit that caused
   it. Each edit writes its own `.bak` (reverts exactly that step); the
   `.pre-extend.bak` snapshot reverts the whole chain.
5. On a FAIL: revert that one edit via its `.bak` and retry with a different
   placement/port hint — **max 2 mechanical retries per edit**; never hand-edit
   geometry. If the chain can't converge, restore the `.pre-extend.bak` snapshot
   and fall back to the **generate** path.
6. Then the full downstream gate + visual-rubric loop.

## Selector scoring formula

`select-template.py` reads the prebuilt index (no per-run XML parsing) and blends,
for each entry, these signals against the request. The request's family and
scenario aliases are detected with the **same vocabularies** that built the index
(`build-template-index.py`), so request and template are classified consistently.
All keyword matching is **word-boundary** (`kw_hit`) — "storage" never matches
"rag", "aws" never matches "flaws".

| Signal | Weight | Notes |
|---|---|---|
| scenarioAliases hit | **+10 each** | Curated canonical scenario markers (Task Center, Joule, MCP, Private Link, Event Mesh, …). Strongest. |
| family match | **+6** | Request's inferred family == template family (skipped for `generic`). |
| serviceTokens overlap | **+3 / word**, cap +24 | Request words found inside the template's canonical service names. |
| title / filename overlap | **+4 / word** | Request words in the template title or filename. |
| labelTokens overlap | **+1 / word**, cap +8 | Request words in the template's full-text word bag. Weakest; capped so large templates can't dominate on bulk. |
| level match | **+5** | Only when the request names L0/L1/L2 (or `--level`) and it equals the template level. |
| level mismatch | **−3** | Request names a level; template is a *different* explicit level. |

Score = sum of the above (rounded to 1 decimal). Candidates are sorted by score
desc, then id.

## Confidence threshold

`RECOMMEND_THRESHOLD = 14.0`.

The bar sits deliberately **above any single weak signal**:

- One curated alias hit alone (10) does **not** clear it — a one-word scenario
  mention shouldn't trigger a scaffold.
- An alias hit **+ family match** (10 + 6 = 16), or an alias hit **+ real service
  overlap**, clears it — that combination means a genuinely close template.

Only the top-ranked candidate can be flagged `recommended`. When nothing clears
the bar, `scaffold-diagram.py` exits `3` (distinct from `1` error / `2` usage) so a
caller can branch cleanly to the procedural engine.

### Worked examples

```
select-template.py "Joule agent calls S/4HANA via MCP and XSUAA"
  1.  42.0  ra0029-architecture   [ai/unknown]  ★ recommended
        scenario match: Joule, MCP (+20) · family ai (+6) · service overlap (+12)
```
```
select-template.py "SAP Task Center central inbox L1"
  1.  31.0  sap-task-center-l1    [connectivity/L1]  ★ recommended
        scenario match: Task Center (+10) · service overlap (+6) · title/file (+8)
```
```
select-template.py "a picnic in the park with sandwiches"
  1.   0.0  ra0000-demo           [generic/unknown]
  → no template clears 14.0 → use generate-drawio.py
```

## Per-path score gate and reference feedback

The score gate is **authoritative** but not identical for all paths. Scaffolded
diagrams inherit a real template skeleton, so they can be hard-gated against the
corpus. Procedural generate diagrams should learn from the corpus but not be
blocked by a raw fingerprint score that intentionally measures structural
similarity to one existing reference.

- All paths: `score-diagram.py --sap-like <out>` **≥ 85** — reference-free
  SAP-likeness (works everywhere, including Desktop where the loose corpus isn't
  bundled).
- Scaffold / scaffold-extend only:
  `score-diagram.py --corpus assets/templates <out> --min-score 82` — hard gate
  because the artifact should still fingerprint like the copied reference.
- Generate only: `score-diagram.py --corpus assets/templates <out> --json --top 5`
  — feedback, not a hard gate. Use the top matches and weak dimensions to revise
  the next IR pass if the diagram looks off-pattern.

Empirically on the corpus check: a verbatim template copy scores **100**; a
scaffolded **+ relabelled** (or **+ extended**) diagram scores **~98**
(relabel/extend changes content and adds a few cells, not the overall structure);
so **82** is a safe floor that a real scaffold always clears while still failing a
scaffold whose structure drifted from the SAP base. A procedural `generate`
diagram may score much lower on corpus similarity even when it is SAP-like,
because no single reference shares its exact skeleton. On Desktop the corpus line
is skipped when `assets/templates/` is absent; the `--sap-like ≥ 85` floor still
applies.

## Generate-from-reference pattern transfer

When the decision is `generate`, the selector still matters. Treat the top ranked
candidates as layout teachers:

1. Record the top reference id/path, score, rationale, coverage, `present`,
   `missing`, and `extra`.
2. State why it cannot be extended: low coverage, gutting guard, heavy extras, or
   user choice.
3. Borrow structural patterns only: zone count/depth, left-center-right reading
   direction, BTP/subaccount nesting, identity band, governance/top band,
   private/on-prem right tier, network separator, suite/product molecule style,
   and edge/pill families.
4. Do not borrow unconfirmed content. Extras from references become interview or
   best-practice suggestions, not automatic nodes.
5. After rendering, run corpus scoring without `--min-score` and compare the
   closest matches against the brief. If the generated diagram is visually unlike
   every close reference for avoidable reasons, revise the IR once before
   delivering.

## Surgical relabel rules

`relabel.py` is how the scaffold and scaffold-extend paths adapt a copied
template **without redrawing**:

- `--set <cellId>=<new label>` — address a cell by its `id`.
- `--replace "<old>=<new>"` — match a cell by its rendered visible text (HTML
  stripped, `<br>` → space, entities unescaped, whitespace collapsed).

Both preserve one simple inline wrapper (`<b>/<i>/<u>/<font>/<span>`) so
colour/formatting survive a text swap, and touch **only** the `value`/`label`
attribute — `mxGeometry`, `style`, `id`, `source`/`target` are left exactly as
parsed. In-place edits write a `.bak`; use `--out` to write elsewhere. Never
hand-edit geometry — that is what breaks SAP fidelity. If a relabel goes wrong,
restore the `.bak` and redo.
