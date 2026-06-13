<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Enterprise rollout — `sap-diagrams-pro`

Push the plugin to every developer's Claude Code automatically, with no manual
steps, using **managed settings** (the highest-precedence config — users cannot
override or disable it).

## 1. Deploy the managed-settings file

Copy [`managed-settings.example.json`](managed-settings.example.json) to the
Claude Code **managed settings** path on each machine (via your MDM — Jamf/Kandji,
Intune/GPO, Ansible, …):

| OS | Path |
|---|---|
| macOS | `/Library/Application Support/ClaudeCode/managed-settings.json` |
| Linux / WSL | `/etc/claude-code/managed-settings.json` |
| Windows | `C:\Program Files\ClaudeCode\managed-settings.json` |

```json
{
  "extraKnownMarketplaces": {
    "sap-diagrams-pro": {
      "source": { "source": "github", "repo": "Raistlin82/sap-diagrams-pro" },
      "autoUpdate": true
    }
  },
  "enabledPlugins": {
    "sap-diagrams-pro@sap-diagrams-pro": true
  }
}
```

- `extraKnownMarketplaces` registers the marketplace (the `Raistlin82/sap-diagrams-pro` repo).
- `enabledPlugins` force-installs **and** enables the plugin for everyone.
- `autoUpdate` refreshes it from the repo on startup.

> If you maintain other managed settings, **merge** these keys into the existing
> file (it must remain a single valid JSON object).

## 2. Prerequisites image / onboarding

Ensure each machine has: **Python 3.10+**, optionally **draw.io desktop** (PNG
previews), and the **SAP-domain skills** (`npx skills add secondsky/sap-skills`).
The bundled `sap-docs` MCP needs only outbound HTTPS to
`https://mcp-sap-docs.marianzeis.de/mcp` (allow it through the proxy/firewall).
`scripts/preflight.py` reports any gap per machine.

## 3. Private fork (optional)

To host the marketplace internally, fork the repo to your org (e.g.
`your-org/sap-diagrams-pro`) and change the `repo` value above accordingly. For a
private GitHub repo, developers' `gh`/git must be authenticated to it.

## Notes & limits

- This is a **Claude Code** distribution. **Claude Desktop / claude.ai** has no
  equivalent org-wide plugin/skill enforcement (claude.ai skills are per-user and
  sandboxed). See the repo `INSTALL.md`.
- Some managed-settings keys are version-dependent across Claude Code releases —
  validate on one machine before a fleet-wide push, and check the current docs:
  <https://code.claude.com/docs/en/plugin-marketplaces> and
  <https://code.claude.com/docs/en/configuration>.
