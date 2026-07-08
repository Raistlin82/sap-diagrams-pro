<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Claude Desktop / claude.ai Agent Skill — `sap-diagram-generate`

A **self-contained Agent Skill** port of the plugin's generator, for **Claude
Desktop / claude.ai** and the **Claude API workspace**. It bundles the
deterministic Python engine + the SAP shape index, so it produces a downloadable
**`.drawio`** entirely inside the code-execution sandbox.

> This is a **separate artifact** from the Claude Code plugin. Use the plugin
> (`/plugin install …`) for the full local experience (PNG preview, local MCP,
> SAP-domain skills). Use this skill when you need it inside Claude Desktop / the
> API workspace, where those local capabilities aren't available.

## Build the bundle

```bash
bash packaging/claude-desktop-skill/build.sh
```

Produces (gitignored): `dist/claude-desktop-skill/sap-diagram-generate/` and
`dist/claude-desktop-skill/sap-diagram-generate.zip`. Re-run after any change to
`scripts/` or `assets/` — the engine is copied in, single source of truth.

## Option A — claude.ai / Claude Desktop (per user)

Each colleague, once:
1. **Settings → Capabilities → Skills** (requires a plan with code execution; the
   "Create and edit files" / code tool must be on).
2. **Upload** `sap-diagram-generate.zip`.
3. Use it: *"Generate an L1 SAP BTP solution diagram for …"* → download the
   produced `.drawio`.

There is **no central admin push** for Skills on claude.ai — each user uploads
the zip themselves.

## Option B — Claude API / Enterprise workspace (shared org-wide)

Upload once via the **Skills API**; the skill is then shared across the whole
workspace (all members/apps using the API). Requires an Enterprise/Team workspace
and the relevant beta headers (e.g. `skills-2025-10-02`, `code-execution-2025-08-25`,
`files-api-2025-04-14`). See the official docs:
<https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview> and
the enterprise guide. This is the closest thing to "company-wide" for the
non-Claude-Code surface.

## Grounding connector (recommended)

So Claude can ground component names in the SAP Discovery Center, add the SAP docs
MCP as a **connector** in Claude Desktop (Settings → Connectors) /  workspace
connectors: `https://mcp-sap-docs.marianzeis.de/mcp` — **community-operated, not
SAP**; only generic SAP product names are sent to it. Without it, the skill still
works (best-effort names) but classification/naming is less reliable.

## What's bundled

The full perfect-diagrams engine runs self-contained in the sandbox:
- **Entry points**: `generate-drawio.py` (IR v2 → `.drawio`), `validate-ir.py`,
  `validate-drawio.py`, `check-composition.py` (geometric gate),
  `apply-rubric-patches.py` (visual-rubric loop), `render-preview.py` (PNG).
- **Private modules**: `_skeleton_layout.py`, `_channel_router.py`, `_molecules.py`,
  `_geom_checks.py`, `_pure_render.py`, `_drawio_io.py`.
- **Assets**: `style-contract.json`, `shape-index.json`, `canonical-pills.json`,
  `brand-pack/` (public chips only), `icon-atlas/`, bundled Arimo fonts, and the
  `references/visual-rubric.md` doc. The gitignored `brand-pack.local/`
  (trademarks / customer logos) is **excluded**.

Because the pure-Python renderer (`_pure_render.py` + bundled fonts + icon atlas)
is bundled, the skill now also produces a **PNG preview** in the sandbox — no
draw.io app required.

## Limits vs the Claude Code plugin
- **No local MCP / SAP-domain skills** → grounding is via the remote connector +
  Claude's built-in knowledge, not the `secondsky/sap-skills` best-practice pass.
- `preflight.py` (the dependency/MCP preflight) is Claude-Code-only and is
  **not** bundled.
