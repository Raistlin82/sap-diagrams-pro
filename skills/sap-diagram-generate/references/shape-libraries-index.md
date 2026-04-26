<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Shape Libraries Index

The plugin uses 7 official SAP shape library sets, downloaded from [SAP/btp-solution-diagrams](https://github.com/SAP/btp-solution-diagrams) at first invocation and cached at `$SAP_DIAGRAMS_CACHE/btp-solution-diagrams/assets/shape-libraries-and-editable-presets/draw.io/`.

The parsed catalog lives at [`assets/shape-index.json`](../../../assets/shape-index.json), conformant to [`assets/shape-index.schema.json`](../../../assets/shape-index.schema.json).

## The 7 sets

| Set ID | Directory in SAP repo | What it contains | Typical L1/L2 use |
|---|---|---|---|
| `foundational` | `20-02-00-...-foundational-set` | Cloud Foundry runtime, HANA Cloud, Identity, Audit Log, basic platform | Always — every BTP diagram has at least 1 |
| `integration-suite` | `20-02-01-...-integration-suite-set` | Cloud Integration, API Mgmt, Open Connectors, Event Mesh, Trading Partner Mgmt | Any diagram with iflows, EDI, B2B |
| `app-dev-automation` | `20-02-02-...-app-dev-automation-set` | Build Apps, Build Process Automation, Build Code, Build Work Zone | Any diagram with a custom app/UI |
| `data-analytics` | `20-02-04-...-data-analytics-set` | SAC, Datasphere, Data Intelligence, HANA Cloud DB | Any diagram with reporting / analytics |
| `ai` | `20-02-05-...-ai-set` | AI Core, AI Launchpad, Joule, DOX, GenAI Hub | Any diagram with AI / ML / DOX |
| `btp-saas` | `20-02-06-...-btp-saas-set` | SAP-built SaaS apps that run on BTP (Task Center, Start, Cloud Identity) | Diagrams showing SAP cloud apps |
| `generic` | `20-03-generic-icons` | User, mobile device, database, server, network, browser | Non-SAP elements + all L0 diagrams |

A 8th aggregate set `all` exists in the SAP repo but is intentionally **skipped** by the index builder to avoid duplication.

## Sizes

Each shape exists in three sizes (S = 24px, M = 48px, L = 96px). The plugin defaults to **M** for L0 / L1 and **S** for L2 (more compact when many services are present).

The selected size is recorded in the `size` field of each `services[]` entry in `shape-index.json`. To change the default size in the generator, set `metadata.iconSize` to `"S"`, `"M"` or `"L"`.

## How the index is built

`scripts/build-shape-index.py` walks each set directory, parses each `*.xml` library file (a draw.io `mxlibrary` containing a JSON-encoded array of shape entries), and produces a flat catalog with these fields per service:

```json
{
  "name": "<canonical SAP service name>",
  "aliases": ["<acronym>", "<short form>"],
  "set": "<set-id>",
  "size": "S | M | L",
  "drawioStyle": "<full draw.io style attribute>"
}
```

The `drawioStyle` is the bare style string (without the `style="..."` wrapper) — it can be embedded directly into an `mxCell` element.

## Refreshing the index

When SAP releases new shape libraries (typically every 3-6 months), refresh the cache and rebuild the index:

```bash
bash scripts/bootstrap-cache.sh --refresh
python3 scripts/build-shape-index.py
```

The `meta.sourceCommit` field of the resulting `shape-index.json` records the upstream git SHA — use it to track which version of the SAP catalog you're using.

## Looking up a service

Two strategies, in order of preference:

1. **Exact match on `name`**: `services.find(s => s.name === 'SAP Build Process Automation')`
2. **Alias match**: `services.find(s => s.aliases.includes('BPA'))`
3. **Fuzzy substring match (case-insensitive)**: as a last resort, `s.name.toLowerCase().includes(query.toLowerCase())`

The `sap-icons-resolve` skill encapsulates this logic. From within `sap-diagram-generate`, invoke it for any service name that doesn't match exactly on first try.

## What's NOT in the index

- **Non-SAP icons** — the plugin generates plain rounded rectangles for non-SAP / third-party elements. Ad-hoc icons (e.g. PostgreSQL elephant logo) must be added manually post-generation.
- **Old SAP icon styles** — only the latest "Horizon redesign" icons are indexed. The deprecated set is intentionally excluded to encourage modern visuals.
- **Custom organisation logos** — your team logo, customer logo. Add post-generation if needed.

## Common mistakes

- **Hardcoding service names**: the SAP catalog evolves. Always look up via `assets/shape-index.json`, never bake names into prompts/templates.
- **Using L-size icons in L2 diagrams**: L (96px) icons crowd the canvas. Use S or M.
- **Mixing sets visually**: don't switch between the redesigned (current) and legacy icon styles in the same diagram.
- **Missing services**: if `assets/shape-index.json` doesn't contain a service the user named, fall back to a labelled box (no icon) and report the gap to the user — don't fabricate an icon.
