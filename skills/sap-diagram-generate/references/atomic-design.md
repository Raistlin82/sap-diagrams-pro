<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Atomic Design System — SAP Solution Diagrams

Source: [SAP BTP Solution Diagram Guideline — Atomic Design System](https://sap.github.io/btp-solution-diagrams/docs/btp_guideline/atomic/).

The SAP Solution Diagram Guideline is built on Brad Frost's Atomic Design methodology. Understanding the three levels (atoms / molecules / organisms) is critical for producing diagrams that scale from simple to complex while staying visually consistent.

## Atoms

The smallest building blocks. Atoms have no semantic meaning on their own — they're the raw materials.

**The atoms in a SAP solution diagram are:**

- **Colors** (Horizon palette — see [`horizon-palette.md`](horizon-palette.md))
- **Line styles** (solid, dashed, dotted, thick — see [`line-styles-spacing.md`](line-styles-spacing.md))
- **Icons** (from the 7 SAP shape libraries + the generic-icons set)
- **Text** (font: Helvetica/Arial; sizes: 16 for title, 14 for group labels, 12 for node labels, 10 for edge labels)

**Rule**: never invent a new atom. Use only colors / lines / icons / fonts from the catalog.

## Molecules

Combinations of atoms that have a small but recognisable meaning.

**Common molecules:**

- A **box** with a Horizon border + fill + a label (e.g. a rounded rectangle with `strokeColor=#0070F2; fillColor=#EBF8FF`).
- An **arrow** with a line style + label (e.g. dashed arrow with "async" text).
- An **icon + label** combo (e.g. the BPA icon with "SAP Build Process Automation" caption).
- A **swim-lane header** (a rectangle with no fill and a Horizon-colored bottom border).

**IR v2 molecules** (`assets/style-contract.json`; emitted from the `type`/`kind` fields documented in `SKILL.md` Step 6):

- **`product-box`** — a BTP-blue container (fill `#EBF8FF`→`#ECF8FF`) with a title row and an inner white **`capability-chip`** panel: an icon+label grid (icon optional per capability) rather than separate boxes per capability. Authored via a node with `"type": "product"` + `capabilities: [{label, icon?}, ...]`.
- **`subaccount-frame`** — a tight white rounded frame, BTP-blue border, labelled "Subaccount: …". Authored via a group with `"type": "subaccount"`; **nestable** — a `subaccount` group's `parent` may be another `subaccount` id (e.g. Extension Test ⊃ Extension Production), rendering frame-inside-frame.
- **`governance-strip`** — a wide BTP-blue band spanning the canvas, sitting above the BTP frame's top edge. Authored via a group with `"type": "governance"`.
- **`tier-box-sap`** / **`tier-box-nonsap`** — a labelled RIGHT-zone tier box; SAP-blue border for `kind: "public"` / `"private"`, non-SAP grey `#475E75` for `kind: "any-premise"`. Authored via a group with `"type": "cloud-tier"` + `kind`.
- **`custom-app-box`** — a BTP-blue product-style card for a bespoke application built on BTP (distinct from `sap-app`, a SAP-shipped product). Authored via a group with `"type": "custom-app"`.
- **`network-separator`** / **`network-separator-label`** — the vertical grey `#5B738B` (`strokeWidth=3`) bar + "NETWORK" caption between the BTP center and a RIGHT-zone tier. Driven by `metadata.networkSeparator` (default `true` whenever a RIGHT-zone tier group exists).
- **`chip`** / **`db`** — a small white BTP-bordered label chip, and the cylinder datastore shape. Authored via a node with `"type": "chip"` or `"type": "db"`.
- **`badge-hyperscaler`** / **`badge-runtime`** — small logo badges (Azure/AWS, Cloud Foundry/Kyma) on a group's or the diagram's `badges.hyperscalers`/`badges.runtimes`.
- **`watermark`** — a large, semi-transparent (`opacity=15`) background image behind the diagram content. Authored via `metadata.branding.partnerWatermark`; a customer logo (`metadata.branding.customerLogo`) renders top-left instead, next to the title, at full contrast. Only use a customer's own logo/watermark asset with their explicit consent — never a competitor's or an unrelated customer's.

**Rule**: each molecule should be replaceable in isolation. If you change the BPA icon-label molecule, it should not affect the surrounding flow.

## Organisms

Groups of molecules that form a meaningful section of the diagram.

**Standard organisms in a SAP solution diagram:**

- **User layer** — one or more user/actor molecules, visually grouped (top of the diagram by convention).
- **Third-party layer** — external SaaS, partner integrations.
- **BTP Layer** — the core of any SAP solution diagram, containing all BTP service molecules clustered by capability area.
- **SAP application layer** — S/4HANA, SuccessFactors, Ariba, etc., visually grouped below the BTP layer.
- **Non-SAP system layer** — on-prem databases, legacy systems.
- **Cross-cutting concerns** — observability, security, identity (right side or bottom of the diagram).

**Rule**: every diagram has at least 3 organisms (user + BTP + 1 external). L0 may collapse externals into a single organism; L2 always shows them separately.

## How the plugin uses atomic design

The JSON intermediate representation (`scripts/generate-drawio.py`) maps to atomic design 1:1:

| JSON entity | Atomic design level | Catalog source |
|---|---|---|
| `metadata` (palette / fonts) | atoms | `foundation.md` |
| `nodes[]` (single component) | molecules | service icons + `area_shapes.xml` + `default_shapes.xml` |
| `groups[]` (cluster of components) | organisms | `essentials.xml` (preset compositions) |
| `edges[]` (line + label) | molecules | `connectors.xml` (87 variants) + `annotations_and_interfaces.xml` (6 pills) |
| Interface badge child | atom | `annotations_and_interfaces.xml` (Interface SAP / Generic) |
| Step number child | atom | `numbers.xml` (7 colour variants × 1-9) |

### Optional node fields (Phase 4 extensions)

| Field | Type | Purpose | Example |
|---|---|---|---|
| `boxStyle` | string | Variant from `area_shapes.xml`. Applied only to nodes WITHOUT a SAP icon. Vocabulary: `btp-filled`, `btp-outline`, `btp-dashed`, `btp-dotted`, `non-sap-{filled\|outline\|dashed\|dotted}`, `accent-{teal\|purple\|pink}[-outline\|-dashed]`, `positive`, `critical`, `negative`. | `"boxStyle": "accent-purple"` highlights an AI / GenAI component |
| `interface` | string | Renders an "Interface" pill at the top of the node (from `annotations_and_interfaces.xml`). `"sap"` = blue `#0070f3`, `"generic"` = grey `#475f75`. | `"interface": "sap"` for an exposed BTP API |
| `step` | integer (1-9) | Numbered circle overlaid at top-left (from `numbers.xml`). | `"step": 3` for the 3rd step in a flow |
| `stepKind` | string | Step circle colour: `default` (grey), `blue`, `purple`, `pink`, `green`, `yellow`, `teal`. | `"stepKind": "blue"` |

These four fields combine: a single node can be a SAP icon, with a step number, an Interface badge, and a custom box style for the fallback when the icon doesn't resolve.

The `groups[].type` field selects the right organism style:

- `user` → white fill, non-SAP border, rounded
- `third-party` → light grey fill, non-SAP border, rounded
- `btp-layer` → BTP fill `#EBF8FF`, BTP border `#0070F2`, rounded
- `subaccount` → white fill, BTP border, nestable inner frame ("Subaccount: …")
- `governance` → BTP fill/border, wide top-band strip
- `cloud-tier` → tier box; border/fill follow `kind` (`public`/`private` = BTP-blue, `any-premise` = non-SAP grey)
- `custom-app` → BTP fill `#EBF8FF`, BTP border (bespoke apps built on BTP)
- `sap-app` → white fill, BTP border (BTP-affiliated SAP cloud apps)
- `non-sap` → light grey fill, non-SAP border

### Identity placement

The identity cluster (IAS / XSUAA / Authorization) is never merged into a generic ops/third-party box. Two valid placements only:

1. **Parented to the BTP frame** (`"parent": "<btp-group-id>"`) — nests inside as its own labelled BTP-blue inner frame, typically near the bottom of the frame.
2. **Standalone** (no `parent`) — its own `btp-layer`-typed group positioned just below the main BTP frame (`"position": "bottom"`), never on the RIGHT beside the backend/tier boxes.

## Common mistakes

- **Drawing freeform shapes**: any star / cloud / hexagon outside the SAP shape libraries breaks the system.
- **Inconsistent stroke widths**: keep `strokeWidth=1.5` for all molecules and organisms. The only exception is the firewall (`strokeWidth=3`).
- **Mixing icon sizes (S/M/L) inside one organism**: pick one size per organism. Mixing reads as visual noise.
- **Using gradients**: Horizon is flat. Skip drop-shadows and gradients.
- **Custom rounded-corner radii**: with `absoluteArcSize=1`, the standard arc is `arcSize=16` (inner boxes/lanes), `arcSize=24` (top-level areas) or `arcSize=32` (BTP base layer). Don't deviate.
