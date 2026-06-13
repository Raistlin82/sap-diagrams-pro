<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# SAP Skills Integration — Knowledge Composition

`sap-diagrams-pro` is a **drawing** plugin. It does not pretend to know everything about SAP architecture itself — instead, it composes its knowledge by consulting the canonical SAP-domain skills published by the community. This keeps the plugin focused (visual generation) and benefits from improvements in the SAP-domain skills without redeploying.

## Preflight gate

Both knowledge layers (skills + MCP) are checked up-front by `scripts/preflight.py`. If a REQUIRED dependency is missing — the `sap-btp-best-practices` skill, or the `sap-docs` MCP — the skill surfaces the install command and stops (or proceeds in a clearly-labelled degraded mode with the user's consent). Run it first:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/preflight.py --need <concern-tags>
```

## Documentation MCP servers consulted (content grounding)

Skills give *best-practice judgement*; the MCP servers give *authoritative facts* (canonical names, categories, deprecation, capabilities). Both are needed.

| MCP server | When | What it provides |
|---|---|---|
| `sap-docs` (marianfoo/[mcp-sap-docs](https://github.com/marianfoo/mcp-sap-docs)) | **Always** | `sap_discovery_center_search` → canonical service `name`, `category` (BTP-service vs SaaS-product, icon set), `isDeprecated`; `search`/`fetch` → capability & architecture docs (ABAP/RAP/BTP/CAP); `sap_discovery_center_service` → pricing/roadmap. |
| `sap-note-search` | When a fix/known-issue is relevant | SAP Notes lookup. |
| `sap-cds-mcp` | When CAP / CDS modelling is in scope | CDS model + CAP docs. |
| `sap-fiori-mcp` | When Fiori Elements / UI generation is in scope | Fiori tooling guidance. |

**Grounding rule:** before classifying a component or assigning it an icon, look it up via `sap_discovery_center_search`. The returned `category` is the canonical signal for BTP-service-vs-SaaS classification (the single most common "wrong block type" mistake). Treat a config-only MCP presence as unconfirmed until a tool call actually returns.

## Skills consulted

The plugin references the following skills (all under [skills.sh/secondsky/sap-skills](https://skills.sh/secondsky/sap-skills) and [skills.sh/secondsky/sap-pce-expert](https://skills.sh)):

| Skill | When to consult | What it provides |
|---|---|---|
| `sap-btp-best-practices` | Always (final pass before generation) | Production-readiness checklist (logging, audit, alerting, security). Spots structural gaps. |
| `sap-btp-connectivity` | When on-prem / non-SAP systems are mentioned | Recommends Cloud Connector, Private Link, VPN, Trust patterns. |
| `sap-btp-cias` | When Cloud Integration Automation Service is detected | Validates that CIAS is the right choice vs. plain CPI / Integration Suite. |
| `sap-api-style` | When a S/4HANA OData / RFC integration is mentioned | Flags Clean Core compliance (Released C1 vs deprecated APIs). |
| `sap-btp-developer-guide` | When user requests a development view | Validates CAP / extension framework / build pattern. |
| `sap-pce-expert` | When PCE / RISE / Private Edition is mentioned | Validates PCE-specific patterns (Private Link Service, on-prem ABAP). |
| `sap-btp-master-data-integration` | When master data flows are mentioned | Recommends MDI / MDG / DRS patterns and central Business Partner mastering. |
| `sap-btp-cloud-transport-management` | When transport governance is mentioned | Suggests cTMS, Piper, BTP CI/CD or GitHub Actions + cTMS hybrid mode. |
| `sap-btp-cloud-logging` | Always (operational layer check) | Recommends Cloud Logging when missing from production diagrams. |
| `sap-btp-job-scheduling` | When background jobs / cron / batch are mentioned | Recommends BTP Job Scheduler vs. K8s CronJob vs. application-internal. |
| `sap-btp-business-application-studio` | When development tooling is mentioned | Recommends BAS for developer environment standardisation. |
| `sap-btp-build-work-zone-advanced` | When end-user portal / launchpad is mentioned | Recommends Work Zone configuration patterns. |
| `sap-btp-intelligent-situation-automation` | When event-driven automation is mentioned | Recommends ISA + signal mapping patterns. |
| `sap-btp-service-manager` | When service binding management is mentioned | Recommends Service Manager + xsuaa cross-bindings. |

## Invocation pattern

Within `sap-diagram-generate`'s procedure (Step 2 of the [interactive workflow](interactive-workflow.md)), invoke each relevant skill via the `Skill` tool **only when triggers match** the parsed description. Do not invoke all skills unconditionally — that's expensive and noisy.

### Trigger heuristics

| Trigger keyword in description | Skill(s) to consult |
|---|---|
| "production", "enterprise", "deploy" | `sap-btp-best-practices`, `sap-btp-cloud-logging` |
| "S/4HANA", "ECC", "on-prem", "PostgreSQL", "VPN" | `sap-btp-connectivity` |
| "PCE", "RISE", "Private Edition" | `sap-pce-expert`, `sap-btp-connectivity` |
| "OData", "API_*", "RFC", "BAPI" | `sap-api-style` |
| "MDI", "Master Data Integration", "central BP" | `sap-btp-master-data-integration` |
| "iflow", "Cloud Integration", "CPI", "Integration Suite" | `sap-btp-cias` (if CIAS-specific) |
| "transport", "cTMS", "deploy", "CI/CD" | `sap-btp-cloud-transport-management` |
| "job", "cron", "batch", "scheduler" | `sap-btp-job-scheduling` |
| "Fiori launchpad", "portal", "Work Zone" | `sap-btp-build-work-zone-advanced` |
| "BAS", "Business Application Studio", "developer environment" | `sap-btp-business-application-studio` |

### Aggregation

After invoking the relevant skills, aggregate their findings into a **single best-practice report** with these severity levels:

- **CRITICAL** — the architecture has a structural flaw (e.g. on-prem access without Cloud Connector). Block generation until resolved.
- **WARNING** — production-readiness gap (e.g. no logging). Generate, but flag prominently in the report.
- **INFO** — recommendation that improves alignment with SAP standards but is not strictly required.

Apply the user's `validation_strictness` setting (`informational` vs `strict`) to decide whether CRITICAL is fail-fast.

## When a SAP skill is unavailable

If a referenced skill is not installed in the user's environment, **do not fail**. Skip it gracefully and emit an INFO note to the user:

```
ℹ️  Skill `sap-btp-best-practices` not installed — best-practice
    validation skipped for this run. Install via:
    npx skills add secondsky/sap-skills@sap-btp-best-practices
```

This keeps the plugin functional even in minimal environments while educating users about the available knowledge layers.

## Output format from consulted skills

When invoking a SAP-domain skill, ask for output in a structured form so the plugin can aggregate cleanly. Recommended prompt template:

```
Validate the following architecture against your knowledge.

Components:
  <list from interactive-workflow Step 1>

Description:
  <user's natural-language input>

Return your findings as a JSON array:
[
  {"severity": "CRITICAL|WARNING|INFO", "topic": "<short>", "message": "<detail>"}
]

Return only the JSON array, no commentary.
```

Skills that don't natively return JSON: ask for a markdown table and parse with regex (acceptable fallback).

## Future: caching

Repeated invocations of `sap-diagrams-pro` against the same description should not re-consult the SAP skills (each call is expensive). Plan for v0.2: cache the per-skill findings keyed by `(skill_name, hash(description))` in `~/.cache/sap-diagrams-pro/skill-findings/`.

## Common mistakes

- **Invoking all 14 skills on every diagram**: triggers slow generation and noisy reports. Use the trigger heuristics above.
- **Hardcoding skill behaviour into the plugin**: the plugin's job is to compose knowledge, not duplicate it. If you find yourself adding "best-practice rules" directly in `validate-drawio.py`, stop — that knowledge belongs in `sap-btp-best-practices`.
- **Ignoring CRITICAL findings**: never silently override a CRITICAL from a SAP-domain skill. Surface to the user and require explicit override.
- **Asking the user to fix issues during diagram generation**: this is a drawing tool. If the architecture has problems, point to the right SAP skill for guidance — don't try to redesign the architecture inside this plugin.
