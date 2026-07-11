# Template-Informed Completeness — Design

**Date:** 2026-07-11
**Status:** Approved (direction) — spec revised after review
**Branch:** `feat/template-informed-completeness`

## Problem

The generator interviews the user (Step 4) to clarify an ambiguous description, but
it only ever *narrows* what the user already said — it never helps the user discover
components they **forgot but actually want**. Meanwhile the template corpus is a rich,
untapped completeness signal: when a closely-matching SAP reference architecture
contains a component the user did not mention, that component is often not noise —
it is a piece of the recurring pattern the user simply didn't think to name (a
Destination service behind a Cloud Connector, an Audit Log next to Cloud Logging,
identity provisioning next to authentication).

Two concrete defects surfaced while dogfooding a Build Work Zone + S/4HANA PCE + Cloud
ALM L1 request expose the same root cause — the pipeline treats a template's "extra"
components as *waste to strip*, never as *hints to offer*:

1. **Selector has no remove/present guard.** `select-template --components` recommended
   `scaffold-extend` from a Joule reference (`ra0024-joule-s4pce`) where the delta would
   keep 5 nodes and **remove 21**. The heavy-extra guard counts only *structural* extras,
   not the fact that 21 *light* extras are the template's whole identity.
2. **Corpus gate is mis-calibrated for the generate path.** The SKILL asserts a
   well-formed procedural diagram "should clear 82" on `score-diagram --corpus`. It does
   not: every shipped procedural demo — including the gold `task-center-L1`, which scores
   `--sap-like` **100** — scores `--corpus` **55–60**. The corpus fingerprint measures
   similarity to a *single* template, so it structurally rewards only scaffold/relabel.

## Goal

Turn the template corpus into a completeness advisor that feeds a single enriched
interview, and fix the two gate/decision defects that share the "extras are waste"
assumption. The user is always *asked* — nothing is auto-added.

## Non-Goals (YAGNI)

- No auto-adding suggested components without an explicit user answer.
- No change to the ranking algorithm (`score_entry`/`rank`) itself.
- No iterative/multi-round suggestion loop — one enriched interview.
- The recon does **not** re-ground each suggestion via MCP on its own; Claude grounds
  accepted components in Step 2 as it already does.
- No engine-core changes (`_skeleton_layout`, `_channel_router`, `_molecules`,
  `generate-drawio`) — the 4 shipped demos must regenerate byte-identical.

## Design

### Revised procedure (plugin SKILL numbering)

```
Step 1    Draft inventory (unchanged)
Step 1.5  [NEW] Recon: rank templates (plain `select-template` rank), remember the top ids
Step 2    Ground in MCP (unchanged; may also ground candidate components)
Step 3    Consult best-practice skills (unchanged)
Step 3.5  [NEW] Triage extras -> suggestion set. ONE call:
          `select-template "<req>" --components "<draft>" --suggest --best-practice "<bp>" --json`
          (fires here, not at 1.5, because it cross-references Step 3's best-practice output)
Step 4    ENRICHED interview: ONE AskUserQuestion batch unifying
          (a) best-practice "missing" findings + (b) template consensus-extras
Step 5    Confirm the REFINED inventory
Step 5.5  Decide scaffold-extend / generate on the refined inventory (guard #1 lives here)
```

Step 1.5 is a plain rank (cheap, no `--suggest`) so Claude knows the candidate set early;
the `--suggest` triage call fires at Step 3.5 once best-practice findings exist.

### Triage rule (Step 3.5) — precise & deterministic

**Input.** `suggest_extras(candidates, requested, best_practice, top_n=5,
min_consensus=2, cap=6)` where **`candidates` is a list of index-entry dicts** (NOT
`Ranked` objects). The caller maps the top-`top_n` `Ranked.id`s back to index entries
exactly as `_emit_coverage` already does (`select-template.py:453`).

**Per-candidate extras.** For each candidate entry, call the existing
`coverage_report(entry, requested)`; its `extra` list is `[{"label", "weight"}, …]` and
**already excludes anything matching `requested`** (it is computed with `kw_hit` against
the requested set). Read `e["label"]`.

