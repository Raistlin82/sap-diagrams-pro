---
name: sap-diagram-generate
description: Generate SAP-compliant draw.io (.drawio) architecture diagrams — SAP BTP solution diagrams at level L0/L1/L2 — from a natural-language description. Grounds component names in the SAP Discovery Center (via the SAP docs connector when available), then runs a bundled deterministic engine (IR v2 → skeleton slot layout → channel-routed edges → geometric gate → visual-rubric self-critique loop) to emit a downloadable .drawio styled to the official SAP BTP Solution Diagram Guideline (Horizon palette, atomic-design molecules, zone composition) plus a PNG preview. Use when asked to create / draw / generate a SAP or BTP architecture or solution diagram, or when SAP services are named (CAP, S/4HANA, Integration Suite, DOX / SAP Document AI, AI Core, Build Process Automation, Event Mesh, PCE, RISE).
license: Apache-2.0
---

# Generate a SAP-Compliant Architecture Diagram

This skill bundles a deterministic Python engine that renders a JSON intermediate
representation (IR v2) into a `.drawio` file styled per the official
[SAP BTP Solution Diagram Guideline](https://sap.github.io/btp-solution-diagrams/)
(Horizon palette, atomic-design molecules, zone composition, channel-routed edges).

**Golden rule: ground the content first, then render.** A diagram is only as good
as the inventory behind it — verify component names and categories before drawing.

## Environment notes (read first)

- Runs in the **code-execution sandbox**. Everything the engine needs is bundled in
  this skill: `scripts/` (the engine) with `assets/` alongside (style contract,
  shape index, public brand pack, icon atlas, bundled Arimo fonts). Run the scripts
  with paths **relative to the skill root** (`scripts/…`, `assets/…`).
- It produces a **`.drawio` file** to download and open in draw.io desktop /
  [drawio.com](https://drawio.com), and a **PNG preview** via the bundled
  pure-Python renderer — **no draw.io app needed** (`render-preview.py --engine pure`
  uses the bundled fonts + icon atlas).
- **Grounding** uses the SAP documentation connector (`mcp-sap-docs`) *if it is
  enabled in this workspace*. If it isn't, proceed with best-effort canonical names
  and tell the user that enabling the connector
  (<https://github.com/marianfoo/mcp-sap-docs>, a community server — not SAP)
  improves accuracy. Only generic SAP product names are sent to it.
- **Confidentiality:** this bundle carries only the PUBLIC brand pack. Customer
  logos / trademarks resolve to a neutral text chip; that is expected.

## Procedure

### 1. Ground the components
If the SAP docs connector is available, look up each named/implied component in the
**SAP Discovery Center** to get the **canonical service name**, **category** (decides
BTP-service vs standalone SaaS-product placement, and the icon set), and deprecation
status. Use the canonical name in the IR `service` field so the icon resolves.
Examples: "DOX" → product is now **"SAP Document AI"** (resolve the icon via the
historic name `Document Information Extraction`); "Enterprise Messaging" → **"SAP
Event Mesh"**.

### 2. Interview the user (only what's ambiguous)
Confirm just what you can't infer: **level(s)** (L0/L1/L2, default L1); **runtime**
(Cloud Foundry / Kyma); **identity** (IAS + XSUAA / external IdP); **integration**
(sync OData/REST vs async events); **backends** (S/4HANA on-prem/PCE/Cloud, non-SAP
DBs); **connectivity** (Cloud Connector / Private Link); **observability**.

### 3. Build the IR v2 (JSON)

The IR has `metadata`, `groups[]`, `nodes[]`, `edges[]`, and optional `layoutHints[]`.
**Group `type` selects the molecule and zone automatically** — no manual coordinates.

**`groups[]` — `type` → zone → molecule**

| `type` | Zone | Rendered as |
|---|---|---|
| `user` | LEFT | **frameless** person/device icon + label |
| `btp-layer` | CENTER | blue container with a **"SAP BTP" chip**; nested lanes = white inner frames |
| `subaccount` | CENTER (nest via `parent`) | white BTP-bordered inner frame; **nestable** (e.g. `Extension Test` ⊃ `Extension Production`) |
| `governance` | TOP band | wide BTP-blue strip for cross-cutting governance/monitoring (e.g. Cloud ALM) |
| `cloud-tier` | RIGHT | tier box; `kind: "public"\|"private"\|"any-premise"` (public/private = SAP-blue border, any-premise = grey) |
| `custom-app` | RIGHT | BTP-blue **product-style** card for a bespoke app built on BTP |
| `sap-app` | RIGHT | white backend **box** (icon-left + title + `subtitle`), BTP-blue border |
| `third-party` / `non-sap` / `external` | RIGHT | white backend **box**, grey border |

Every group also accepts: `badges: {hyperscalers: [...], runtimes: [...]}` (small
logo badges; runtimes render as a text chip), `parent` (nesting), `position`
(`top-left`…`bottom-right` → column+band), `zone` (`left`\|`center`\|`right`,
overrides the column), `flow` (`row`\|`col`\|`grid`, intra-group packing).

**`nodes[]`**

| field | purpose |
|---|---|
| `service` | canonical Discovery-Center name → SAP icon |
| `genericIcon` | `user`/`mobile`/`desktop`/`database`/… when there's no service icon |
| `type` | `product` (leaf molecule with a `capabilities` grid), `chip` (small white BTP-bordered label, e.g. a PCE/runtime marker), `db` (cylinder datastore) — omit for a plain node |
| `capabilities` | `[{label, icon?}, …]` (only on `type:"product"`) → icon+label grid inside the box; `icon` optional (bare labels render text-only) |
| `subtitle` | one-line caption under the title (backend/product box) |
| `interface` | `sap`\|`generic` → an "Interface" pill atop the node |
| `step` / `stepKind` | numbered step circle (1–99 / colour name) |

**`edges[]`**

| field | purpose |
|---|---|
| `style` | `solid`(sync)\|`dashed`(async)\|`dotted`(optional)\|`thick`(firewall) |
| `flowFamily` (v2, preferred) | `identity`\|`provisioning`\|`master-data`\|`transport`\|`firewall`\|`default` → colour + dash family; `firewall` = `strokeWidth=3` |
| `kind` | `trust`\|`authenticate`\|`authorize`\|`generic_protocol`\|`annotation`\|`default` → canonical pill/colour when no `flowFamily` |
| `pill` | free-text protocol label on the edge (`"SAML2/OIDC"`, `"SCIM"`, `"CTMS"`) |

Use kebab-case ids. L0 ≤ 10 elements · L1 10–30 · L2 ≥ 30.

**Identity placement.** Never fold the identity cluster (IAS / XSUAA / Authorization)
into a generic ops/third-party box. Parent it to the BTP frame (`parent:"<btp-id>"`)
→ it nests as its own labelled BTP-blue inner frame near the bottom; if standalone,
give it its own `btp-layer` group `position:"bottom"` — never on the RIGHT with backends.

**Worked example (archetype A)** — governance strip + nested subaccounts + a
`product` node with capabilities + `cloud-tier` + `flowFamily` edges + branding:

```json
{
  "metadata": {
    "title": "Archetype A", "level": "L1",
    "branding": {"customerLogo": "acme", "partnerWatermark": "lutech"},
    "badges": {"hyperscalers": ["azure"], "runtimes": ["cloud-foundry"]}
  },
  "layoutHints": [],
  "groups": [
    {"id": "governance", "type": "governance", "label": "Governance", "position": "top"},
    {"id": "btp", "type": "btp-layer", "label": "SAP BTP", "position": "center"},
    {"id": "subaccount-test", "type": "subaccount", "label": "Test", "parent": "btp"},
    {"id": "subaccount-production", "type": "subaccount", "label": "Production", "parent": "subaccount-test"},
    {"id": "cloud-tier-right", "type": "cloud-tier", "label": "Private Cloud", "position": "right", "kind": "private"},
    {"id": "personas", "type": "user", "label": "Personas", "position": "left"},
    {"id": "identity", "type": "btp-layer", "label": "Identity", "position": "bottom"}
  ],
  "nodes": [
    {"id": "cloud-alm", "label": "Cloud ALM", "group": "governance", "type": "product", "service": "Cloud ALM",
     "capabilities": [{"label": "Monitor", "icon": "monitor"}, {"label": "Analyze"}, {"label": "Automate", "icon": "automate"}, {"label": "Alert"}]},
    {"id": "bpa", "label": "Build Process Automation", "group": "subaccount-production", "type": "product", "service": "Build Process Automation",
     "capabilities": [{"label": "Workflow", "icon": "workflow"}, {"label": "Decision"}, {"label": "Visibility", "icon": "visibility"}, {"label": "RPA"}]},
    {"id": "pce", "label": "Private Cloud Edition (PCE)", "group": "cloud-tier-right", "type": "chip"},
    {"id": "persona-admin", "label": "IT Admin", "group": "personas", "genericIcon": "user"},
    {"id": "ias", "label": "Identity Authentication", "group": "identity", "service": "Identity Authentication"}
  ],
  "edges": [
    {"id": "e1", "source": "persona-admin", "target": "ias", "style": "solid", "label": "Login", "flowFamily": "identity", "pill": "SAML2/OIDC"},
    {"id": "e2", "source": "cloud-alm", "target": "bpa", "style": "dashed", "label": "Process insights", "flowFamily": "master-data"},
    {"id": "e3", "source": "bpa", "target": "pce", "style": "solid", "label": "Deploy config", "flowFamily": "transport", "pill": "CTMS"}
  ]
}
```

### 4. Validate the IR (pre-render gate)
Write the IR to `ir.json`, then:
```bash
python3 scripts/validate-ir.py ir.json
```
- **Exit 0** (`OK`) → go to step 5.
- **Exit 2** → fix the `ERROR <where>: <what>.` lines (wrong enum, dangling `parent`,
  malformed `capabilities`/`badges`/`branding`, cyclic parenting) and re-validate.
  Do not generate until this exits 0.

### 5. Generate + mechanical gate
```bash
python3 scripts/generate-drawio.py   ir.json --out "<title>-<level>.drawio"
python3 scripts/validate-drawio.py   "<title>-<level>.drawio" --strict   # palette/XML; exit 1 on CRITICAL
python3 scripts/check-composition.py "<title>-<level>.drawio"             # geometric gate; exit 2 on FAIL
```
On any CRITICAL/FAIL: fix the IR and regenerate. **Max 2 mechanical retries** before
escalating to the user with the exact error.

### 6. Render, then LOOK — the visual-rubric self-critique loop
```bash
python3 scripts/render-preview.py "<title>-<level>.drawio" --engine pure --out "<title>-<level>.png"
```
**Open the PNG and evaluate it against [`references/visual-rubric.md`](references/visual-rubric.md)**
(the ~25 binary checks across Composition / Routing / Typography / Semantics bundled
in this skill). For each failing check emit a findings object — one of the 7 patch
ops, or `null` for a manual/content finding (recolor, icon swap, legend text):
```json
[{"rule": "route-no-pierce", "location": "edge 'e3' cuts through the BPA box", "patch": {"op": "channel_prefer", "edge": "e3", "value": "V2"}}]
```
Then apply and regenerate:
```bash
python3 scripts/apply-rubric-patches.py ir.json --findings findings.json
# then repeat step 5 (generate + gate) and step 6 (render + look) on the same ir.json
```
**Repeat the look → patch → regenerate loop at most 3 visual iterations.** Never
invent an 8th op. If Pillow is somehow unavailable so no PNG can be produced, skip
the visual loop, run the geometric gate only, and say so as a WARNING — never
dead-end; still deliver the `.drawio`.

### 7. Deliver
Deliver only when `check-composition.py` reports **0 FAIL** and every auto-checkable
rubric item passes. **Return the `.drawio` file to the user** as a download (and the
PNG preview), suggesting they open the `.drawio` in draw.io. Include a scorecard:
```
Gate: 0 FAIL / N WARN (check-composition) · 0 CRITICAL (validate-drawio)
Rubric: X/25 pass (manual findings outstanding: …)
Vision iterations used: n/3
```
Surface any remaining manual/content findings as a punch list; log any user override
of a residual WARN.

## Quality bar
Opens in draw.io · `validate-ir.py` exits 0 · 0 validator CRITICAL / 0 composition
FAIL · every auto-checkable rubric item passes (manual findings disclosed) · canonical
service names · right level · every edge labelled · consistent across levels.
