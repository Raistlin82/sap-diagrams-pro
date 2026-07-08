<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Interactive Workflow — Confirm Before You Generate

The plugin **does not** silently generate a diagram from a one-line description. Before producing any `.drawio` output, it walks the user through a confirmation flow to ensure the diagram captures their actual architecture, follows SAP best practices, and uses the right level(s) of detail.

This is the single most important quality gate. Skipping it produces beautiful but incorrect diagrams.

## Step 0 — Preflight & content grounding (v0.2 — runs before everything)

The interactive flow is only as good as the facts behind it. Before parsing, **gate on dependencies and ground the content**:

1. **Preflight** — `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/preflight.py --need <concern-tags>`. If a REQUIRED item is missing (the `sap-btp-best-practices` skill, or the `sap-docs` MCP) print the install command (`npx skills add secondsky/sap-skills`; `mcp-sap-docs` → `claude mcp add`, https://github.com/marianfoo/mcp-sap-docs) and stop, unless the user opts into a degraded mode.
2. **Ground in the SAP Discovery Center** — for each component, `mcp__sap-docs__sap_discovery_center_search` gives the canonical `name`, `category` (→ BTP-service vs SaaS-product), and `isDeprecated`. `mcp__sap-docs__search`/`fetch` give capability/architecture facts. This is what lets the skill understand *all* the components a solution actually needs — it is not optional.

Only once the dependencies are present and the inventory is grounded do you proceed to the steps below.

## Six-step interactive flow

### Step 1 — Parse and propose

After parsing the user's natural-language description, present a **proposed component inventory** as a structured table (do not generate the diagram yet):

```
I detected the following components:

USERS / ACTORS
  • AP Clerk (3rd-party-actor)
  • Finance Approver

SAP BTP SERVICES
  • CAP Backend (Cloud Foundry runtime)
  • Document Information Extraction (DOX, AI set)
  • Event Mesh (integration-suite set)
  • Build Process Automation (app-dev-automation set)

SAP APPLICATIONS
  • S/4HANA Cloud (named explicitly)

THIRD-PARTY / NON-SAP
  • Italian SDI (Sistema di Interscambio)
  • PostgreSQL (on-prem)

DATA FLOWS
  • SDI → CAP (async, FatturaPA XML in)
  • CAP → DOX (sync, OCR)
  • CAP ↔ Event Mesh (async, internal)
  • CAP → S/4HANA (sync, Supplier Invoice OData)
  • CAP → BPA (async, approval kickoff)

LEVEL DETECTED: L1 (15 elements, named services, mid-detail)
```

### Step 2 — Consult SAP skills for best-practice validation

Before asking the user to confirm, invoke the relevant SAP skills (see [`sap-skills-integration.md`](sap-skills-integration.md)) to surface gaps. Examples:

- `sap-btp-best-practices` may flag: "Cloud Logging service missing — required for any production BTP solution"
- `sap-btp-connectivity` may flag: "Cloud Connector missing — needed for on-prem PostgreSQL access"
- `sap-api-style` may flag: "S/4HANA OData call should be tagged as Released C1 for Clean Core compliance"
- `sap-pce-expert` may flag (when PCE is detected): "Private Link Service should be mentioned for PCE integration"

Aggregate the suggestions into a **best-practice gap report**:

```
SAP best-practice findings (before you confirm):

⚠️  WARNING — sap-btp-best-practices
   The diagram is missing operational components recommended for production:
     • Cloud Logging
     • Audit Log Service
     • Alert Notification

ℹ️  INFO — sap-btp-connectivity
   On-prem PostgreSQL detected. Add Cloud Connector to the BTP Layer
   to make the connectivity path explicit.

ℹ️  INFO — sap-api-style
   S/4HANA Supplier Invoice integration: prefer the Released-C1
   API_SUPPLIERINVOICE_PROCESS_SRV (annotate the edge).
```

### Step 3 — Ask the user to confirm or amend

Present three explicit choices:

1. **Accept as detected** — proceed with the inventory as-is (L1 default level).
2. **Apply best-practice suggestions** — extend the inventory with the warnings/info above before generating.
3. **Amend manually** — let the user remove / add / rename components, change the level.

Wait for the answer. If the user answers "manually", iterate — accept their edits, re-run the SAP-skill validation pass, present again.

### Step 4 — Choose level(s)

If the user did not specify a level, ask:

```
Which detail level(s) would you like? You can choose multiple.

  • L0 — Executive overview (5-10 boxes, no technical detail)
  • L1 — Architect mid-detail (15-30 elements, named services)  [RECOMMENDED]
  • L2 — Technical implementation (30+ elements, all services named)
  • L3 — Deployment view (PLUGIN EXTENSION, non-standard)

Reply with one or more (e.g. "L0 + L1" or "L1 only").
```

Rules:

- L0/L1/L2 are **SAP-standard** levels. Always available.
- **L3 is a plugin extension** for deployment-runtime visualisation (Kubernetes pods, network policies, ingress, persistent volumes). Clearly mark it as non-standard if chosen — the resulting diagram will not be acceptable for SAP Architecture Center submissions but is useful for internal runbooks.
- If the user picks multiple, generate one `.drawio` per level, named `<title>-L<N>.drawio`.

### Step 5 — Generate with full context

Only when steps 1-4 are complete, hand off to `SKILL.md`'s Steps 6-8 for each requested level:

1. Build the IR v2 per the agreed inventory and level(s) — the full authoring grammar (group `subaccount`/`governance`/`cloud-tier`/`custom-app` types, `product`/`chip`/`db` nodes, `flowFamily`/`pill` edges, `metadata.branding`/`badges`) is in `SKILL.md` Step 6.
2. Resolve every named SAP service via `sap-icons-resolve` (do not skip — fall back to plain box only when truly missing from the index).
3. Validate the IR (`scripts/validate-ir.py`) — must exit 0 before generating (`SKILL.md` Step 7).
4. Generate, gate (`validate-drawio.py --strict` + `check-composition.py`), render, and run the visual-rubric look → patch → regenerate loop, max 3 vision iterations (`SKILL.md` Step 8). This is where the diagram actually gets built and verified — this document only covers getting to a confirmed inventory.

### Step 6 — Report and offer next steps

Present, per level, the scorecard `SKILL.md` Step 8.6 produces (gate FAIL/WARN counts, rubric pass count, crossings, piercings, vision iterations used) plus any degrade-path WARNING:

```
✅ Generated 2 diagrams:
   • diagrams/nova-invoice-suite-L0.drawio  (8 elements — gate: 0 fail/0 warn; rubric: 26/26 pass)
   • diagrams/nova-invoice-suite-L1.drawio  (24 elements — gate: 0 fail/0 warn; rubric: 24/26 pass,
     2 manual findings: sem-icons-match, comp-legend-present; 2/3 vision iterations used)

Next steps:
  • Open in draw.io desktop or [drawio.com](https://drawio.com)
  • Export PNG: File → Export As → PNG
  • Submit to SAP Architecture Center: see /docs/golden-path/

Want to refine? Re-run with adjustments, or use /sap-diagrams-pro:sap-diagram-validate
to inspect issues in detail.
```

## When to skip the interactive flow

Three cases where it's acceptable to skip the confirmation:

1. **The user explicitly says "just generate it"** (e.g. `/sap-diagram-generate L1 NOVA Invoice Suite — auto`).
2. **The CI pipeline calls the skill** (no human in the loop). In this case, the SAP-skill validation must still run — fail the build if CRITICAL findings appear.
3. **The user is iterating on a previously confirmed inventory** (the second invocation in the same session). In this case, re-confirm only if the description changed.

In all other cases, run the full 6-step flow. The cost is one extra round-trip; the benefit is correct diagrams.

## Common mistakes

- **Skipping step 2 (best-practice consultation)**: leads to diagrams missing observability / connectivity / identity layers.
- **Asking "are you sure?" without showing the inventory**: the user has nothing to evaluate. Show the structured table.
- **Generating multiple levels without asking**: respect the user's time. If they wanted only L1, don't auto-produce L0+L1+L2.
- **Mixing standard L0/L1/L2 with the L3 extension silently**: always flag L3 as non-standard so the user knows the artefact won't be SAP-canonical.
- **Re-confirming on every invocation in a session**: when the user has already approved an inventory, don't re-ask if only the level changes.
