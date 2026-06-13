---
name: sap-diagram-generate
description: Generate SAP-compliant draw.io architecture diagrams from a natural-language description. The skill FIRST grounds the content in authoritative SAP sources — it runs a dependency preflight (reference skills from secondsky/sap-skills + the mcp-sap-docs MCP), looks up every component in the SAP Discovery Center for the canonical name and category, consults the SAP-domain skills for best-practice completeness, and asks a focused set of questions — and ONLY THEN renders the diagram with the deterministic zone-composition engine. Use when the user asks to create, draw, generate, or build a SAP architecture / BTP solution diagram, mentions levels (L0/L1/L2/L3), or names SAP services (CAP, S/4HANA, BTP, Integration Suite, DOX, AI Core, Build Process Automation, Event Mesh, PCE, RISE).
argument-hint: "[L0|L1|L2|L3|combo] <description of the architecture>"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, AskUserQuestion, Skill, mcp__sap-docs__search, mcp__sap-docs__fetch, mcp__sap-docs__sap_discovery_center_search, mcp__sap-docs__sap_discovery_center_service
version: 0.2.0
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

### Step 5 — Confirm the inventory

Present the consolidated inventory (canonical names from Step 2) + best-practice findings (Step 3) + the interview answers, and offer three choices: **accept** · **apply best-practice suggestions** · **amend manually** (then re-run Step 3 on the edits). Wait for the answer. Only proceed once confirmed (or `auto` was given).

### Step 6 — Build the JSON IR

Compose one JSON object per level. See [`examples/`](examples/) for worked patterns.

```json
{
  "metadata": {"title": "…", "level": "L0|L1|L2|L3", "author": "…", "iconSize": "S|M|L (optional)"},
  "groups": [
    {"id": "users",  "type": "user",       "position": "top-left"},
    {"id": "btp",    "type": "btp-layer",  "position": "center", "label": "SAP BTP"},
    {"id": "in",     "type": "btp-layer",  "parent": "btp", "label": "Inbound", "flow": "row"},
    {"id": "s4",     "type": "sap-app",    "position": "right", "label": "Backends"}
  ],
  "nodes": [
    {"id": "u1",  "label": "End User", "group": "users"},
    {"id": "cap", "label": "CAP Backend", "service": "SAP BTP Cloud Foundry Runtime", "group": "btp-core", "interface": "sap", "step": 1},
    {"id": "s4h", "label": "S/4HANA", "subtitle": "Private Cloud Edition", "group": "s4"}
  ],
  "edges": [
    {"id": "e1", "source": "u1", "target": "cap", "style": "solid", "label": "Authenticate", "kind": "authenticate"}
  ]
}
```

**Group `type` drives the molecule automatically** (no manual presets needed):

| `type` | Zone | Rendered as |
|---|---|---|
| `user` | LEFT | **frameless** person/device icon + label (no box) |
| `btp-layer` | CENTER | blue `#EBF8FF` container with a **"SAP BTP" logo chip**; nested lanes = white inner frames |
| `sap-app` | RIGHT | white backend **box** with icon-left + title (+`subtitle`), BTP-blue border |
| `third-party` / `non-sap` / `external` | RIGHT | white backend **box**, grey border |

**Layout is deterministic** (the zone engine `scripts/_zone_layout.py`): the `position` field (`top-left`…`bottom-right`) maps to a column (LEFT/CENTER/RIGHT) + band (top/middle/bottom); containers auto-size to their contents. Use `zone` to override the column and `flow` (`row`|`col`|`grid`) to override intra-group packing. **There is no graphviz dependency.**

Node options: `service` (canonical name → icon), `genericIcon` (user/mobile/desktop/database/…), `subtitle`, `interface` (`sap`|`generic`), `step` (1–99) + `stepKind`, `boxStyle` (fallback when no icon). Edge `kind`: `trust`/`authenticate`/`authorize`/`generic_protocol`/`annotation` render canonical pills; `style`: solid/dashed/dotted/thick. Use kebab-case IDs.

### Step 7 — Generate

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/generate-drawio.py" /tmp/diagram-<level>.json --out "<output_dir>/<title>-<level>.drawio"
```

Default `<output_dir>` is `./diagrams/` (override via `.claude/sap-diagrams-pro.local.md`).

### Step 8 — Verify (XML + composition + visual)

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/validate-drawio.py"   "<out>.drawio"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/check-composition.py" "<out>.drawio"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render-preview.py"    "<out>.drawio" --out "<out>.png"
```

- `validate-drawio.py` — XML structure, Horizon palette, line styles, orphan edges.
- `check-composition.py` — zone overlaps (FAIL), title band, columns, legend presence.
- `render-preview.py` — PNG preview (best-effort; skipped gracefully if draw.io isn't installed).

If a CRITICAL (validator) or FAIL (composition) is reported, fix the IR and regenerate before announcing completion.

### Step 9 — Report

Per file: path, level, element counts, validator + composition summary, and the PNG path if rendered. For multi-level, note cross-level consistency. Suggest opening in draw.io desktop / [drawio.com](https://drawio.com) / VS Code draw.io extension, and (when relevant) SAP Architecture Center submission.

## Configuration

`.claude/sap-diagrams-pro.local.md` (project) or `~/.claude/…`: `btp_repo_path`, `arch_center_repo_path`, `default_level`, `output_dir`, `validation_strictness`, `auto_consult_skills`.

## References

- [`references/interactive-workflow.md`](references/interactive-workflow.md) — the full preflight → ground → interview flow.
- [`references/sap-skills-integration.md`](references/sap-skills-integration.md) — which SAP skills + MCP to consult, per concern.
- [`references/component-groups.md`](references/component-groups.md) — organisms + the BTP-service vs SaaS-product rule.
- [`references/atomic-design.md`](references/atomic-design.md), [`references/horizon-palette.md`](references/horizon-palette.md), [`references/line-styles-spacing.md`](references/line-styles-spacing.md), [`references/levels-l0-l1-l2.md`](references/levels-l0-l1-l2.md), [`references/shape-libraries-index.md`](references/shape-libraries-index.md).

## Quality bar

A "good" diagram: opens cleanly in draw.io; 0 validator CRITICAL and 0 composition FAIL; uses canonical Discovery-Center names; right level (L0 ≤ 10 / L1 10–30 / L2 ≥ 30 elements); every edge labelled; consistent across levels. If two criteria are missed, regenerate before delivering.

## L3 extension — non-standard

L3 is a plugin extension for deployment-runtime views (K8s pods, ingress, PVCs). It is **not** part of the official SAP guideline — flag it as non-canonical for SAP Architecture Center submissions. Use for internal runbooks only.
