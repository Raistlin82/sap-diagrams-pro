<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Line Styles, Connectors and Spacing

Source: [SAP BTP Solution Diagram Guideline — Foundation](https://sap.github.io/btp-solution-diagrams/docs/btp_guideline/foundation/) (Line Styles + Spacing sections), [Diagr Components — Lines and Connectors](https://sap.github.io/btp-solution-diagrams/docs/btp_guideline/diagr_comp/lines_connectors/).

## Line styles — semantic conventions

The SAP guideline assigns specific semantics to line styles. **Always include a legend in any diagram that uses more than one style** — otherwise the convention is ambiguous to readers unfamiliar with SAP standards.

| Style | drawio attribute | Meaning |
|---|---|---|
| **Solid** | `dashed=0;strokeWidth=1.5` | Direct, **synchronous** request-response data flow (HTTP / OData / RFC). |
| **Dashed** | `dashed=1;dashPattern=8 4;strokeWidth=1.5` | Indirect, **asynchronous** flow (events, message queues, webhooks). |
| **Dotted** | `dashed=1;dashPattern=1 4;strokeWidth=1.5` | Optional / conditional flow (only on certain configurations or scenarios). |
| **Thick** | `strokeWidth=4` | Reserved for **firewall boundaries** only. Never use thick lines for emphasis on regular flows. |

The plugin's `EDGE_STYLES` dict in `generate-drawio.py` encodes exactly these four. Adding a new style requires updating both the generator and the validator.

## Edge kind — semantic colours

Beyond the line style, an edge can declare a semantic ``kind`` that switches its stroke colour and (for `trust`) the label rendering. The default is `"default"` (Horizon non-SAP grey `#475E75`). Set ``kind`` on the JSON edge object alongside ``style``, ``label`` and ``direction``.

| `kind` | Stroke | Pill colors | Use case | Notes |
|---|---|---|---|---|
| `default` | `#475E75` (grey) | — (plain label) | Standard data flow | White-background label, no pill. |
| `trust` | `#CC00DC` (pink) | stroke `#CC00DC`, fill `#FFF0FA` | IAS ↔ XSUAA, identity federation, OAuth trust | Auto-bidirectional. SAP-canonical "Trust" pill from `annotations_and_interfaces.xml`. |
| `authenticate` | `#188918` (green) | stroke `#188918`, fill `#F5FAE5` | User → app login flow, IAS authentication | One-directional. Matches the SAP "Authenticate" pill. |
| `authorize` | `#470BED` (purple) | stroke `#470BED`, fill `#F1EDFF` | XSUAA / Authorization Service token validation | One-directional. Matches the SAP "Authorize" pill. |
| `generic_protocol` | `#475F75` (grey) | stroke `#475F75`, fill `#F5F6F7` | Named protocol (OData, REST, GraphQL, RFC) | One-directional. Replaces verbose inline labels like "API_SUPPLIERINVOICE_PROCESS_SRV" with a clean pill. |
| `positive` | `#188918` (green) | — (no pill, line only) | Certified / success flow | Use sparingly. |
| `critical` | `#C35500` (orange) | — | Degraded / at-risk flow | E.g. circuit-breaker-open dependency. |
| `negative` | `#D20A0A` (red) | — | Failed / deprecated flow | E.g. removed integration. |

Example trust edge in JSON:

```json
{
  "id": "e-trust-1",
  "source": "ias",
  "target": "xsuaa",
  "style": "solid",
  "label": "Trust",
  "kind": "trust"
}
```

The plugin will:

1. Render the connector as a pink (`#CC00DC`) bidirectional `blockThin` arrow.
2. Emit a separate child `mxCell` with `vertex="1"` and `parent=<edge_id>`, styled as a rounded pill (`arcSize=50`, pink border, `#FFF0FA` fill, bold pink text), centred on the edge midpoint via relative geometry + offset.

Why a separate vertex for the pill: drawio does not honour `arcSize` on inline edge labels. The SAP samples (e.g. cell `r2Ocmoq0C5dt8iKspmja-162` in Private Link L2) use the same multi-cell pattern.

## Arrow direction

| Direction | drawio attribute | Use when |
|---|---|---|
| **Forward** | `endArrow=classic; startArrow=none` | Default: data flows from source to target. |
| **Bidirectional** | `endArrow=classic; startArrow=classic` | Symmetric flow (e.g. WebSocket, HTTP duplex). Use sparingly — most "bidirectional" flows are actually request-response, which is forward. |
| **None** | `endArrow=none; startArrow=none` | Visual association only, no data flow (e.g. "is part of"). Rare — usually a group is a better choice. |

## Connectors and routing

- **Right-angle** routing is the default for SAP diagrams (matches the orthogonal grid layout). drawio uses `edgeStyle=orthogonalEdgeStyle` for this.
- **Curved** routing is acceptable for organic flows in L0 diagrams; never in L1/L2.
- **Straight** routing is acceptable for very short flows (e.g. user → app on the same row).

The plugin uses default routing (no `edgeStyle` set), which lets drawio choose at render time. For consistency, prefer adding `edgeStyle=orthogonalEdgeStyle` post-generation if the layout looks crowded.

## Edge labels

- **Always label edges**, even with one word. Unlabelled edges are a code smell.
- Use **body text color** `#556B82` for labels.
- Keep labels short: 1-3 words. "OData v4", "async event", "OAuth2 callback".
- Position: middle of the edge (drawio default with `align=center`).

## Spacing — the SAP-logo rule of thumb

> "Spacing around objects should be even and roughly the height of the SAP logo."

In practice for a 1600×1000 canvas:

| Element pair | Recommended gap |
|---|---|
| Group ↔ group | ≥ 32px |
| Group inner padding | 24px (top), 16px (sides) |
| Node ↔ node within group | 24px horizontal, 24px vertical |
| Node ↔ group border | 16px |
| Title ↔ first group | 32px |
| Edge label ↔ edge endpoint | 8-12px |

The plugin's auto-layout (`generate-drawio.py`) enforces these spacings via `GROUP_PADDING`, `NODE_GAP_X`, `NODE_GAP_Y` constants.

## Common mistakes

- **Mixed line semantics in the same diagram**: don't use dashed for both async AND optional. Pick one meaning per style.
- **Using thick lines for emphasis**: thick is firewall only. For emphasis, use color or position, not width.
- **Crowded edges**: if you have 6+ edges crossing each other, the diagram is too dense. Split into multiple diagrams or move to a higher level.
- **Edges crossing groups**: avoid routing edges through unrelated groups. drawio's auto-router can be told to avoid certain shapes via `noEdgeStyle=1`.
- **Inconsistent stroke widths**: keep `1.5` for everything except firewalls. If your validator reports `strokeWidth` outside this range, regenerate.
- **No legend in mixed-style diagrams**: a small text box "Legend: solid=sync, dashed=async" prevents misreadings.

## Legend template

Add a legend organism in the bottom-right (or below the title) for any diagram using ≥ 2 line styles:

```
┌─────────── Legend ───────────┐
│  ─────  synchronous (HTTP)   │
│  - - -  asynchronous (events)│
│  · · ·  optional             │
│  ▬▬▬▬▬  firewall              │
└──────────────────────────────┘
```

This is a "molecule" in atomic-design terms. The plugin does not auto-add legends today — opt-in via the `metadata.includeLegend: true` JSON field (planned for v0.2).