**Canonical key (grouping).** For every extra label compute
`key = clean_label(label).lower().removeprefix("sap ").strip()`. This merges
`SAP Destination Service` / `Destination Service` and normalises HTML cruft. Group all
extras across the `top_n` candidates by `key`. `consensus[key]` = the number of **distinct
candidates** whose extras include that key (deduped within a candidate).

**Display label.** For each key, the output `"label"` is the `clean_label` spelling that
occurs most often across candidates; ties broken lexicographically (shortest-then-alpha)
— fully deterministic.

**Selection.** A key becomes a suggestion iff:
- `consensus[key] >= min_consensus` (default 2 of 5 = recurring pattern), **OR**
- it matches a **best-practice** name: `any(kw_hit(key, bp.lower()) or kw_hit(bp.lower(), key)
  for bp in best_practice)` (both directions, pinned for deterministic tests).

Sort suggestions by `consensus` desc then label; **cap at `cap = 6`**. Each item:

```json
{"label": "Destination Service", "consensus": 4, "candidates": 5,
 "bestPractice": true, "reason": "in 4/5 reference simili · best-practice"}
```

This filter removes the Joule noise: `Joule`/`AI Core` appear only in the single Joule
template (consensus 1, no best-practice match) → not suggested; `Destination`,
`Audit Log`, identity provisioning recur across candidates → surfaced.

### Interview presentation (Step 4, Claude behaviour — SKILL prose)

The script only *produces* the `suggestions` array; Claude renders it into the existing
`AskUserQuestion` interview as one **multi-select** question:
"Questi compaiono di norma in questa architettura ma non li hai citati — quali aggiungo?"
with each suggestion's `reason` shown beside it. Accepted items are appended to the
inventory before Step 5 confirmation. If `suggestions` is empty, the interview proceeds
exactly as today.

### Finding #1 folds in — remove/present guard

After the user promotes some extras into the request, the Step 5.5 decision is computed on
the **refined** inventory (Claude passes it on `--components`), so many former "remove"
items become "keep" and the delta shrinks honestly. The guard is a single rule in
`decide()`, evaluated on values available *before* the scaffold/extend/generate branch
(`select-template.py:386`, where `extra` and `present` already exist; `delta` is built
later, so the guard is expressed in terms of `extra`):

> If `len(extra) > len(present)` → force `decision = "generate"`.

`decide()` computes `present`/`extra` solely from its `requested` arg, so the guard sees
whatever `--components` set the caller passes — the refined set at Step 5.5. Rationale:
keeping fewer nodes than we strip means the template is the wrong base (a gutted layout
with holes; remove does not reflow or shrink frames). This is evaluated *after*
refinement, so a template only "loses" if it genuinely doesn't fit even the enriched
request.

### Finding #2 — corpus gate is scaffold-only (a command change, not just prose)

`score-diagram.py` behaviour (verified): `--sap-like` **never gates** — it prints/returns
score, exit 0 (`score-diagram.py:648`); `--corpus … --min-score 82` **exits 2** when the
best match is below the floor (`687-689`). So the plugin SKILL, which invokes
`--corpus … --min-score 82` on the generate path (`skills/sap-diagram-generate/SKILL.md`
lines ~175 and ~307), currently instructs a command that **fails every procedural
diagram** (55–60 < 82). The fix is to change the commands/gate, not only the prose:

- **Generate path:** authoritative gate is `score-diagram --sap-like` (≥ 85), enforced by
  Claude reading the JSON `score`. **Drop `--corpus --min-score` from the generate path**
  (or run `--corpus` without `--min-score`, informational only) so no false failure.
- **Scaffold / scaffold-extend path:** keep the dual gate (`--sap-like ≥ 85` **and**
  `--corpus … --min-score 82`) — a template-derived artifact fingerprints ~98.

## Components / Interfaces

### `scripts/select-template.py`

Backward-compatible CLI additions (no `--suggest` and no `--components` → identical to
today):

- `--suggest` (new flag): also emit a `suggestions` array (the triaged extras). Requires
  `--components`. Fired at Step 3.5.
