---
name: sap-icons-resolve
description: Resolve a SAP service name (canonical or alias like BPA, DOX, CAP, IS) to the corresponding draw.io shape from the SAP shape libraries (foundational, integration-suite, app-dev-automation, data-analytics, ai, btp-saas, generic). Returns the matching service entry with the draw.io style snippet ready to embed in an mxCell. Use when the user asks for the icon of a SAP service, when looking up shape XML for a specific BTP service, or when other skills need to resolve service names.
argument-hint: "<service name or alias>"
allowed-tools: Read, Bash
version: 0.1.0
---

# Resolve a SAP Service Name to a draw.io Shape

Look up a SAP service by canonical name or common alias and return the matching shape entry from `assets/shape-index.json`.

## When to invoke this skill

- User asks "what's the icon for SAP Build Process Automation?"
- User asks "give me the BPA / DOX / CAP shape"
- The `sap-diagram-generate` skill needs to resolve a node's `service` field
- An external script needs to compose draw.io XML programmatically

This skill is **mostly internal** — typically chained from `sap-diagram-generate`. End users invoke it directly only for ad-hoc lookups.

## Inputs

- **Service name** — canonical SAP service name, an alias, or a partial match.

Examples that should resolve:

- `Build Process Automation` (canonical)
- `BPA` (acronym alias)
- `Document Information Extraction`, `DOX` (acronym)
- `CAP`, `Cloud Application Programming Model`
- `Integration Suite`, `IS`
- `Cloud Integration`, `CPI`

## Procedure

### Step 1 — Ensure the shape index is built

```bash
test -f "${CLAUDE_PLUGIN_ROOT}/assets/shape-index.json" || {
  bash "${CLAUDE_PLUGIN_ROOT}/scripts/bootstrap-cache.sh"
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/build-shape-index.py"
}
```

### Step 2 — Match in priority order

Search the `services[]` array in `${CLAUDE_PLUGIN_ROOT}/assets/shape-index.json` using:

1. **Exact name match** (case-sensitive) — best result.
2. **Exact alias match** — second best.
3. **Case-insensitive name match** — common for user typos.
4. **Substring match (case-insensitive)** — fuzzy fallback.
5. **No match** — return null and recommend the closest 3 by Levenshtein distance.

Preference inside results: pick the entry with `size: "M"` (the default size). Fall back to `S` for L2 use cases or `L` for L0 use cases.

### Step 3 — Return the resolved entry

For matches, return a single JSON object:

```json
{
  "name": "SAP Build Process Automation",
  "aliases": ["BPA"],
  "set": "app-dev-automation",
  "size": "M",
  "drawioStyle": "shape=mxgraph.sap.icons...; ..."
}
```

For misses, return:

```json
{
  "match": null,
  "suggestions": [
    {"name": "<closest 1>", "score": 0.85},
    {"name": "<closest 2>", "score": 0.72},
    {"name": "<closest 3>", "score": 0.61}
  ]
}
```

### Step 4 — Compose the mxCell when asked

If the caller wants ready-to-paste XML, wrap the resolved style:

```xml
<mxCell id="<id>" value="<label>" style="<drawioStyle>" vertex="1" parent="1">
  <mxGeometry x="0" y="0" width="80" height="80" as="geometry" />
</mxCell>
```

Default geometry: 80×80 for `M` size. 40×40 for `S`. 160×160 for `L`.

## Common aliases

The shape index includes auto-generated acronyms; common manual aliases are documented in [`references/service-aliases.md`](references/service-aliases.md). Extend that file when you discover a frequently-asked alias that isn't auto-detected.

## Quality bar

A "good" resolution:

- Returns a deterministic match for exact names.
- Returns sensible suggestions for misses (no random results).
- Never fabricates a service that isn't in the index.
- Reports the source set (`foundational`, `integration-suite`, …) so the caller can filter by capability domain.

## References

- [`references/service-aliases.md`](references/service-aliases.md) — manually-curated alias map (canonical name ↔ common short forms).
- [`../sap-diagram-generate/references/shape-libraries-index.md`](../sap-diagram-generate/references/shape-libraries-index.md) — overview of the 7 shape sets.
- [`../../assets/shape-index.json`](../../assets/shape-index.json) — the full catalog (regenerate with `scripts/build-shape-index.py`).

## Limitations

- **Service names in the index are derived from SAP's internal IDs** (e.g. `10002-cloud-integration-automation_sd`). The auto-generated aliases may miss intuitive short forms.
- **Adding a manual alias** requires editing `references/service-aliases.md` and re-running `build-shape-index.py` (or shipping a future v0.2 enhancement that merges that file at index-build time).
- **Generic icons** (User, Database, Server, Browser, Network) are in the `generic` set — match for keywords "user", "database", "server", etc.
