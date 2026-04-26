<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Diagram Levels — L0, L1, L2

The SAP convention defines three levels of detail for a solution diagram. Picking the right level is the single most important decision when producing a diagram — too detailed, and the message is lost; too abstract, and the audience can't act on it.

## L0 — Executive overview

**Audience**: C-level, sponsors, decision-makers without SAP product knowledge.
**Use case**: pitch decks, executive summaries, "what does this solution do" slides.
**Element budget**: **5-10 boxes total**.

**Characteristics:**

- One or two organisms at most: a "User" and a "Solution" box, possibly a "External Systems" box.
- No named SAP services. Reference the capability ("AI document extraction") rather than the product ("DOX").
- Edges labelled with business outcomes ("invoice in", "payment out") rather than technical flows.
- White space dominates — the message is the simplicity itself.
- Single page, landscape, fits a slide.

**Example output (textual):**

```
┌───────────┐    invoice scan     ┌────────────────────┐    posting    ┌────────────┐
│   User    │────────────────────▶│  NOVA Solution     │──────────────▶│  S/4HANA   │
│ (AP team) │                     │  (BTP-based)       │               │            │
└───────────┘                     └────────────────────┘               └────────────┘
```

## L1 — Architect mid-detail

**Audience**: solution architects, technical sales, RFP responders.
**Use case**: technical proposals, blog posts, architecture review boards.
**Element budget**: **15-30 elements**.

**Characteristics:**

- 3-6 organisms: User, Third-party, BTP Layer, SAP application(s), non-SAP system(s).
- Named SAP services in the BTP Layer (CAP, DOX, Build Apps, Integration Suite, Event Mesh).
- Named SAP applications (S/4HANA Cloud, SuccessFactors).
- Edges labelled with protocol or pattern (`OData v4`, `async event`, `webhook`).
- Some sub-grouping inside the BTP Layer is allowed (e.g. "Inbound", "Processing", "Outbound" lanes).
- May span two slides if needed; landscape preferred.

This is the **default level** the plugin uses when the user does not specify. It is also the most common level in [SAP Architecture Center](https://architecture.learning.sap.com).

## L2 — Technical implementation

**Audience**: implementation team, integration developers, security architects.
**Use case**: solution design documents, runbook references, internal architecture documentation.
**Element budget**: **30+ elements** (typical 30-60).

**Characteristics:**

- 6+ organisms covering all areas: Users (multi-persona), Third-party, BTP Layer (decomposed by capability domain), SAP applications, non-SAP systems, cross-cutting concerns (observability, identity, networking).
- Every BTP service named with its specific component (e.g. "Cloud Connector" instead of "BTP Connectivity").
- Edges labelled with concrete API names (`API_BUSINESS_PARTNER_0001`, `CloudEvents v1.0`).
- Deployment-runtime split: Kyma cluster vs Cloud Foundry vs on-prem indicated visually.
- Multi-page diagrams are common — split by domain, not by complexity.
- Must include legend (line styles, color semantics).

## How to choose

Use this decision flow:

1. **Who is the primary reader?** Executive → L0. Architect → L1. Implementer → L2.
2. **What's the deliverable?** Slide → L0/L1. Document → L1/L2. Runbook → L2.
3. **How much do they care about specific products?** Not at all → L0. By name → L1. By API → L2.
4. **What's the budget for production?** 5 minutes → L0. 30 minutes → L1. 2+ hours → L2.

When the user does not specify a level, default to **L1**.

## Element budget guardrails

The validator will warn if your diagram exceeds these budgets:

| Level | Min nodes | Max nodes | Min groups | Max groups |
|---|---|---|---|---|
| L0 | 2 | 10 | 1 | 3 |
| L1 | 6 | 30 | 3 | 7 |
| L2 | 15 | 80 | 5 | 12 |

If you find yourself wanting more than the max, split into multiple diagrams of the same level rather than upgrading.

## Naming convention for output files

Recommended filename pattern:

```
<short-title>-<level>.drawio
```

Examples:

- `nova-invoice-suite-L1.drawio`
- `e2e-procurement-overview-L0.drawio`
- `sf-payroll-integration-L2.drawio`

This matches the SAP convention used in `architecture-center/docs/ref-arch/RA00XX/drawio/` files.
