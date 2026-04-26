---
name: sap-diagram-generate
description: Generate one or more SAP-compliant draw.io architecture diagrams from a natural-language description, after walking the user through an interactive confirmation flow that consults SAP-domain skills (sap-btp-best-practices, sap-btp-connectivity, sap-pce-expert, etc.) for best-practice validation. Use when the user asks to create, draw, generate, build a SAP architecture diagram, BTP solution diagram, reference architecture, or mentions levels (L0/L1/L2/L3) and SAP services like CAP, S/4HANA, BTP, Integration Suite, DOX, AI Core, Build Process Automation, Event Mesh, PCE, RISE.
argument-hint: "[L0|L1|L2|L3|combo] <description of the architecture>"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
version: 0.1.0
---

# Generate a SAP-Compliant Architecture Diagram (Interactive)

Produce one or more `.drawio` files that follow the official [SAP BTP Solution Diagram Guideline](https://sap.github.io/btp-solution-diagrams/) and embed best-practice findings from canonical SAP-domain skills. Walk the user through a confirmation flow before generating — never silently produce a diagram from a one-line description.

## When to invoke this skill

Trigger on user requests like:

- "Create a SAP architecture diagram for…"
- "Generate a BTP solution diagram showing…"
- "I need a L1 / L2 / L3 diagram of …"
- "Draw the architecture of NOVA Invoice Suite"
- "Make me a diagram with CAP backend on Kyma + S/4HANA + DOX"

Do **not** invoke for: editing existing diagrams (use `sap-diagram-validate` then manual edits), generating non-SAP diagrams, or producing PowerPoint/Lucid output (this skill emits draw.io only).

## Inputs

- **Level(s)** — `L0`, `L1`, `L2`, `L3`, or any combination. Defaults to `L1` if user does not specify; ask for confirmation in the interactive flow.
- **Description** — natural-language sentence(s) listing user/3rd-party actors, BTP services, S/4HANA / non-SAP systems, and key data flows.
- **Optional title** — diagram title; default derives from the description.
- **Optional `auto` flag** in the user's prompt — skip the interactive confirmation. Best-practice consultation still runs.

## Procedure (10 steps)

The full flow is documented in [`references/interactive-workflow.md`](references/interactive-workflow.md). Summary:

### Step 1 — Bootstrap the cache (one-time)

```bash
test -d "${SAP_DIAGRAMS_CACHE:-$HOME/.cache/sap-diagrams-pro}/btp-solution-diagrams" || \
  bash "${CLAUDE_PLUGIN_ROOT}/scripts/bootstrap-cache.sh"
```

Refresh `assets/shape-index.json` if missing or older than 7 days:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/build-shape-index.py"
```

### Step 2 — Parse and propose component inventory

From the user's description, extract:

- **Actors / users** (group type `user`)
- **Third-party / non-SAP systems** (group type `third-party` / `non-sap`)
- **SAP applications** (group type `sap-app` — S/4HANA, SuccessFactors, Ariba)
- **BTP services** (group type `btp-layer` — CAP, DOX, Build Apps, …)
- **Data flows** between them (sync vs async, request-response vs event-driven)

For each named SAP service, look it up via the `sap-icons-resolve` skill (or directly in `assets/shape-index.json`). Resolve aliases (BPA → Build Process Automation; DOX → Document Information Extraction).

Present the inventory as a structured table. **Do not generate the diagram yet.**

### Step 3 — Consult SAP-domain skills (best-practice gate)

Apply the trigger heuristics in [`references/sap-skills-integration.md`](references/sap-skills-integration.md) to decide which SAP skills to invoke. Always invoke `sap-btp-best-practices` and (if applicable) `sap-btp-cloud-logging`.

Aggregate findings from all consulted skills into one report, classified `CRITICAL` / `WARNING` / `INFO`.

If a referenced skill is not installed locally, skip it gracefully and emit an INFO note pointing the user to install it (`npx skills add secondsky/sap-skills@<skill>`).

### Step 4 — Present inventory + findings to the user

Show the user:

1. The component inventory (from Step 2).
2. The best-practice findings (from Step 3).
3. The detected level (from element count) and ask whether to use it or pick differently.
4. Three explicit choices:
   - **Accept as detected** — proceed.
   - **Apply best-practice suggestions** — extend the inventory with the findings before generating.
   - **Amend manually** — let the user remove / add / rename components.

Wait for the answer. If the user picks "Amend manually", iterate (re-run Step 3 after their edits).

### Step 5 — Confirm level(s)

If the user has not yet committed to a level (or asked for multiple), present:

- `L0` — Executive overview (5-10 boxes, no technical detail).
- `L1` — Architect mid-detail (15-30 elements, named services). **Recommended default.**
- `L2` — Technical implementation (30+ elements, all services named).
- `L3` — Deployment view (PLUGIN EXTENSION, non-standard for SAP submissions).

Multi-level requests (`L0+L1+L2`) generate one `.drawio` per level. If `L3` is selected, prefix the validator report with a note that the artefact is non-canonical for SAP Architecture Center submissions.

See [`references/levels-l0-l1-l2.md`](references/levels-l0-l1-l2.md) for budget guidelines per level.

### Step 6 — Build the JSON intermediate

For each requested level, compose a JSON object matching the schema below. Reference [`examples/L0-example.json`](examples/L0-example.json), [`examples/L1-example.json`](examples/L1-example.json), [`examples/L2-example.json`](examples/L2-example.json) for worked patterns.

```json
{
  "metadata": {
    "title": "<diagram title>",
    "level": "L0|L1|L2|L3",
    "author": "<from git config or empty>"
  },
  "groups": [
    {"id": "<id>", "type": "user|third-party|btp-layer|sap-app|non-sap", "label": "<label>", "position": "top-left|...|bottom-right"}
  ],
  "nodes": [
    {"id": "<id>", "label": "<display>", "service": "<canonical SAP name or null>", "group": "<group-id>"}
  ],
  "edges": [
    {"id": "<id>", "source": "<node-id>", "target": "<node-id>", "style": "solid|dashed|dotted|thick", "label": "<short>", "direction": "forward|bidirectional|none"}
  ]
}
```

Stable, kebab-case IDs (no spaces, no special chars). Validate the JSON has at least: 1 group, 2 nodes, 1 edge.

### Step 7 — Decide layout (group positions)

Use the 3×3 grid convention. See [`references/component-groups.md`](references/component-groups.md):

| Position | Typical content |
|---|---|
| `top-left` | Users |
| `top-center` | Third-party |
| `top-right` | Other SAP cloud apps |
| `center` | BTP Layer (core) |
| `right` | Outbound channels |
| `bottom-left` | On-premise SAP (S/4HANA) |
| `bottom-center` | Non-SAP databases |
| `bottom-right` | Cross-cutting concerns (observability, identity) |

### Step 8 — Decide line styles

Apply the convention from [`references/line-styles-spacing.md`](references/line-styles-spacing.md):

- `solid` — synchronous
- `dashed` — asynchronous
- `dotted` — optional
- `thick` — firewall only

### Step 9 — Emit the .drawio file(s)

For each level:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/generate-drawio.py" /tmp/diagram-<level>.json --out "<output_dir>/<title>-<level>.drawio"
```

Default `<output_dir>` is `./diagrams/`. Resolve from project-local settings (`.claude/sap-diagrams-pro.local.md`) if present.

### Step 10 — Self-validate and report

Always validate before announcing completion:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/validate-drawio.py" "<output>.drawio"
```

If CRITICAL issues are reported by the validator (different from CRITICAL findings of SAP-domain skills), regenerate with adjustments. WARNING / INFO are reported but do not block delivery.

Report to the user:

- One bullet per generated file with path, level, element counts, and validator summary
- If multi-level, note any consistency issues across levels (e.g. component named in L1 but not in L2)
- Suggestion: "Open with draw.io desktop, [drawio.com](https://drawio.com), or VS Code with the draw.io extension"
- Next steps for SAP Architecture Center submission (when applicable)

## Configuration

Read project-local settings from `.claude/sap-diagrams-pro.local.md` or `~/.claude/sap-diagrams-pro.local.md`:

- `btp_repo_path` — alternative path to the SAP shapes repo
- `arch_center_repo_path` — alternative path to the architecture-center repo
- `default_level` — `L0` / `L1` / `L2` (L3 not allowed as default — always opt-in)
- `output_dir` — where to write generated diagrams
- `validation_strictness` — `informational` (default) or `strict`
- `auto_consult_skills` — `true` (default) or `false` to skip Step 3

## References

- [`references/horizon-palette.md`](references/horizon-palette.md) — Horizon palette + allowed combinations.
- [`references/atomic-design.md`](references/atomic-design.md) — atoms / molecules / organisms.
- [`references/levels-l0-l1-l2.md`](references/levels-l0-l1-l2.md) — when to use each level + budgets.
- [`references/component-groups.md`](references/component-groups.md) — User, Third-party, BTP Layer conventions.
- [`references/line-styles-spacing.md`](references/line-styles-spacing.md) — solid/dashed/dotted/thick + spacing rules.
- [`references/shape-libraries-index.md`](references/shape-libraries-index.md) — the 7 SAP shape sets.
- [`references/interactive-workflow.md`](references/interactive-workflow.md) — full interactive flow detail.
- [`references/sap-skills-integration.md`](references/sap-skills-integration.md) — how to consult sap-btp-* skills + trigger heuristics.

## Templates and examples

- [`templates/L0-base.drawio`](templates/L0-base.drawio) — minimal L0 starting point.
- [`templates/L1-base.drawio`](templates/L1-base.drawio) — L1 with 5 groups pre-positioned.
- [`templates/L2-base.drawio`](templates/L2-base.drawio) — L2 with full 3×3 grid.
- [`examples/L0-example.json`](examples/L0-example.json) — worked L0 JSON.
- [`examples/L1-example.json`](examples/L1-example.json) — worked L1 JSON for "NOVA Invoice Suite".
- [`examples/L2-example.json`](examples/L2-example.json) — worked L2 JSON.

## Quality bar

A "good" generated diagram:

- Parses as valid XML and opens cleanly in draw.io / drawio.com.
- Has 0 CRITICAL validator issues.
- Has at most 2 WARNING validator issues (cosmetic palette / layout edge cases acceptable).
- Has 0 unresolved CRITICAL findings from consulted SAP skills.
- Uses the right level: L0 ≤ 10 elements, L1 = 10-30, L2 ≥ 30.
- Uses canonical SAP service names from the shape index.
- Has every edge labelled (even with one word).
- (If multi-level) is consistent across levels — same component appears with the same name in every level it belongs to.

If two of these criteria are missed, regenerate before delivering.

## L3 extension — non-standard

L3 is a plugin-specific extension for **deployment-runtime** views (Kubernetes pods, ingress, PVCs, network policies, service mesh). It is **not part of the official SAP guideline** and diagrams produced at L3 should not be submitted to SAP Architecture Center.

When generating L3, the plugin emits a leading comment in the `.drawio` metadata:

```
<!-- SAP Diagrams Pro — L3 extension (non-canonical, deployment view) -->
```

Use L3 for: internal runbooks, K8s onboarding docs, on-call references. Skip L3 for: customer proposals, blog posts, SAP submissions.
