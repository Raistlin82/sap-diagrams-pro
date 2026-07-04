<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Step 0 — SAP docs consultation (mandatory)

Before building any inventory or generating any diagram, you **must** validate the user's named components and their relationships against SAP's own documentation. Skipping this step has produced wrong architectures in the past — including placing SAP Convergent Charging *inside* S/4HANA, when help.sap.com explicitly states it is "deployed and connected to" S/4HANA as a separate system.

This reference catalogues *which* tools to call, *when*, *with which queries*, and *what to extract* from the results.

## Why this exists

The plugin's training data is stale and the model's general knowledge of SAP product topology is often wrong about:

- Whether a named module is **embedded** in S/4HANA or runs as a **standalone application** (CC, Convergent Mediation, Master Data Governance, …).
- Whether two products are **predecessor/successor** (e.g. SOM in CRM vs. SOM in S/4HANA, SAP CPI vs. SAP Integration Suite).
- Which **business functions** must be activated to enable a process flow (e.g. `FICAX` vs. `FICAC_CORE` + `FICAC_CI` for BRIM).
- The **canonical name** vs. marketing name (e.g. *Customer Experience* is a brand umbrella; the actual product is *SAP Sales Cloud V2* + *Service Cloud V2*).
- Whether an integration is **synchronous (RFC/OData)** or **asynchronous (replication, IDoc, event mesh)**. The arrowhead style depends on this.

One Step 0 search per uncertain component avoids re-generation cycles later.

## Available MCP tools

These tools are available in the Claude Code environment hosting this plugin. They are *not* bundled with the plugin itself — they come from the `sap-docs` and `sap-notes` MCP servers (project `marianfoo/sap-docs`).

| Tool                                   | When to call                                                                                     |
|----------------------------------------|--------------------------------------------------------------------------------------------------|
| `mcp__sap-docs__search`                | **First call always**. Full-text across SAP Help portal, BTP docs, ABAP keyword docs, Architecture Center, Clean ABAP styleguides. Returns ranked document ids + URLs. |
| `mcp__sap-docs__fetch`                 | Retrieve the full markdown of a result. Always fetch the top 1–3 results when the question is architectural (deployment, integration topology, scope of a product). |
| `mcp__sap-docs__sap_search_objects`    | Released ABAP objects (CDS views, classes, RAP behaviors, BAdIs). Use to confirm an API exists and is Clean Core compliant for the target system_type (`private_cloud` / `public_cloud` / `btp` / `on_premise`). |
| `mcp__sap-docs__sap_get_object_details`| Full release state of one specific object (e.g. "is `I_BUSINESSPARTNER` released in BTP?"). Returns successor recommendations when deprecated. |
| `mcp__sap-docs__sap_community_search`  | Fallback when `search` returns nothing useful, or for obscure error messages and real-world integration patterns. Filters by minimum kudos. |
| `mcp__sap-docs__sap_discovery_center_search` | SAP Discovery Center service catalogue (BTP services, what they do, pricing tiers). |
| `mcp__sap-docs__sap_discovery_center_service`| Full service details for one entry. |
| `mcp__sap-docs__abap_feature_matrix`   | Feature support matrix per ABAP release. Use when the user mentions a specific S/4HANA release. |
| `mcp__sap-notes__search`               | SAP Notes (KBAs, bug fixes, security patches). Use when the user mentions an **error**, **dump**, **patch**, or a specific note number. |
| `mcp__sap-notes__fetch`                | Retrieve a specific SAP Note by id. |

## Workflow

### Pattern A — User describes a known SAP solution by name

> e.g. "Generate a diagram for SAP BRIM on S/4HANA private cloud edition"

1. `search(query="<solution canonical name> architecture <target environment>")` — example: `"SAP BRIM Convergent Charging architecture S/4HANA private cloud"`.
2. `fetch` the top result from `help.sap.com` (it will be the master integration page). Look for the section titled *Integration Guides* or *Solution Overview*.
3. `fetch` the architecture-center RA if one appears in the top 10 results (e.g. RA0010 for Build Work Zone).
4. Extract:
   - List of **sub-components** with their official acronyms.
   - For each: *embedded in S/4HANA* vs. *standalone application* vs. *BTP cloud service*.
   - Integration points (sync RFC, IDoc, REST/OData, replication, event).
