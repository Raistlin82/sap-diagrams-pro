<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Horizon Palette — SAP Solution Diagram Colors

Source: [SAP BTP Solution Diagram Guideline — Foundation](https://sap.github.io/btp-solution-diagrams/docs/btp_guideline/foundation/).

The palette comes from SAP Horizon, the default visual style for SAP products. Using only these colors keeps diagrams visually consistent with SAP product UIs and the SAP Architecture Center catalogue.

## Primary colors

Used for the main element categories: SAP/BTP areas, non-SAP areas, and text.

| Purpose | Border | Fill | Notes |
|---|---|---|---|
| **SAP / BTP area** | `#0070F2` | `#EBF8FF` | Default for any group/box that represents BTP services or SAP-built components |
| **Non-SAP area** | `#475E75` | `#F5F6F7` | Default for 3rd-party systems, non-SAP databases, generic external services |
| **Title text** | — | — | Color: `#1D2D3E` (use for headers and important labels) |
| **Body text** | — | — | Color: `#556B82` (use for edge labels, captions, secondary descriptions) |

## Semantic colors

Used to convey status, criticality, or quality of a flow / component. Use sparingly — most diagram elements should use Primary.

| State | Border | Fill |
|---|---|---|
| **Positive** (green) | `#188918` | `#F5FAE5` |
| **Critical** (orange) | `#C35500` | `#FFF8D6` |
| **Negative** (red) | `#D20A0A` | `#FFEAF4` |

Examples of legitimate semantic-color usage:

- A failed or deprecated integration → red.
- A degraded / circuit-breaker-open dependency → orange.
- A success-path action / certified component → green.

## Accent colors

Secondary colors for emphasising specific elements. Use them sparingly to create vivid contrast — never as the dominant color of a diagram.

| Accent | Border | Fill |
|---|---|---|
| **Teal** | `#07838F` | `#DAFDF5` |
| **Purple** | `#5D36FF` | `#F1ECFF` |
| **Pink** | `#CC00DC` | `#FFF0FA` |

Typical use cases:

- Highlight a brand-new component in a roadmap diagram → teal or purple.
- Mark an "AI-powered" flow → purple.
- Visualise an experimental / beta service → pink.

## Allowed combinations

A diagram element's `(border, fill)` pair must be one of:

- `(#0070F2, #EBF8FF)` — BTP area
- `(#0070F2, #FFFFFF)` — BTP element inside a BTP area (inner contrast)
- `(#475E75, #F5F6F7)` — non-SAP area
- `(#475E75, #FFFFFF)` — user / actor (rounded with white fill)
- `(#188918, #F5FAE5)` / `(#C35500, #FFF8D6)` / `(#D20A0A, #FFEAF4)` — semantic
- `(#07838F, #DAFDF5)` / `(#5D36FF, #F1ECFF)` / `(#CC00DC, #FFF0FA)` — accent

Avoid: pure black `#000000`, neon colors, gradients (Horizon style is flat).

## Mapping in the generator

These colors are encoded in [`scripts/generate-drawio.py`](../../../scripts/generate-drawio.py) as the `PALETTE` dict and `GROUP_STYLES` mapping. The validator [`scripts/validate-drawio.py`](../../../scripts/validate-drawio.py) emits a `PALETTE_BORDER` / `PALETTE_FILL` warning for any color outside the sets above.

## Common mistakes

- **Black borders**: many drawio templates default to `#000000`. Always replace with `#0070F2` or `#475E75`.
- **Brand-color BTP fills**: people often use solid `#0070F2` as fill — wrong. Fill is `#EBF8FF`, only border is `#0070F2`.
- **Mixing purple + teal as primary**: accents are decorations, not the dominant scheme.
- **Yellow / orange highlighting**: use the Critical orange `#C35500` only for genuinely critical elements; for highlighting prefer the accent palette.
