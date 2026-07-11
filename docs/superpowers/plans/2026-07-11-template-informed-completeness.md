# Template-Informed Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn matching SAP reference templates into a completeness advisor that feeds one enriched interview, and fix the two "extras are waste" defects (a remove/present decision guard; a scaffold-only corpus gate).

**Architecture:** A new pure function `suggest_extras()` in `select-template.py` triages template extras (consensus across the top candidates + best-practice) into interview suggestions, exposed via a `--suggest`/`--best-practice` CLI. `decide()` gains a `len(extra) > len(present)` guard. Both SKILL.md files get new recon/triage steps and a corrected per-path score gate. The engine core is untouched → the 4 shipped demos regenerate byte-identical.

**Tech Stack:** Python 3.12 stdlib only; pytest via `tests/conftest.py::load_script`. Spec: `docs/superpowers/specs/2026-07-11-template-informed-completeness-design.md`.

**Standing constraints:** No engine-core edits (`_skeleton_layout.py`, `_channel_router.py`, `_molecules.py`, `generate-drawio.py`). `assets/brand-pack.local` must never enter the Desktop bundle. Commit messages end with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Commit per task; do not push until the branch is finished.

---

## File Structure

- **Modify** `scripts/select-template.py` — add `suggest_extras()` (T1), `--suggest`/`--best-practice` CLI + `_emit_coverage` augmentation (T2), guard in `decide()` (T3). Add `from collections import Counter` to imports.
- **Create** `tests/test_template_completeness.py` — triage unit tests (T1) + CLI suggest tests (T2).
- **Modify** `tests/test_select_template_coverage.py` — guard tests (T3).
- **Modify** `skills/sap-diagram-generate/SKILL.md` — Step 1.5/3.5, enriched Step 4, gate fix at the two sites (T4).
- **Modify** `packaging/claude-desktop-skill/SKILL.md` — recon/triage (consensus-only) + corpus scaffold-only note (T5).
- **Create** `tests/test_gate_calibration.py` — per-path gate calibration (T6).
- **Modify** `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `CHANGELOG.md`, `packaging/claude-desktop-skill/build.sh` if needed (T6).

---

### Task 1: `suggest_extras()` triage function

**Files:**
- Modify: `scripts/select-template.py` (add `from collections import Counter` near the top imports; add the function after `coverage_report`, ~line 370)
- Create: `tests/test_template_completeness.py`

- [ ] **Step 1: Write the failing tests**

```python
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""tests/test_template_completeness.py — template-informed completeness:
suggest_extras() triage + the --suggest CLI surface. See
docs/superpowers/specs/2026-07-11-template-informed-completeness-design.md."""
from __future__ import annotations

import json
from pathlib import Path

from conftest import load_script

sel = load_script("select-template")


def _entry(eid, service_tokens, aliases=None):
    return {"id": eid, "file": f"{eid}.drawio", "zoneCount": 6,
            "serviceTokens": service_tokens, "scenarioAliases": aliases or []}


def test_consensus_two_of_candidates_suggested():
    # "Destination Service" appears in 2 of 3 candidates -> suggested (>=2).
    # "Joule" appears in only 1 -> filtered out (no best-practice match).
    cands = [
        _entry("c1", ["Work Zone", "Destination Service", "Joule"]),
        _entry("c2", ["Work Zone", "Destination Service"]),
        _entry("c3", ["Work Zone", "Cloud Connector"]),
    ]
    out = sel.suggest_extras(cands, requested=["Work Zone"], best_practice=[])
    labels = {s["label"] for s in out}
    assert "Destination Service" in labels
    assert "Joule" not in labels
    dest = next(s for s in out if s["label"] == "Destination Service")
    assert dest["consensus"] == 2 and dest["candidates"] == 3
    assert dest["bestPractice"] is False


def test_single_template_extra_promoted_by_best_practice():
    # "Audit Log" is in only 1 candidate (consensus 1) but matches a
    # best-practice recommendation -> suggested anyway.
    cands = [
        _entry("c1", ["Work Zone", "Audit Log"]),
        _entry("c2", ["Work Zone"]),
    ]
    out = sel.suggest_extras(cands, requested=["Work Zone"],
                             best_practice=["Audit Log Service"])
    audit = next((s for s in out if s["label"] == "Audit Log"), None)
    assert audit is not None
    assert audit["bestPractice"] is True
    assert "best-practice" in audit["reason"]


def test_sap_prefixed_spellings_merge():
    # "SAP Destination Service" and "Destination Service" are the same key -> one
    # entry with consensus 2 (not two consensus-1 buckets).
    cands = [
        _entry("c1", ["Work Zone", "SAP Destination Service"]),
        _entry("c2", ["Work Zone", "Destination Service"]),
    ]
    out = sel.suggest_extras(cands, requested=["Work Zone"], best_practice=[])
    dests = [s for s in out if "Destination" in s["label"]]
    assert len(dests) == 1
    assert dests[0]["consensus"] == 2


def test_cap_respected():
    # 8 distinct extras each in >=2 candidates; cap=3 keeps only the top 3.
    names = [f"Svc{i}" for i in range(8)]
    cands = [_entry("c1", ["Work Zone", *names]),
             _entry("c2", ["Work Zone", *names])]
    out = sel.suggest_extras(cands, requested=["Work Zone"], best_practice=[], cap=3)
    assert len(out) == 3


def test_already_requested_never_suggested():
    # coverage_report excludes requested items from `extra`, so a requested
    # component is never echoed back as a suggestion.
    cands = [_entry("c1", ["Work Zone", "Integration Suite"]),
             _entry("c2", ["Work Zone", "Integration Suite"])]
    out = sel.suggest_extras(cands, requested=["Work Zone", "Integration Suite"],
                             best_practice=[])
    assert all(s["label"] != "Integration Suite" for s in out)


def test_html_label_normalised():
    # A template label carrying HTML is cleaned before keying/displaying.
    cands = [_entry("c1", ["Work Zone", "<b>Destination Service</b>"]),
             _entry("c2", ["Work Zone", "Destination Service"])]
    out = sel.suggest_extras(cands, requested=["Work Zone"], best_practice=[])
    dests = [s for s in out if "Destination" in s["label"]]
    assert len(dests) == 1 and "<b>" not in dests[0]["label"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_template_completeness.py -q`
Expected: FAIL with `AttributeError: module ... has no attribute 'suggest_extras'`

- [ ] **Step 3: Implement `suggest_extras`**

Add `from collections import Counter` to the imports block at the top of `scripts/select-template.py`. Add this function immediately after `coverage_report` (before `decide`):

```python
# --------------------------------------------------------------------------- #
# Template-informed completeness: triage template extras into interview
# SUGGESTIONS (consensus across candidates OR a best-practice match).
# --------------------------------------------------------------------------- #
def _canon_key(label: str) -> str:
    """Grouping key: cleaned, lowercased, SAP-prefix-stripped."""
    return clean_label(label).lower().removeprefix("sap ").strip()


def suggest_extras(candidates, requested, best_practice=(), top_n=5,
                   min_consensus=2, cap=6, templates_dir=None):
    """Triage the top candidates' EXTRA components into completeness suggestions.

    ``candidates`` are index-entry dicts (NOT ``Ranked``); only the first
    ``top_n`` are used. ``coverage_report`` already excludes ``requested`` from
    each candidate's ``extra``. A canonical extra (grouped by ``_canon_key``) is
    suggested iff it appears in >= ``min_consensus`` distinct candidates OR it
    word-boundary-matches a ``best_practice`` name (both directions). Returns
    ``[{label, consensus, candidates, bestPractice, reason}]`` sorted by
    consensus desc then label, capped at ``cap``."""
    cands = list(candidates)[:top_n]
    n = len(cands)
    bp_low = [b.lower() for b in _clean_requested(best_practice)]

    buckets: dict[str, dict] = {}
    for i, entry in enumerate(cands):
        rep = coverage_report(entry, requested, templates_dir)
        for e in rep["extra"]:
            label = clean_label(e["label"])
            key = _canon_key(e["label"])
            if not key:
                continue
            b = buckets.setdefault(key, {"cands": set(), "spellings": Counter()})
            b["cands"].add(i)
            b["spellings"][label] += 1

    out = []
    for key, b in buckets.items():
        consensus = len(b["cands"])
        bp = any(kw_hit(key, x) or kw_hit(x, key) for x in bp_low)
        if consensus < min_consensus and not bp:
            continue
        # display label: most common cleaned spelling; tie -> shortest then alpha
        label = sorted(b["spellings"].items(),
                       key=lambda kv: (-kv[1], len(kv[0]), kv[0]))[0][0]
        reasons = []
        if consensus >= min_consensus:
            reasons.append(f"in {consensus}/{n} reference simili")
        if bp:
            reasons.append("best-practice")
        out.append({"label": label, "consensus": consensus, "candidates": n,
                    "bestPractice": bp, "reason": " · ".join(reasons)})

    out.sort(key=lambda s: (-s["consensus"], s["label"]))
    return out[:cap]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_template_completeness.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/select-template.py tests/test_template_completeness.py
git commit -m "feat(select-template): suggest_extras() — triage template extras into completeness suggestions

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `--suggest` / `--best-practice` CLI surface

**Files:**
- Modify: `scripts/select-template.py` (`_emit_coverage` ~line 448, `main` ~line 481)
- Modify: `tests/test_template_completeness.py` (append CLI tests)

- [ ] **Step 1: Write the failing tests** (append to `tests/test_template_completeness.py`)

```python
# --------------------------------------------------------------------------- #
# CLI: --suggest
# --------------------------------------------------------------------------- #
def test_cli_suggest_emits_suggestions(capsys):
    rc = sel.main([
        "sap build work zone with s4hana pce and build process automation",
        "--components", "Build Work Zone,S/4HANA,Build Process Automation",
        "--suggest", "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "suggestions" in payload
    assert isinstance(payload["suggestions"], list)
    for s in payload["suggestions"]:
        assert set(s) >= {"label", "consensus", "candidates", "bestPractice", "reason"}


def test_cli_suggest_requires_components(capsys):
    rc = sel.main(["some request", "--suggest", "--json"])
    assert rc == 2  # --suggest without --components is an error


def test_cli_no_suggest_is_backward_compatible(capsys):
    # Without --suggest the coverage payload has NO suggestions key (unchanged).
    rc = sel.main([
        "SAP Build Process Automation L2 with Task Center",
        "--components", "Build Process Automation,Integration Suite,Cloud ALM",
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "suggestions" not in payload
    assert set(payload["delta"]) == {"remove", "relabel", "add"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_template_completeness.py -q -k "suggest_emits or suggest_requires"`
Expected: FAIL (`--suggest` unrecognized → argparse `SystemExit(2)`). Note: `test_cli_no_suggest_is_backward_compatible` already passes pre-impl — only the two genuinely-new tests fail.

- [ ] **Step 3: Implement the CLI**

In `_emit_coverage`, change the signature and append suggestions when requested:

```python
def _emit_coverage(index: dict, ranked: list[Ranked], components: str,
                   as_json: bool, suggest: bool = False,
                   best_practice: str = "") -> int:
    """Report component coverage + the routing decision for the top candidate,
    plus (when ``suggest``) completeness suggestions over the top candidates."""
    requested = _clean_requested(components.split(","))
    top = ranked[0]
    entry = next((e for e in index.get("templates", []) if e.get("id") == top.id),
                 {"id": top.id, "file": top.file})
    result = decide(entry, requested, top.recommended)
    result["template"] = top.id
    result["recommended"] = top.recommended

    if suggest:
        entries = [next((e for e in index.get("templates", []) if e.get("id") == r.id),
                        {"id": r.id, "file": r.file}) for r in ranked]
        result["suggestions"] = suggest_extras(
            entries, requested,
            _clean_requested((best_practice or "").split(",")),
            top_n=len(entries))
```

Keep the rest of `_emit_coverage` unchanged, but before the final `return 0` in the
non-JSON branch add:

```python
    if suggest and result.get("suggestions"):
        print("suggest  : " + ", ".join(
            f"{s['label']} ({s['reason']})" for s in result["suggestions"]))
```

In `main`, add the two arguments after `--components`:

```python
    ap.add_argument("--suggest", action="store_true",
                    help="with --components: also emit completeness suggestions "
                         "(template extras seen across the top candidates)")
    ap.add_argument("--best-practice", default="",
                    help="comma-separated best-practice component names to OR into "
                         "the --suggest triage")
```

And replace the `--components` dispatch guard:

```python
    if args.suggest and args.components is None:
        print("--suggest requires --components", file=sys.stderr)
        return 2
    if args.components is not None:
        return _emit_coverage(index, ranked, args.components, args.json,
                              suggest=args.suggest, best_practice=args.best_practice)
```

- [ ] **Step 4: Run the full completeness + coverage suites**

Run: `python3 -m pytest tests/test_template_completeness.py tests/test_select_template_coverage.py -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add scripts/select-template.py tests/test_template_completeness.py
git commit -m "feat(select-template): --suggest / --best-practice CLI for completeness triage

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: remove/present guard in `decide()`

**Files:**
- Modify: `scripts/select-template.py` (`decide`, ~line 392 branch)
- Modify: `tests/test_select_template_coverage.py` (append guard tests)

- [ ] **Step 1: Write the failing tests** (append to `tests/test_select_template_coverage.py`)

```python
# --------------------------------------------------------------------------- #
# gutting guard: keep fewer than we strip -> generate (finding #1)
# --------------------------------------------------------------------------- #
def test_guard_generate_when_removes_exceed_keeps(tmp_path):
    # 1 present (Alpha), 3 extras (Beta/Gamma/Delta) -> extra(3) > present(1).
    _write_drawio(tmp_path / "gut.drawio", [
        ("a", "Alpha", "1"), ("b", "Beta", "1"),
        ("g", "Gamma", "1"), ("d", "Delta", "1"),
    ])
    entry = {"id": "gut", "file": "gut.drawio", "zoneCount": 12,
             "serviceTokens": ["Alpha", "Beta", "Gamma", "Delta"],
             "scenarioAliases": []}
    result = sel.decide(entry, ["Alpha"], recommended=True, templates_dir=tmp_path)
    assert result["decision"] == "generate"


def test_guard_allows_scaffold_extend_when_keeps_dominate(tmp_path):
    # 3 present, 1 extra -> guard does NOT fire; scaffold-extend still possible.
    _write_drawio(tmp_path / "ok.drawio", [
        ("a", "Alpha", "1"), ("b", "Beta", "1"),
        ("c", "Cappa", "1"), ("e", "Extra", "1"),
    ])
    entry = {"id": "ok", "file": "ok.drawio", "zoneCount": 12,
             "serviceTokens": ["Alpha", "Beta", "Cappa", "Extra"],
             "scenarioAliases": []}
    result = sel.decide(entry, ["Alpha", "Beta", "Cappa", "Missing"],
                        recommended=True, templates_dir=tmp_path)
    assert result["decision"] == "scaffold-extend"
```

- [ ] **Step 2: Run tests to verify the first fails**

Run: `python3 -m pytest tests/test_select_template_coverage.py -q -k guard_generate`
Expected: FAIL — currently returns `scaffold-extend` (extra 3 > present 1 not yet guarded)

- [ ] **Step 3: Implement the guard**

In `decide()`, replace the decision branch (currently starting `if recommended and not missing and not extra:`) with a leading guard:

```python
    if len(extra) > len(present):
        # Gutting guard: stripping more than we keep means the template is the
        # wrong base (a hole-y layout; remove doesn't reflow/shrink frames).
        decision = "generate"
    elif recommended and not missing and not extra:
        decision = "scaffold"
    elif (recommended and coverage >= COVERAGE_MIN
          and (missing or extra) and heavy_guard):
        decision = "scaffold-extend"
    else:
        decision = "generate"
```

Update the `decide()` docstring's numbered list to note the guard: "0. ``generate`` — a *gutting guard*: if more template components would be removed than kept (`len(extra) > len(present)`), the template is the wrong base."

- [ ] **Step 4: Run the coverage suite**

Run: `python3 -m pytest tests/test_select_template_coverage.py -q`
Expected: PASS (existing decision/heavy-guard tests still green — their extra counts never exceed present)

- [ ] **Step 5: Commit**

```bash
git add scripts/select-template.py tests/test_select_template_coverage.py
git commit -m "feat(select-template): gutting guard — remove>keep forces generate (finding #1)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Plugin SKILL.md — recon/triage steps + gate fix

**Files:**
- Modify: `skills/sap-diagram-generate/SKILL.md`

Read the file first. Apply these prose edits (no code):

- [ ] **Step 1: Add Step 1.5 (recon)** after Step 1 ("Parse → draft inventory"):

> ### Step 1.5 — Template reconnaissance (early rank)
> Rank the corpus against the raw request so the candidate set is known before the
> interview: `select-template.py "<request>" --top 5 [--level Lx]`. Remember the top ids;
> the *triage* runs at Step 3.5 once best-practice findings exist.

- [ ] **Step 2: Add Step 3.5 (triage)** after Step 3 ("Consult SAP-domain skills"):

> ### Step 3.5 — Triage template extras into completeness suggestions
> Ask the selector which recurring components the top candidates have that the user
> did NOT mention:
> `select-template.py "<request>" --components "<draft csv>" --suggest --best-practice "<best-practice csv>" --json`
> Read `suggestions[]` — each is an extra that appears across ≥2 top candidates OR matches
> a Step-3 best-practice finding (Joule/AI-only-in-one-template noise is filtered). Feed
> these into the Step 4 interview; do NOT auto-add them.

- [ ] **Step 3: Enrich Step 4** — add a bullet:

> - **Completeness (from Step 3.5)** — present the `suggestions[]` as ONE multi-select
>   question: "these commonly appear in this architecture but you didn't mention them —
>   add any?", showing each suggestion's `reason`. Append accepted items to the inventory.

- [ ] **Step 4: Note the refined inventory at Step 5.5** — add a sentence: "Run the
  decision on the REFINED inventory (after Step 4), so promoted suggestions count as
  `present` and the gutting guard (`remove > keep → generate`) sees the true delta."

- [ ] **Step 5: Fix the corpus gate at BOTH sites.** Find the two blocks that invoke
  `score-diagram.py --corpus … --min-score 82` (one in the Step 5.5 gate description
  ~line 175/179, one in Step 8 item 2 ~line 307/312). Change them to be per-path:

> **Generate path** — the authoritative score gate is `score-diagram.py --sap-like "<out>" --json`;
> require `score ≥ 85`. Do **not** run `--corpus --min-score` here: a from-scratch diagram
> fingerprints ~55 against the corpus by design (the shipped gold demos score 55–60).
>
> **Scaffold / scaffold-extend path** — keep the dual gate: `--sap-like ≥ 85` **and**
> `score-diagram.py --corpus assets/templates "<out>" --min-score 82`.

Correct any prose claiming "a well-formed procedural diagram should clear 82".

- [ ] **Step 6: Verify flag consistency**

Run: `python3 scripts/select-template.py --help 2>&1 | grep -E "\-\-suggest|\-\-best-practice|\-\-components"`
Expected: all three flags present. Then eyeball that every `select-template` invocation in the SKILL uses only real flags.

- [ ] **Step 7: Commit**

```bash
git add skills/sap-diagram-generate/SKILL.md
git commit -m "docs(skill): plugin SKILL — recon (1.5) + triage (3.5) + per-path score gate

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Desktop SKILL.md — recon/triage (consensus-only) + gate note

**Files:**
- Modify: `packaging/claude-desktop-skill/SKILL.md`

Read the file first. Its numbering is 1/2/2.5/3/4/5/6/7 and it has NO best-practice step.

- [ ] **Step 1: Add recon + triage** into the interview step (§2) and hybrid decision (§2.5):
  same idea as the plugin, but the triage call omits `--best-practice` (there is no
  best-practice consult on Desktop), so it is **consensus-only**. Document that as an
  accepted degrade:

> Desktop has no best-practice consult, so completeness triage is consensus-only:
> `select-template.py "<request>" --components "<draft csv>" --suggest --json`. Present
> `suggestions[]` in the interview; never auto-add.

- [ ] **Step 2: Add the gutting-guard + refined-inventory note** to §2.5 (mirror the
  plugin's Step 5.5 note).

- [ ] **Step 3: Add a corpus scaffold-only note** to the gate step (§5): one line stating
  the corpus similarity gate applies only to scaffold/scaffold-extend; the generate path
  relies on `--sap-like ≥ 85` (the Desktop file already guards `--corpus` behind an
  unbundled corpus, so no command change is needed here).

- [ ] **Step 4: Commit**

```bash
git add packaging/claude-desktop-skill/SKILL.md
git commit -m "docs(skill): Desktop SKILL — consensus-only completeness triage + corpus-is-scaffold-only note

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Gate calibration test + regression + bundle + 0.7.0

**Files:**
- Create: `tests/test_gate_calibration.py`
- Modify: `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `CHANGELOG.md`

- [ ] **Step 1: Write the gate-calibration test**

```python
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""tests/test_gate_calibration.py — codifies finding #2: the generate path's
authoritative gate is --sap-like (>=85); --corpus is scaffold-only (procedural
diagrams fingerprint ~55 against the corpus by design)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCORE = ROOT / "scripts" / "score-diagram.py"
DEMOS = [ROOT / "demo" / "nova" / "nova-L1.drawio",
         ROOT / "demo" / "nova" / "nova-L2.drawio",
         ROOT / "demo" / "interactive" / "cap-bwz-bpa-L1.drawio",
         ROOT / "demo" / "replicas" / "task-center-L1.drawio"]


def _sap_like(path: Path) -> float:
    out = subprocess.run([sys.executable, str(SCORE), "--sap-like", str(path), "--json"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)["score"]


@pytest.mark.parametrize("demo", DEMOS, ids=lambda p: p.name)
def test_generate_path_demos_clear_sap_like_85(demo):
    assert demo.exists(), demo
    assert _sap_like(demo) >= 85.0, f"{demo.name} sap-like < 85 (generate-path gate)"
```

- [ ] **Step 2: Run it**

Run: `python3 -m pytest tests/test_gate_calibration.py -q`
Expected: PASS (demos score 93.7–100)

- [ ] **Step 3: Confirm the demos regenerate byte-identical** (regression already lives in
  `tests/test_scaffold_extend_integration.py::test_demos_byte_identical`):

Run: `python3 -m pytest tests/test_scaffold_extend_integration.py -q`
Expected: PASS. (This file's `test_scaffold_extend_dual_gate` also already covers the spec's "a scaffolded template clears `--corpus … --min-score 82`" assertion — no new scaffold-path test is needed here.)

- [ ] **Step 4: Full suite**

Run: `python3 -m pytest -q`
Expected: PASS (previous count + the new tests, 0 fail)

- [ ] **Step 5: Rebuild the Desktop bundle**

Run: `bash packaging/claude-desktop-skill/build.sh`
Expected: exits 0, ≤ 200 files, `brand-pack.local` excluded (`select-template.py` already bundled). Confirm: `unzip -l dist/claude-desktop-skill/sap-diagram-generate.zip | tail -1`.
Then run the automated bundle assertions rather than eyeballing: `python3 -m pytest tests/test_desktop_bundle.py -q` (enforces the file cap + `brand-pack.local` exclusion).

- [ ] **Step 6: Bump to 0.7.0 + CHANGELOG**

Edit `.claude-plugin/plugin.json` and the plugin entry in `.claude-plugin/marketplace.json` to `0.7.0`. Add a CHANGELOG block summarising template-informed completeness (recon 1.5 + triage 3.5 + enriched interview), the gutting guard (#1), and the per-path gate fix (#2).

- [ ] **Step 7: Commit**

```bash
git add tests/test_gate_calibration.py .claude-plugin/plugin.json .claude-plugin/marketplace.json CHANGELOG.md
git commit -m "test(gate): generate-path demos clear sap-like 85 (finding #2) + bump 0.7.0

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Remember
- Exact file paths; complete code in the plan; exact commands with expected output.
- DRY / YAGNI / TDD / frequent commits.
- Engine core untouched → the 4 demos MUST stay byte-identical (Task 6 Step 3 proves it).
- After all tasks: final full-feature code review → superpowers:finishing-a-development-branch (merge to main + push 0.7.0).