5. Build the inventory table with one row per component, with a final column `SAP doc URL` citing the page that justifies the placement.

### Pattern B — User names a component you're not sure how to classify

> e.g. "I want SAP Master Data Governance on the diagram"

1. `search(query="SAP Master Data Governance deployment options")`.
2. Read the result snippet. If it mentions multiple deployment options (on-premise / cloud edition / hybrid), surface the choice to the user before generating — do not assume.
3. If unclear, call `sap_discovery_center_search(query="SAP Master Data Governance")` to see if there is a BTP service for it.

### Pattern C — User mentions an ABAP API or CDS view

> e.g. "Show me where I_BUSINESSPARTNER is consumed by the BTP extension"

1. `sap_search_objects(query="I_BUSINESSPARTNER", system_type="private_cloud", clean_core_level="A")`.
2. If found, the object is released. Place it on the diagram with confidence.
3. If not found, call `sap_get_object_details(object_type="DDLS", object_name="I_BUSINESSPARTNER", system_type="private_cloud")` — the response will indicate state (`released | deprecated | classicAPI | …`) and successor objects when deprecated.

### Pattern D — User describes a flow that involves error handling or known issues

> e.g. "S/4HANA → BTP via Cloud Connector, but we keep hitting 503"

1. `mcp__sap-notes__search(q="Cloud Connector 503 BTP destination")`.
2. Read the top 3 notes. They will name the specific tuning parameter or fix to apply.
3. Reflect the finding as an **annotation pill** next to the affected edge on the diagram (e.g. "raise *MaxConnections*"), not as a free-form comment.

## What to do with the findings

For each consulted document, capture the following in the inventory table that you present to the user before generating:

| Component               | Group              | SAP doc URL                                                | Decision rationale                          |
|-------------------------|--------------------|------------------------------------------------------------|---------------------------------------------|
| SAP Convergent Charging | sap-app (standalone) | https://help.sap.com/docs/Convergent_Charging/…/51f242b29…  | "deployed and connected" to S/4HANA per CC integration guide 2025 |
| Convergent Invoicing (CI) | sap-app (in S/4HANA PCE) | https://help.sap.com/docs/SAP_S4HANA_ON-PREMISE/…/c918bf4f… | FICAC_CI business function inside S/4HANA   |
| FI-CA (Contract Accounts) | sap-app (in S/4HANA PCE) | https://help.sap.com/docs/SAP_S4HANA_ON-PREMISE/…/c918bf4f… | Base business function (FICAX or FICAC_CORE) |
| …                       | …                  | …                                                          | …                                           |

This makes the architecture **auditable**. If a reviewer (or a future you) questions a placement, the doc URL is one click away.

## Anti-patterns

- **Inferring topology from product brand pages**: marketing pages on sap.com often omit deployment details. Always pivot to help.sap.com.
- **Trusting one search result**: when a placement is non-obvious (embedded vs. standalone), `fetch` the doc and read the surrounding paragraphs. Snippets can be misleading.
- **Skipping Step 0 for "well-known" topologies**: even well-known architectures shift over time. SOM moved from CRM to S/4HANA. CC went from on-premise-only to deployable on hyperscaler. Old mental models become wrong silently.
- **Silently "fixing" the user's intent**: if the user describes a topology that disagrees with SAP docs, surface the disagreement before generating. They may know something the docs don't (PoC, future state, intentional deviation).

## Caching

The `sap-docs` tools have their own backend cache. You do not need to re-fetch the same document twice within a session. Within the plugin, no persistent cache of SAP doc content is kept (would drift fast). The `~/.cache/sap-diagrams-pro/architecture-center/` repo clone is the only persistent SAP knowledge cache — refresh weekly via `bootstrap-cache.sh --refresh`.

## Cost / latency

A typical Step 0 for a single-solution diagram costs 2–4 MCP tool calls (one `search`, one or two `fetch`). For multi-solution diagrams (e.g. BRIM + Datasphere + IAS), expect 5–10 calls. Run independent searches in parallel by emitting multiple tool calls in the same assistant message.
