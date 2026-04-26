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
- **Text** (font: SAP 72; sizes: 18 for title, 13 for group labels, 11 for node labels, 10 for edge labels)

**Rule**: never invent a new atom. Use only colors / lines / icons / fonts from the catalog.

## Molecules

Combinations of atoms that have a small but recognisable meaning.

**Common molecules:**

- A **box** with a Horizon border + fill + a label (e.g. a rounded rectangle with `strokeColor=#0070F2; fillColor=#EBF8FF`).
- An **arrow** with a line style + label (e.g. dashed arrow with "async" text).
- An **icon + label** combo (e.g. the BPA icon with "SAP Build Process Automation" caption).
- A **swim-lane header** (a rectangle with no fill and a Horizon-colored bottom border).

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
- `sap-app` → white fill, BTP border (BTP-affiliated SAP cloud apps)
- `non-sap` → light grey fill, non-SAP border

## Common mistakes

- **Drawing freeform shapes**: any star / cloud / hexagon outside the SAP shape libraries breaks the system.
- **Inconsistent stroke widths**: keep `strokeWidth=1.5` for all molecules and organisms. The only exception is the firewall (`strokeWidth=4`).
- **Mixing icon sizes (S/M/L) inside one organism**: pick one size per organism. Mixing reads as visual noise.
- **Using gradients**: Horizon is flat. Skip drop-shadows and gradients.
- **Custom rounded-corner radii**: the standard arc is `arcSize=8` (nodes) or `arcSize=12` (groups). Don't deviate.
