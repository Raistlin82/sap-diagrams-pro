<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Installing `sap-diagrams-pro` (Claude Code plugin)

`sap-diagrams-pro` is a **Claude Code plugin** (it bundles the skills
`sap-diagram-generate`, `sap-diagram-validate`, `sap-icons-resolve`, an agent,
the Python engine, and the `sap-docs` MCP). It runs in **Claude Code** (CLI / IDE
extension), not in Claude Desktop — see [Why Claude Code, not Desktop](#why-claude-code-not-claude-desktop).

## For end users (per developer)

In Claude Code:

```text
/plugin marketplace add Raistlin82/sap-diagrams-pro
/plugin install sap-diagrams-pro@sap-diagrams-pro
```

That's it — the bundled `sap-docs` MCP (SAP Discovery Center + docs grounding) is
wired automatically from `.mcp.json`.

### Prerequisites (checked automatically by the preflight)

| Need | Why | Install |
|---|---|---|
| **Python 3.10+** | the diagram engine | system Python |
| **draw.io desktop** | PNG preview (optional — generation works without it) | <https://www.drawio.com/> |
| **`sap-docs` MCP** | grounds component names/categories in the SAP Discovery Center | **bundled** (this plugin's `.mcp.json`) |
| **`secondsky/sap-skills`** + **`sap-pce-expert`** | best-practice consultation | `npx skills add secondsky/sap-skills` |

Run the preflight any time to see what's missing:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/preflight.py"
```

### Use it

```text
/sap-diagrams-pro:sap-diagram-generate L1 <describe your architecture>
```

The skill grounds the content (Discovery Center + SAP-domain skills), asks a few
questions, then generates a `.drawio` and verifies it.

## For administrators — roll out to the whole company

Auto-install and pin the plugin on every machine via **managed settings** (your
MDM/IT pushes the file; users cannot disable it). See
[`docs/enterprise/`](docs/enterprise/managed-settings.example.json) for a ready
template and the per-OS file locations.

## Why Claude Code, not Claude Desktop?

This plugin needs the user's machine: it runs Python, (optionally) the local
draw.io app for previews, and reaches MCP/skills. Claude Desktop / claude.ai
*Agent Skills* run in a **sandbox** (no local apps, no local MCP, no other local
skills) and have **no org-wide admin deploy** on claude.ai. The bundled `sap-docs`
MCP happens to be a **remote HTTP** server, so the *grounding* part is portable —
but a true Desktop port would still need the local render step removed and would
lose org-wide enforcement. For company distribution, use the Claude Code
marketplace + managed settings described above.
