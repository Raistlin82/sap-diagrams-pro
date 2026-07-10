# Hybrid scaffold-or-generate workflow

The engine can produce a SAP diagram two ways. This reference documents the
decision, the selector's scoring formula, and the confidence threshold that
routes between them.

## The two paths

| Path | How | When |
|---|---|---|
| **Scaffold** | Copy the closest real SAP reference `.drawio`, then edit it surgically (relabel cells, swap icons). Inherits the exact canvas, zones, Horizon palette, fonts and icons of a real SAP diagram → highest fidelity. | A real template in the corpus is *close enough* to the request (selector score clears the threshold). |
| **Generate** | Author an IR v2 and render it procedurally with `generate-drawio.py`. Full control, arbitrary topology. | No template is close enough — the request is novel or a combination no single template covers. |

Both paths converge on the **same downstream gate**: `validate-drawio.py --strict`
+ `check-composition.py` + `score-diagram.py --corpus … --min-score 82` + the
visual-rubric vision loop. A diagram is only delivered when that gate is green.

## Decision procedure

1. `select-template.py "<request>" --top 5 [--level L2]` ranks the 156 entries in
   `assets/template-index.json`.
2. If the top candidate is flagged **`★ recommended`** (score ≥ `RECOMMEND_THRESHOLD`)
   → **scaffold**: `scaffold-diagram.py "<request>" --out <f>.drawio` copies it and
   prints a relabel checklist; adapt with `relabel.py` + icon swaps.
3. Else (`scaffold-diagram.py` exits `3`) → **generate**: author the IR.

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

## Corpus score gate (`--min-score 82`)

`score-diagram.py --corpus assets/templates <out> --min-score 82` fingerprints the
candidate (structure + Horizon style, not literal content) against every reference
and takes the best match. Empirically:

- a verbatim template copy scores **100**,
- a scaffolded **+ relabelled** diagram scores **~98** (relabelling changes text,
  not structure),
- so **82** is a safe floor that a real scaffold always clears while still failing
  a diagram whose structure has drifted from SAP conventions. It applies to the
  procedural path too — a low score there means the IR wandered off-convention.

## Surgical relabel rules

`relabel.py` is how the scaffold path adapts a copied template **without
redrawing**:

- `--set <cellId>=<new label>` — address a cell by its `id`.
- `--replace "<old>=<new>"` — match a cell by its rendered visible text (HTML
  stripped, `<br>` → space, entities unescaped, whitespace collapsed).

Both preserve one simple inline wrapper (`<b>/<i>/<u>/<font>/<span>`) so
colour/formatting survive a text swap, and touch **only** the `value`/`label`
attribute — `mxGeometry`, `style`, `id`, `source`/`target` are left exactly as
parsed. In-place edits write a `.bak`; use `--out` to write elsewhere. Never
hand-edit geometry — that is what breaks SAP fidelity. If a relabel goes wrong,
restore the `.bak` and redo.
