---
name: sap-diagram-generate
description: Generate SAP-compliant draw.io architecture diagrams from a natural-language description. The skill FIRST grounds the content in authoritative SAP sources — it runs a dependency preflight (reference skills from secondsky/sap-skills + the mcp-sap-docs MCP), looks up every component in the SAP Discovery Center for the canonical name and category, consults the SAP-domain skills for best-practice completeness, and asks a focused set of questions — and ONLY THEN renders the diagram with the deterministic zone-composition engine. Use when the user asks to create, draw, generate, or build a SAP architecture / BTP solution diagram, mentions levels (L0/L1/L2/L3), or names SAP services (CAP, S/4HANA, BTP, Integration Suite, DOX, AI Core, Build Process Automation, Event Mesh, PCE, RISE).
argument-hint: "[L0|L1|L2|L3|combo] <description of the architecture>"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, AskUserQuestion, Skill, mcp__sap-docs__search, mcp__sap-docs__fetch, mcp__sap-docs__sap_discovery_center_search, mcp__sap-docs__sap_discovery_center_service
version: 0.3.0
---

# Generate a SAP-Compliant Architecture Diagram

Produce one or more `.drawio` files that follow the official [SAP BTP Solution Diagram Guideline](https://sap.github.io/btp-solution-diagrams/).

**The golden rule: ground the content before you draw.** A diagram is only as good as the inventory behind it. You cannot know which components a solution needs, their canonical names, or whether each is a *BTP service* or a *standalone SaaS product* by guessing — you must look it up in the SAP Discovery Center (via the `mcp-sap-docs` MCP) and check best-practice completeness with the SAP-domain skills. So the flow is always: **preflight → ground → consult → interview → confirm → generate → verify**. Never jump straight to rendering.

## When to invoke

- "Create a SAP architecture diagram for…", "Generate a BTP solution diagram showing…"
- "I need an L1 / L2 diagram of …", "Draw the architecture of <project>"
- "Make me a diagram with CAP backend on Kyma + S/4HANA + DOX"

Do **not** invoke for: editing an existing diagram (use `sap-diagram-validate` + manual edits), non-SAP diagrams, or PowerPoint/Lucid output (this emits draw.io only).

## Inputs

- **Level(s)** — `L0`, `L1`, `L2`, `L3`, or a combination. Default `L1` (confirm in the interview).
- **Description** — actors, BTP services, SAP/non-SAP systems, data flows.
- **Optional `auto` flag** — skip the interview (preflight + grounding still run).

---

## Procedure

### Step 0 — Preflight (dependency gate) — ALWAYS

Diagrams depend on the SAP reference skills (`secondsky/sap-skills` + `sap-pce-expert`) and the documentation MCP servers (`mcp-sap-docs`, …). Run the preflight and surface gaps before anything else:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/preflight.py" --need <concern-tags>
```

`<concern-tags>` are derived from the description (e.g. `cap,ai,onprem,identity,integration,observability,pce`). Read the report:

- If a **REQUIRED** item is missing (`sap-btp-best-practices` skill, or the `sap-docs` MCP) → tell the user the exact install command (`npx skills add secondsky/sap-skills`; for the MCP, point to `https://github.com/marianfoo/mcp-sap-docs` + `claude mcp add …`) and stop. Offer a **degraded mode** only with explicit consent (and an INFO that content grounding will be weaker).
- A *config-only* MCP check ("configured") is not proof of reachability — before relying on it, confirm an `mcp__sap-docs__*` tool actually returns. If the tools are unavailable in-session, treat the MCP as missing.
- Missing **recommended** items: note them and continue.

Also bootstrap the shape cache + index if needed:

```bash
test -d "${SAP_DIAGRAMS_CACHE:-$HOME/.cache/sap-diagrams-pro}/btp-solution-diagrams" || bash "${CLAUDE_PLUGIN_ROOT}/scripts/bootstrap-cache.sh"
test -f "${CLAUDE_PLUGIN_ROOT}/assets/shape-index.json" || python3 "${CLAUDE_PLUGIN_ROOT}/scripts/build-shape-index.py"
```

### Step 1 — Parse → draft inventory

From the description, extract a first-pass list: actors/users, third-party/non-SAP systems, SAP applications (S/4HANA, SuccessFactors, Ariba…), BTP services, and the data flows between them. This draft is a hypothesis — Steps 2–3 verify it.

### Step 2 — Ground every component in the SAP Discovery Center (MCP)

For each named or implied component, query the MCP so the diagram uses authoritative facts, not guesses:

```
mcp__sap-docs__sap_discovery_center_search(query="<service name>")
```

Use the result to capture, per component:

- **Canonical name** (`name`) — feed this verbatim into the IR `service` field so the icon resolves exactly (e.g. "Build Process Automation" → "SAP Build Process Automation").
- **Category** (`category` / `additionalCategories`) — this is how you classify the organism:
  - It's a **BTP service** (listed in the Discovery Center service catalog) → it goes inside the `btp-layer` group. Categories map to icon sets: *Application Development and Automation* → app-dev; *Integration* → integration-suite; *AI* → ai; *Data and Analytics* → data-analytics; *Foundation / Cross Services* → foundational.
  - It's a **standalone SaaS product** (S/4HANA, SuccessFactors, Ariba, Concur, Fieldglass, Signavio) → `sap-app` group (RIGHT zone). See [`references/component-groups.md`](references/component-groups.md) for the full BTP-vs-SaaS rule.
- **`isDeprecated`** — warn the user and suggest the replacement if true.

For capability/architecture facts (what a service does, required companions, recommended patterns) use `mcp__sap-docs__search` then `mcp__sap-docs__fetch` on the best hit, and `sap_discovery_center_service(serviceId=…)` for pricing/roadmap when relevant. Resolve aliases (BPA → SAP Build Process Automation, DOX → SAP Document Information Extraction, IAS → SAP Cloud Identity Services / Identity Authentication, XSUAA → SAP Authorization and Trust Management Service).

### Step 3 — Consult SAP-domain skills (best-practice completeness)

Invoke the relevant skills (in parallel — multiple `Skill` calls in one message) per the heuristics in [`references/sap-skills-integration.md`](references/sap-skills-integration.md). Always consult `sap-btp-best-practices`; add others by concern (`sap-btp-connectivity` for on-prem, `sap-btp-cloud-identity-services` for identity, `sap-btp-cloud-logging`/observability, `sap-btp-integration-suite`, `sap-pce-expert` for PCE, `sap-api-style` for clean-core C1 APIs, `sap-cap-capire` for CAP…).

Aggregate findings as `CRITICAL | WARNING | INFO` — typically *missing* components the inventory should include (Cloud Logging, Audit Log, Alert Notification, Cloud Connector, IAS↔XSUAA trust, Private Link for PCE).

### Step 4 — Interview the user (focused, derived questions)

Using `AskUserQuestion`, ask only what is still ambiguous **after** Steps 2–3 (don't ask what the docs/skills already answered). Typical questions (batch 2–4 multiple-choice):

- **Level(s)** — L0 / L1 / L2 / combo (default L1).
- **Runtime** — Cloud Foundry / Kyma / both / ABAP.
- **Identity** — IAS + XSUAA only / external IdP federated via IAS / corporate SAML.
- **Integration style** — synchronous (OData/REST) / asynchronous (events) / mixed.
- **Backends** — S/4HANA on-prem (PCE) / S/4HANA Cloud / non-SAP DBs / which.
- **Connectivity** — Cloud Connector / Private Link / direct.
- **Observability scope** — Cloud Logging only / + Audit Log + Alert Notification + Cloud ALM.
- **Branding** — ask explicitly whether to add a **partner watermark** and/or a **customer logo**. **Default is NONE — never assume a company or apply a watermark on your own** (do not default to "Lutech" or any partner). If the user says yes, ask them to **provide the image** (paste/attach the logo file, or point to a path). Save it under `assets/brand-pack.local/` (gitignored — trademarks/customer assets stay local) with a short key, add it to that pack's `index.json` as a `dataUri`, and only then set `metadata.branding.partnerWatermark` / `branding.customerLogo` to that key. If the user declines or provides nothing, omit `branding` entirely.

### Step 5 — Confirm the inventory

Present the consolidated inventory (canonical names from Step 2) + best-practice findings (Step 3) + the interview answers, and offer three choices: **accept** · **apply best-practice suggestions** · **amend manually** (then re-run Step 3 on the edits). Wait for the answer. Only proceed once confirmed (or `auto` was given).

### Step 5.5 — Scaffold, scaffold-extend, or generate (hybrid decision) — ALWAYS

Before authoring any IR, decide **how** to produce the diagram. Feed the selector
the **confirmed canonical component list** from Step 5 and read its `decision`:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/select-template.py" "<request>" \
  --components "<confirmed canonical components, comma-separated>" --json [--level L2]
```

The JSON carries `decision` ∈ {`scaffold`, `scaffold-extend`, `generate`}, a
coverage report (`present` / `missing` / `extra` — each extra tagged
`light`|`heavy`), the top `template` id, `recommended`, `heavyGuardOk`, and a
bounded `delta` = `{remove: ["<label>", …], relabel: [{from, to}, …], add: ["<label>", …]}`.
The thresholds behind the decision (`RECOMMEND_THRESHOLD=14`, `COVERAGE_MIN=0.4`,
`HEAVY_EXTRA_MAX=1` + the `zoneCount/3` clause) are documented in
[`references/scaffold-workflow.md`](references/scaffold-workflow.md). **Branch on `decision`:**

**`scaffold`** (relabel-only — the existing pure path: `★ recommended`, nothing
`missing`, nothing `extra`). Copy the template and apply the relabels; adapt
**surgically — never redraw**:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/scaffold-diagram.py" --template <template> --out "<out>.drawio"
# one relabel.py call per delta.relabel entry (preserves geometry/style/ids, writes a .bak):
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/relabel.py" "<out>.drawio" --replace "<from>=<to>"
```

Swap service icons with the `sap-icons-resolve` skill where the checklist calls
for it. Do **not** author an IR. Then go straight to the **Step 8 gate**.

**`scaffold-extend`** (base template + delta — `★ recommended`, `coverage ≥ 0.4`,
at least one `missing`/`extra`, heavy guard holds). Scaffold, **snapshot the whole
starting template**, then apply the delta as an **ordered chain**:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/scaffold-diagram.py" --template <template> --out "<out>.drawio"
cp "<out>.drawio" "<out>.drawio.pre-extend.bak"          # whole-chain snapshot
```

1. **Remove** each `delta.remove` — **heavy extras first** (they free the most space):
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/remove-cell.py" "<out>.drawio" --match "<label>"   # or --id <cellId>
   ```
2. **Relabel** each `delta.relabel` (as in the `scaffold` path).
3. **Add** each `delta.add` — a node plus an edge to wire it in. Claude picks the
   placement mode per component: `--mode append` for a group member (localized
   reflow of that group), or `--mode slot --near <refCellId>` for the nearest free
   slot (least invasive). `add-node.py --json` returns the new cell id for wiring:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/add-node.py" "<out>.drawio" --group <groupId> \
     --label "<…>" --service "<canonical name>" --mode append --json
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/add-edge.py" "<out>.drawio" --source <id> --target <id> \
     --flowFamily <family> --pill "<protocol>" --label "<…>"
   ```

**Run `check-composition.py` after each structural edit** (add-node / add-edge /
heavy remove) so a new FAIL is attributed to the edit that caused it:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/check-composition.py" "<out>.drawio"
```

On a FAIL: revert that one edit via its `.bak` and retry it with a different
placement/port (`--mode slot --near …`, or different endpoint ports) — **max 2
retries per edit**. Never hand-edit geometry. If the chain still can't converge,
**restore the snapshot** (`cp "<out>.drawio.pre-extend.bak" "<out>.drawio"`) and
fall through to the **`generate`** path. Then go to the **Step 8 gate**.

**`generate`** (nothing clears the bar). Author the IR — proceed to **Step 6** as usual.

**Authoritative score gate (enforced identically here and in the integration
test).** Whichever path produced the artifact, it must clear **both**:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/score-diagram.py" --sap-like "<out>.drawio" --json                                    # SAP-likeness ≥ 85
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/score-diagram.py" --corpus "${CLAUDE_PLUGIN_ROOT}/assets/templates" "<out>.drawio" --min-score 82
```

A scaffolded / scaffold-extended diagram keeps the template's structure, so it
clears both comfortably; a low score on the `generate` path means the IR drifted
from SAP conventions. This dual score gate is the score portion of the **same
downstream gate** all three paths converge on (Step 8): `validate-drawio.py --strict`
+ `check-composition.py` + this dual score gate + the visual-rubric loop.

### Step 6 — Build the IR v2 (authoring grammar)

Compose one JSON object per level. See [`examples/`](examples/) for worked v1 patterns and [`tests/fixtures/ir-v2-sample.json`](../../tests/fixtures/ir-v2-sample.json) for the full v2 archetype this step is grounded in. IR v2 is a **strict superset** of v1 — every v1 field still works; the fields below are additive.

**`metadata`**

| field | type | purpose |
|---|---|---|
| `title`, `level`, `author` | string | as v1 |
| `iconSize` | `S`\|`M`\|`L` (optional) | default service-icon render size |
| `branding.customerLogo` | string (optional) | ref into `assets/brand-pack(.local)/`; renders top-left, next to the title. **Set ONLY when the user asked for it in Step 4 and provided the asset** — never embed a logo you don't have explicit rights to use (see the confidentiality rule for customer logos). Omit otherwise |
| `branding.partnerWatermark` | string (optional) | large, low-contrast background image ref. **Set ONLY when the user opted in (Step 4) and supplied the image** — never default to a partner (e.g. Lutech). Omit otherwise |
| `badges.hyperscalers` / `badges.runtimes` | `[string, ...]` (optional) | diagram-level badge strip (same shape as a group's `badges`) |
| `networkSeparator` | bool (default `true`) | draws the vertical grey NETWORK bar between the BTP center and any RIGHT-zone tier; leave it on whenever a `cloud-tier`/`sap-app`/`non-sap`/`third-party`/`external` group sits outside the BTP frame, set `false` only when there is nothing on the right to separate from |
| `layoutHints` | `[]` (top-level, sibling of `metadata`) | the 7-op patch vocabulary — see [`references/visual-rubric.md`](references/visual-rubric.md). **Leave this empty at authoring time.** It exists for Step 8's vision loop to fill in; hand-authoring a hint here almost always means the real fix belongs in `zone`/`flow`/`type` instead |

**`groups[]` — `type` drives the molecule automatically** (no manual presets needed):

| `type` | Zone | Rendered as |
|---|---|---|
| `user` | LEFT | **frameless** person/device icon + label (no box) |
| `btp-layer` | CENTER | blue `#EBF8FF` container with a **"SAP BTP" logo chip**; nested lanes = white inner frames |
| `subaccount` | CENTER (nested inside `btp-layer` via `parent`) | white BTP-bordered inner frame labelled "Subaccount: …"; **nestable** — set a `subaccount` group's `parent` to another `subaccount` id to model containment (e.g. `Extension Test` ⊃ `Extension Production`) |
| `governance` | TOP (own band above the BTP frame) | wide BTP-blue strip spanning the canvas width, for cross-cutting governance/monitoring products (e.g. Cloud ALM) |
| `cloud-tier` | RIGHT | a labelled tier box; set `kind: "public"\|"private"\|"any-premise"` — `public`/`private` render with the SAP-blue border (`tier-box-sap`), `any-premise` renders grey (`tier-box-nonsap`) unless the tier is itself SAP-managed |
| `custom-app` | RIGHT (or wherever `zone` places it) | BTP-blue **product-style** card for a bespoke application built on BTP (distinct from a `sap-app`, which is a SAP-shipped product) |
| `sap-app` | RIGHT | white backend **box** with icon-left + title (+`subtitle`), BTP-blue border |
| `third-party` / `non-sap` / `external` | RIGHT | white backend **box**, grey border |

Every group also accepts `badges: {hyperscalers: [...], runtimes: [...]}` (rendered as small logo badges on the group, typically on `subaccount`/`cloud-tier`), `parent` (nesting), `position` (`top-left`…`bottom-right`, mapped to column+band), `zone` (`left`\|`center`\|`right`, overrides the column), and `flow` (`row`\|`col`\|`grid`, intra-group packing).

**Layout is deterministic** (the skeleton slot engine `scripts/_skeleton_layout.py`, no graphviz dependency): `position` maps to a column (LEFT/CENTER/RIGHT) + band (top/middle/bottom); containers auto-size to their contents.

**`nodes[]`**

| field | type | purpose |
|---|---|---|
| `service` | string | canonical Discovery-Center name → icon |
| `genericIcon` | string | `user`/`mobile`/`desktop`/`database`/… when there's no service icon |
| `type` | `product`\|`chip`\|`db` (optional) | `product` — a leaf molecule with a `capabilities` grid instead of child nodes (e.g. "SAP Build Process Automation" with Workflow/Decision/Visibility/RPA chips); `chip` — a small white BTP-bordered label chip (e.g. a PCE/runtime marker inside a `cloud-tier`); `db` — the cylinder datastore molecule |
| `capabilities` | `[{label, icon?}, ...]` (only on `type: "product"`) | rendered as an icon+label grid inside the product box; `icon` is optional (bare-label capabilities render text-only) |
| `subtitle` | string | one-line caption under the title (backend-box / product-box molecules) |
| `interface` | `sap`\|`generic` | "Interface" pill at the top of the node |
| `step` / `stepKind` | int (1–99) / color name | numbered step circle |
| `boxStyle` | string | fallback box variant when no icon resolves |

**`edges[]`**

| field | type | purpose |
|---|---|---|
| `style` | `solid`\|`dashed`\|`dotted`\|`thick` (v1) | line style |
| `kind` | `trust`\|`authenticate`\|`authorize`\|`generic_protocol`\|`annotation`\|`positive`\|`critical`\|`negative`\|`default` (v1) | canonical SAP pill/color when the edge doesn't need a `flowFamily` |
| `flowFamily` | `identity`\|`provisioning`\|`master-data`\|`transport`\|`firewall`\|`default` (v2) | selects one of the six `edge-*` style-contract molecules (colour + dash family) — use this over `kind` whenever the edge represents one of these semantic flows; `firewall` renders `strokeWidth=3` |
| `pill` | string (v2) | free-text protocol/annotation label rendered as a pill on the edge (e.g. `"SAML2/OIDC"`, `"SCIM"`, `"CTMS"`) — independent of `kind`/`pillColor` |

Use kebab-case IDs throughout.

**Identity placement.** The identity cluster (IAS / XSUAA / Authorization) is never folded into a generic ops/third-party box. If it's parented to the BTP frame (`parent: "<btp-group-id>"`), it nests inside as its own labelled BTP-blue inner frame near the bottom. If it isn't parented (a standalone top-level group), give it its own `btp-layer`-typed group positioned just below the main BTP frame — never place it on the RIGHT with the backends.

**Worked example (archetype A)** — governance strip + nested subaccounts + a `product` node with capabilities + `cloud-tier`s (public/private/any-premise) + `flowFamily` edges + branding (adapted from `tests/fixtures/ir-v2-sample.json`; trimmed here for readability):

```json
{
  "metadata": {
    "title": "Archetype A", "level": "L1", "author": "…",
    "branding": {"customerLogo": "acme", "partnerWatermark": "lutech"},
    "badges": {"hyperscalers": ["azure"], "runtimes": ["cloud-foundry"]}
  },
  "layoutHints": [],
  "groups": [
    {"id": "governance", "type": "governance", "label": "Governance", "position": "top"},
    {"id": "btp", "type": "btp-layer", "label": "SAP BTP", "position": "center"},
    {"id": "subaccount-test", "type": "subaccount", "label": "Test", "parent": "btp"},
    {"id": "subaccount-production", "type": "subaccount", "label": "Production", "parent": "subaccount-test"},
    {"id": "cloud-tier-right", "type": "cloud-tier", "label": "Private Cloud", "position": "right", "kind": "private"},
    {"id": "personas", "type": "user", "label": "Personas", "position": "left"},
    {"id": "identity", "type": "btp-layer", "label": "Identity", "position": "bottom"}
  ],
  "nodes": [
    {"id": "cloud-alm", "label": "Cloud ALM", "group": "governance", "type": "product", "service": "Cloud ALM",
     "capabilities": [{"label": "Monitor", "icon": "monitor"}, {"label": "Analyze"}, {"label": "Automate", "icon": "automate"}, {"label": "Alert"}]},
    {"id": "bpa", "label": "Build Process Automation", "group": "subaccount-production", "type": "product", "service": "Build Process Automation",
     "capabilities": [{"label": "Workflow", "icon": "workflow"}, {"label": "Decision"}, {"label": "Visibility", "icon": "visibility"}, {"label": "RPA"}]},
    {"id": "pce", "label": "Private Cloud Edition (PCE)", "group": "cloud-tier-right", "type": "chip"},
    {"id": "persona-admin", "label": "IT Admin", "group": "personas", "genericIcon": "user"},
    {"id": "ias", "label": "Identity Authentication", "group": "identity", "service": "Identity Authentication"}
  ],
  "edges": [
    {"id": "e1", "source": "persona-admin", "target": "ias", "style": "solid", "label": "Login", "flowFamily": "identity", "pill": "SAML2/OIDC"},
    {"id": "e2", "source": "cloud-alm", "target": "bpa", "style": "dashed", "label": "Process insights", "flowFamily": "master-data"},
    {"id": "e3", "source": "bpa", "target": "pce", "style": "solid", "label": "Deploy config", "flowFamily": "transport", "pill": "CTMS"}
  ]
}
```

See [`references/atomic-design.md`](references/atomic-design.md) and [`references/component-groups.md`](references/component-groups.md) for how each of these maps to a molecule/organism in the style contract.

### Step 7 — Validate the IR

Before generating anything, run the IR grammar gate:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/validate-ir.py" /tmp/diagram-<level>.json
```

- **Exit 0** (`OK`) — proceed to Step 8.
- **Exit 2** — one or more `ERROR <where>: <what>.` lines (with `Allowed: <...>` when there's a fixed vocabulary). Read the actionable error, fix the IR (wrong enum value, dangling `parent` reference, malformed `capabilities`/`badges`/`branding` shape, cyclic group parenting…), and re-validate. Do not call `generate-drawio.py` until this exits 0.

### Step 8 — Generate + gate + visual-rubric loop

1. **Generate**

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/generate-drawio.py" /tmp/diagram-<level>.json --out "<output_dir>/<title>-<level>.drawio"
   ```

   Default `<output_dir>` is `./diagrams/` (override via `.claude/sap-diagrams-pro.local.md`).

2. **Gate — mechanical checks, must be green before any visual pass**

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/validate-drawio.py"   "<out>.drawio" --strict
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/check-composition.py" "<out>.drawio"
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/score-diagram.py" --corpus "${CLAUDE_PLUGIN_ROOT}/assets/templates" "<out>.drawio" --min-score 82
   ```

   - `validate-drawio.py --strict` — XML structure, Horizon palette, line styles, orphan edges; exits 1 if any CRITICAL is found.
   - `check-composition.py` — the **geometric** gate (zone overlaps, piercings, crossing budget once computed, legend presence); exits 2 on any FAIL.
   - `score-diagram.py --corpus … --min-score 82` — SAP-likeness fingerprint vs the whole template corpus; exits 2 when the best match scores below 82. **All three paths must clear it** (see Step 5.5's authoritative dual score gate) — a scaffolded / scaffold-extended diagram fingerprints ~98 (it keeps the SAP template's structure), and a well-formed procedural diagram should clear 82 too; a low score on the generate path means the IR drifted from SAP conventions. (If the corpus is absent, this step is a no-op — skip it.)

   On any CRITICAL/FAIL/low-score: **generate path** — fix the IR and regenerate; **scaffold path** — undo via the `.bak` and redo the `relabel.py` edits (or pick an alternate template), never hand-edit geometry. **Max 2 mechanical retries** before escalating to the user with the exact error.

3. **Render**

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render-preview.py" "<out>.drawio" --engine auto --out "<out>.png"
   ```

   `--engine auto` (default) picks the draw.io desktop CLI when present, else `_pure_render.py` (Pillow-based) — see the degrade path below if neither is available.

4. **Look — read the PNG and evaluate every check in [`references/visual-rubric.md`](references/visual-rubric.md)** (26 binary checks across Composition/Routing/Typography/Semantics). Emit findings JSON, one object per failing check:

   ```json
   [{"rule": "route-no-pierce", "location": "edge 'e3' cuts through the BPA box", "patch": {"op": "channel_prefer", "edge": "e3", "value": "V2"}}]
   ```

   `patch` is one of the 7 ops (`references/visual-rubric.md`'s table) or `null` for a manual/content finding (recolor, icon swap, legend content…) — surface `null`-patch findings to the user in the final report; never invent an 8th op.

5. **Patch + regenerate + re-render**

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apply-rubric-patches.py" /tmp/diagram-<level>.json --findings /tmp/findings-<n>.json
   # then repeat steps 1–4 on the same IR
   ```

6. **Repeat the look → patch → regenerate loop at most 3 vision iterations.** Deliver only when `check-composition.py` reports 0 FAIL **and** every auto-checkable rubric item passes (surface remaining manual/content items to the user as a punch list — a `null` patch still counts against "green" but is not a blocker on its own). Include a **scorecard** in the final report:

   ```
   Gate: 0 FAIL / 0 WARN (check-composition) · 0 CRITICAL (validate-drawio)
   Rubric: 24/26 pass (2 manual findings outstanding: sem-icons-match, comp-legend-present)
   Crossings: 3 · Piercings: 0 · Vision iterations used: 2/3
   ```

   The user may explicitly override and ask to deliver despite a residual WARN/manual finding — honour it, but log the override and the residual list in the report; never silently drop it.

7. **Degrade path (no render engine available).** If `render-preview.py --engine auto` cannot produce a PNG (no draw.io launcher **and** Pillow missing, so `--engine pure` also fails) skip the vision loop entirely — run the geometric gate only (steps 1–2) and say so explicitly in the report as a **WARNING** ("visual rubric skipped: no render engine available — install draw.io desktop or `pip install pillow`"). Never dead-end: deliver the `.drawio` with the gate result and the warning.

### Step 9 — Report

Per file: path, level, element counts, the gate scorecard (validator + composition + rubric, per Step 8.6), and the PNG path if rendered. Call out any degrade-path WARNING (Step 8.7) and any user-approved override explicitly. For multi-level, note cross-level consistency. Suggest opening in draw.io desktop / [drawio.com](https://drawio.com) / VS Code draw.io extension, and (when relevant) SAP Architecture Center submission.

## Configuration

`.claude/sap-diagrams-pro.local.md` (project) or `~/.claude/…`: `btp_repo_path`, `arch_center_repo_path`, `default_level`, `output_dir`, `validation_strictness`, `auto_consult_skills`.

## References

- [`references/scaffold-workflow.md`](references/scaffold-workflow.md) — the hybrid scaffold / scaffold-extend / generate decision, the coverage report + thresholds, the selector's scoring formula, the ordered extend workflow, and the surgical relabel rules (Step 5.5).
- [`references/interactive-workflow.md`](references/interactive-workflow.md) — the full preflight → ground → interview flow.
- [`references/sap-skills-integration.md`](references/sap-skills-integration.md) — which SAP skills + MCP to consult, per concern.
- [`references/component-groups.md`](references/component-groups.md) — organisms + the BTP-service vs SaaS-product rule.
- [`references/atomic-design.md`](references/atomic-design.md), [`references/horizon-palette.md`](references/horizon-palette.md), [`references/line-styles-spacing.md`](references/line-styles-spacing.md), [`references/levels-l0-l1-l2.md`](references/levels-l0-l1-l2.md), [`references/shape-libraries-index.md`](references/shape-libraries-index.md).
- [`references/visual-rubric.md`](references/visual-rubric.md) — the 26 binary checks + 7-op patch vocabulary driving Step 8's vision loop.

## Quality bar

A "good" diagram: opens cleanly in draw.io; `validate-ir.py` exits 0; 0 validator CRITICAL and 0 composition FAIL; every auto-checkable [`visual-rubric.md`](references/visual-rubric.md) item passes (remaining manual findings disclosed); uses canonical Discovery-Center names; right level (L0 ≤ 10 / L1 10–30 / L2 ≥ 30 elements); every edge labelled; consistent across levels. If two criteria are missed, regenerate before delivering.

## L3 extension — non-standard

L3 is a plugin extension for deployment-runtime views (K8s pods, ingress, PVCs). It is **not** part of the official SAP guideline — flag it as non-canonical for SAP Architecture Center submissions. Use for internal runbooks only.