- `--best-practice "<csv>"` (new, optional): best-practice recommended component names to
  OR into the triage. Omitted → triage is consensus-only.
- The existing `--components` decision path gains the `len(extra) > len(present)` guard in
  `decide()`.

New pure function (unit-testable without a live MCP), signature above:
`suggest_extras(candidates: list[dict], requested, best_practice, top_n=5,
min_consensus=2, cap=6) -> list[dict]`. Reuses `enumerate_components`, `coverage_report`,
`kw_hit`, `clean_label` (all already available via `_load_builder()` / module scope).

### SKILL docs — explicit per-file mapping

**`skills/sap-diagram-generate/SKILL.md`** (plugin; has Step 1–9 with a Step 5.5):
- Insert **Step 1.5** (recon rank) and **Step 3.5** (triage `--suggest` call + enriched
  interview inputs).
- Reword **Step 4** to fold the `suggestions` into the interview; note **Step 5.5** runs
  the decision on the refined inventory.
- Correct the corpus-gate command/prose at **BOTH** sites that hard-code
  `--corpus … --min-score 82` on the generate path: the Step 5.5 authoritative-gate line
  (~179) and the Step 8 item-2 gate block (~312). Scaffold path keeps the dual gate.

**`packaging/claude-desktop-skill/SKILL.md`** (Desktop; different numbering
1/2/2.5/3/4/5/6/7, **no best-practice step**):
- Recon + triage prose lands in the interview step (§2) and hybrid decision (§2.5). Because
  there is no best-practice consult on Desktop, its triage is **consensus-only**
  (`--best-practice` omitted) — documented as an accepted degrade.
- Finding #2 needs **no command change here**: the Desktop gate (§5) already guards
  `--corpus` behind `corpus_size > 0` (corpus is not bundled) and already says to rely on
  `--sap-like ≥ 85`. Add a one-line note that corpus is scaffold-only.

## Data Flow

`draft components` + `best-practice findings`
→ `select-template "<req>" --components "<draft>" --suggest --best-practice "<bp>" --json`  (Step 3.5)
→ `suggestions[]` → Claude's enriched `AskUserQuestion` → user picks → `refined components`
→ `select-template "<req>" --components "<refined>" --json`  (Step 5.5)
→ `decision` (+ `len(extra) > len(present)` guard) → scaffold-extend / generate
→ generate + **per-path gate** (generate: `--sap-like ≥ 85`; scaffold: dual) → render.

## Error Handling / Edge Cases

- No candidates clear minimal relevance → `suggestions: []`; normal interview.
- Every extra is single-template and no best-practice match → `suggestions: []` (Joule
  case). Verified by test.
- `coverage_report` already excludes requested items from `extra`, so suggestions never
  echo something the user already listed.
- Template label carries HTML cruft → normalised via `clean_label` before key/display.
- `--best-practice` omitted → triage falls back to consensus-only (the Desktop reality).

## Testing

- **Triage:** extra in ≥2 candidates → suggested; single-template extra → NOT suggested
  unless best-practice; best-practice OR path (both `kw_hit` directions); `cap` respected;
  `SAP X`/`X` spellings merge to one key with summed consensus; HTML label normalised;
  display-label tie-break deterministic.
- **Guard:** a requested set where `len(extra) > len(present)` → `decision == "generate"`;
  a set where it holds → `scaffold-extend` still possible.
- **Gate calibration:** the 4 shipped demos clear `--sap-like ≥ 85` (generate path); a
  scaffolded template clears `--corpus … --min-score 82` (scaffold path). Codifies #2.
- **Regression:** the 4 demos regenerate byte-identical (engine untouched).
- **Backward-compat:** `select-template` without `--suggest` emits the same JSON as before
  (plus the guard, which is inert unless `extra > present`).

## Rollout

Feature branch `feat/template-informed-completeness`, subagent-driven TDD, two-stage review
per task, final full-feature review, then finishing-a-development-branch → merge to main +
version bump (0.7.0) to the plugin marketplace + rebuild the Desktop bundle.
