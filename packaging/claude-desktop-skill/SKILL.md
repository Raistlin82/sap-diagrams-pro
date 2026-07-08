---
name: sap-diagram-generate
description: Generate SAP-compliant draw.io (.drawio) architecture diagrams — SAP BTP solution diagrams at level L0/L1/L2 — from a natural-language description. Grounds component names in the SAP Discovery Center (via the SAP docs connector when available), then runs a bundled deterministic engine to emit a downloadable .drawio styled to the official SAP BTP Solution Diagram Guideline (Horizon palette, zone composition). Use when asked to create / draw / generate a SAP or BTP architecture or solution diagram, or when SAP services are named (CAP, S/4HANA, Integration Suite, DOX / SAP Document AI, AI Core, Build Process Automation, Event Mesh, PCE, RISE).
license: Apache-2.0
---

# Generate a SAP-Compliant Architecture Diagram

This skill bundles a deterministic Python engine that renders a JSON intermediate
representation (IR) into a `.drawio` file styled per the official
[SAP BTP Solution Diagram Guideline](https://sap.github.io/btp-solution-diagrams/)
(Horizon palette, atomic-design molecules, zone composition).

**Golden rule: ground the content first, then render.** A diagram is only as good
as the inventory behind it — verify component names and categories before drawing.

## Environment notes (read first)
- Runs in the **code-execution sandbox**. It produces a **`.drawio` file** to
  download and open in draw.io desktop / [drawio.com](https://drawio.com), and can
  also emit a **PNG preview** via the bundled pure-Python renderer (no draw.io app
  needed — `render-preview.py` + the bundled fonts and icon atlas).
- **Grounding** uses the SAP documentation connector (`mcp-sap-docs`) *if it is
  enabled in this workspace*. If it isn't, proceed with best-effort canonical
  names and tell the user that enabling the connector
  (<https://github.com/marianfoo/mcp-sap-docs>, a community server — not SAP)
  improves accuracy. Only generic SAP product names are sent to it.

## Procedure

### 1. Ground the components
If the SAP docs connector is available, look up each named/implied component in
the **SAP Discovery Center** to get the **canonical service name**, **category**
(decides BTP-service vs standalone SaaS-product placement, and the icon set), and
deprecation status. Use the canonical name in the IR `service` field so the icon
resolves. Examples: "DOX" → product is now **"SAP Document AI"** (resolve the icon
via the historic name `Document Information Extraction`); "Enterprise Messaging" →
**"SAP Event Mesh"**.

### 2. Interview the user (only what's ambiguous)
Confirm just what you can't infer: **level(s)** (L0/L1/L2, default L1); **runtime**
(Cloud Foundry / Kyma); **identity** (IAS + XSUAA / external IdP); **integration**
(sync OData/REST vs async events); **backends** (S/4HANA on-prem/PCE/Cloud,
non-SAP DBs); **connectivity** (Cloud Connector / Private Link); **observability**.

### 3. Build the IR (JSON)
```jsonc
{
  "metadata": { "title": "…", "level": "L0|L1|L2", "iconSize": "S|M|L (optional)" },
  "groups": [
    { "id": "users", "type": "user",       "position": "top-left" },
    { "id": "btp",   "type": "btp-layer",  "position": "center", "label": "SAP BTP" },
    { "id": "in",    "type": "btp-layer",  "parent": "btp", "label": "Inbound", "flow": "row" },
    { "id": "sys",   "type": "sap-app",    "position": "right", "label": "Backends" }
  ],
  "nodes": [
    { "id": "u1",  "label": "End User", "group": "users" },
    { "id": "cap", "label": "CAP Backend", "service": "Kyma Runtime", "group": "in", "interface": "sap", "step": 1 },
    { "id": "s4",  "label": "SAP S/4HANA", "subtitle": "Private Cloud Edition", "group": "sys" }
  ],
  "edges": [
    { "id": "e1", "source": "u1", "target": "cap", "style": "solid", "label": "Authenticate", "kind": "authenticate" }
  ]
}
```

**Group `type` selects the molecule automatically** (no manual layout needed):

| `type` | Zone | Rendered as |
|---|---|---|
| `user` | LEFT | **frameless** person/device icon + label |
| `btp-layer` | CENTER | blue container with a **"SAP BTP" logo chip**; nested lanes = white inner frames |
| `sap-app` | RIGHT | white backend **box** (icon-left + title + `subtitle`), blue border |
| `third-party` / `non-sap` / `external` | RIGHT | white backend **box**, grey border |

**Layout is deterministic**: `position` (`top-left`…`bottom-right`) → column
(LEFT/CENTER/RIGHT) + band (top/middle/bottom); containers auto-size to content.
Override with `zone` (column) and `flow` (`row`|`col`|`grid`).

Node options: `service` (canonical name → SAP icon), `genericIcon`
(`user`/`mobile`/`desktop`/`database`/…), `subtitle`, `interface` (`sap`|`generic`),
`step` (1–99) + `stepKind`. Edge `style`: solid(sync)/dashed(async)/dotted(optional)/thick(firewall).
Edge `kind`: `trust`/`authenticate`/`authorize`/`generic_protocol`/`annotation` render canonical pills.
Use kebab-case ids. L0 ≤ 10 elements · L1 10–30 · L2 ≥ 30.

### 4. Render in the sandbox
Write the IR to `ir.json`, then run the bundled engine (it lives in this skill's
`scripts/` dir, with `assets/` alongside):
```bash
python3 scripts/generate-drawio.py ir.json --out "<title>-<level>.drawio"
```

### 5. Validate, then deliver
```bash
python3 scripts/validate-ir.py       ir.json                      # IR v2 gate (pre-render)
python3 scripts/validate-drawio.py   "<title>-<level>.drawio"     # palette / XML
python3 scripts/check-composition.py "<title>-<level>.drawio"     # geometric gate
python3 scripts/render-preview.py    "<title>-<level>.drawio" --out "<title>-<level>.png"
```
Regenerate if a CRITICAL (validator) or FAIL (composition) appears, then **return
the `.drawio` file to the user** as a download (and the PNG preview if useful),
suggesting they open the `.drawio` in draw.io.

## Quality bar
Opens in draw.io · 0 validator CRITICAL / 0 composition FAIL · canonical service
names · right level · every edge labelled · consistent across multiple levels.
