<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Component Groups (Organisms)

Source: [SAP BTP Solution Diagram Guideline — Component Groups](https://sap.github.io/btp-solution-diagrams/docs/btp_guideline/comp_groups/comp_groups/).

A "component group" (organism, in atomic design vocabulary) is a visual cluster that has a clear semantic role in the diagram. Every SAP solution diagram is built from a small, fixed set of organisms — using the same vocabulary across diagrams keeps the SAP catalogue visually coherent.

## The standard organisms

### 1. User layer

**Purpose**: represent the people / roles / personas that interact with the solution.

**Style**: rounded rectangle, non-SAP border `#475E75`, white fill `#FFFFFF`.
**Position by convention**: top of the diagram (top-left or top-center).
**Typical contents**: end users with role names ("AP Clerk", "Finance Approver"), customer-facing personas, partner contacts.

**Example**:

```
┌─────────────────────────────┐
│  AP Team                    │
│  ┌──────┐ ┌──────┐ ┌──────┐ │
│  │ Clerk│ │Approv│ │Audit │ │
│  └──────┘ └──────┘ └──────┘ │
└─────────────────────────────┘
```

### 2. Third-party / partner systems

**Purpose**: external systems not built or sold by SAP that the solution integrates with.

**Style**: rounded rectangle, non-SAP border `#475E75`, light grey fill `#F5F6F7`.
**Position by convention**: top-center or top-right.
**Typical contents**: partner SaaS, banks, government services (SDI, AdE, IRS), CRM integrations.

### 3. BTP Layer

**Purpose**: the core of any SAP solution diagram. Contains all BTP services that compose the solution.

**Style**: rounded rectangle, BTP border `#0070F2`, BTP fill `#EBF8FF`.
**Position by convention**: center of the diagram.
**Typical contents**: CAP, DOX, Build Apps, Build Process Automation, Build Work Zone, Integration Suite, Event Mesh, AI Core, Cloud Logging, Audit Log, Alert Notification, Job Scheduler.

For L2 diagrams, decompose the BTP Layer into sub-groups by capability:

- **Inbound** — connectivity, integration, API management.
- **Processing** — runtime, AI, orchestration.
- **Outbound** — notification, archiving.
- **Operations** — observability, audit, jobs.
- **Identity** — XSUAA, IAS, Authorization Mgmt.

### 4. SAP application layer

**Purpose**: SAP cloud or on-premise applications consumed by the solution.

**Style**: rounded rectangle, BTP border `#0070F2`, white fill `#FFFFFF` (BTP-affiliated but distinguishable from BTP services).
**Position by convention**: bottom-left or bottom-center for on-premise apps; top-right for SAP cloud apps.
**Typical contents**: S/4HANA Cloud, S/4HANA on-premise, ECC, SuccessFactors, Ariba, Fieldglass, Concur, C4C.

### 5. Non-SAP system layer

**Purpose**: legacy systems, on-premise databases, custom applications that aren't SAP.

**Style**: rounded rectangle, non-SAP border `#475E75`, light grey fill `#F5F6F7`.
**Position by convention**: bottom of the diagram (bottom-left or bottom-center).
**Typical contents**: on-prem PostgreSQL, custom Java apps, mainframes, legacy IBM systems.

### 6. Cross-cutting concerns

**Purpose**: capabilities used across the solution but not core to any specific flow.

**Style**: vertical strip on the right side of the diagram. Single rounded rectangle, non-SAP border, white fill.
**Position by convention**: right side (bottom-right or full right column for L2).
**Typical contents**: observability (Cloud Logging, Cloud ALM), security (XSUAA, IAS), networking (Cloud Connector, VPN).

## Composition rules

### Containment

A node can be a member of **exactly one** group. Nested groups (organism inside organism) are discouraged at L0 and L1 — they create visual ambiguity. At L2, the BTP Layer may have sub-groups; in that case the parent BTP Layer is rendered as a thin outer frame and the sub-groups carry the fill.

### Spacing

The "rule of thumb" from the guideline: spacing between organisms must be **at least the height of the SAP logo** (≈ 32px at the 1600×1000 canvas). The plugin's auto-layout enforces 24-32px padding by default.

### Group-internal arrangement

Inside a group, nodes flow in rows (left-to-right, then top-to-bottom). Pack them tightly — the `NODE_GAP_X = 24` and `NODE_GAP_Y = 24` constants in `generate-drawio.py` define the gap.

### Overlap

Groups must not overlap (the validator emits a `BOX_OVERLAP` info issue if they do). The plugin's 3×3 layout prevents this by design; orphan nodes (no group) are placed in the center cell.

## When in doubt

Default to: User (top-left) + BTP Layer (center) + SAP App (bottom-center). This is enough to communicate any L0 or L1 solution.
