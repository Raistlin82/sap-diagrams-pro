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

**Purpose**: SAP **SaaS applications** (full products, not BTP services) consumed by the solution.

**⚠️ Critical distinction — what is and isn't an SAP application**:

✅ **Goes in `sap-app` group**:
- S/4HANA Cloud (the product, not the runtime)
- S/4HANA on-premise / Private Cloud Edition (PCE)
- SAP ECC
- SAP SuccessFactors
- SAP Ariba
- SAP Fieldglass
- SAP Concur
- SAP Customer Experience (C4C)
- SAP Commerce Cloud
- SAP Signavio
- SAP MDG (Master Data Governance)

❌ **Does NOT go in `sap-app`** — these are BTP services and belong in the `btp-layer` group:
- SAP Build Work Zone (BTP service: portal/launchpad)
- SAP Task Center (BTP service)
- SAP Build Apps / Build Code / Build Process Automation (BTP services)
- SAP HANA Cloud (BTP service: database)
- SAP Cloud ALM (BTP service: monitoring)
- SAP Cloud Logging / Audit Log Service (BTP services)
- SAP Identity Authentication (IAS) (BTP service)
- SAP Authorization and Trust Management Service (XSUAA) (BTP service)
- SAP Cloud Connector (BTP service)
- SAP Integration Suite / Cloud Integration / Event Mesh (BTP services)
- SAP AI Core / Joule / Generative AI Hub (BTP services)
- SAP Datasphere / Analytics Cloud / Data Intelligence (BTP services — though Analytics Cloud is borderline)

**Rule of thumb**: if it's listed at <https://discovery-center.cloud.sap/serviceCatalog> as a **service** (subscription/instance under a BTP subaccount), it goes in `btp-layer`. If it's a standalone **product** (separate subscription, separate URL, separate UI), it goes in `sap-app`.

**Style**: rounded rectangle, BTP border `#0070F2`, white fill `#FFFFFF` (BTP-affiliated but distinguishable from BTP services).
**Position by convention**: bottom-left or bottom-center for on-premise apps; top-right for SAP cloud apps. PCE in particular often goes bottom-left because of its on-prem character despite being managed.

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

### 7. Subaccount (IR v2, nestable)

**Purpose**: model a real BTP account hierarchy inside the BTP Layer instead of a flat pile of service boxes.

**Style**: white fill, BTP border `#0070F2`, tight rounded frame labelled "Subaccount: …".
**Position by convention**: nested inside the `btp-layer` group (`"parent": "<btp-group-id>"`).
**Nesting**: a `subaccount` group's `parent` may be another `subaccount` id — e.g. `Extension Test` ⊃ `Extension Production` renders frame-inside-frame. Use this whenever the solution spans more than one subaccount/stage rather than flattening everything into one BTP box.

### 8. Governance (IR v2)

**Purpose**: cross-cutting governance/monitoring products (e.g. Cloud ALM) that sit above — not inside — the BTP Layer.

**Style**: BTP fill/border, wide strip spanning the canvas width.
**Position by convention**: its own top band, above the BTP frame's top edge. Never beside or below it — that misplacement has no `set_zone` fix (see `comp-governance-top` in [`visual-rubric.md`](visual-rubric.md)); the IR `type`/`position` must be correct up front.

### 9. Cloud tiers (IR v2)

**Purpose**: represent the public cloud / private cloud / any-premise deployment tiers a backend runs in, as distinct labelled boxes rather than one undifferentiated "Backends" group.

**Style**: tier box; `kind: "public"` and `kind: "private"` render with the SAP-blue border (SAP-managed), `kind: "any-premise"` renders non-SAP grey `#475E75` unless the tier itself is SAP-affiliated.
**Position by convention**: RIGHT zone, right of the NETWORK separator.
**Typical contents**: a `chip`-typed node naming the concrete offering (e.g. "Private Cloud Edition (PCE)").

### 10. Custom app (IR v2)

**Purpose**: a bespoke application built *on* BTP — distinct from `sap-app` (a SAP-shipped standalone product).

**Style**: BTP fill `#EBF8FF`, BTP border — the same product-card treatment as a `product`-typed node, but at group scope for a whole custom application.
**Position by convention**: wherever the architecture places it (commonly RIGHT, alongside the tiers it's deployed into).

## Composition rules

### Containment

A node can be a member of **exactly one** group. Freeform "organism inside organism" nesting is still discouraged — but IR v2's `subaccount` group type is the one sanctioned exception: it is *designed* to nest (`parent` → another `subaccount` id), and using it at L1 to model a real Test ⊃ Production hierarchy is preferred over flattening everything into one BTP box. At L2, the BTP Layer may also have capability sub-groups; the parent BTP Layer then renders as a thin outer frame and the sub-groups carry the fill.

### Spacing

The "rule of thumb" from the guideline: spacing between organisms must be **at least the height of the SAP logo** (≈ 32px). The plugin's deterministic skeleton slot engine (`scripts/_skeleton_layout.py`) lays organisms out along the horizontal axis — consumers LEFT → BTP CENTER → systems RIGHT — and auto-sizes each container to its content, enforcing even inter-zone padding by default.

### Group-internal arrangement

Inside a group, nodes flow in rows (left-to-right, then top-to-bottom). The skeleton layout engine (`scripts/_skeleton_layout.py`) packs them with even gaps and grows the container to fit. An `order_override` layout hint (see [`visual-rubric.md`](visual-rubric.md)) can force a specific sibling order when the default rank sort crowds a corner.

### Overlap

Groups must not overlap (the validator emits a `BOX_OVERLAP` info issue if they do). The plugin's skeleton layout engine prevents this by design: each organism is placed in its own auto-sized zone along the horizontal axis (consumers LEFT → BTP CENTER → systems RIGHT), and nodes are attached to their group via real draw.io parenting. Orphan nodes (no group) fall back to the center zone.

## When in doubt

Default to: User (top-left) + BTP Layer (center) + SAP App (bottom-center). This is enough to communicate any L0 or L1 solution.
