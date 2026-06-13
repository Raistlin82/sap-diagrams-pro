<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Changelog

All notable changes to `sap-diagrams-pro` are documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed — diagram quality overhaul (content + visual)

- **Deterministic zone-composition layout** (`scripts/_zone_layout.py`) replaces the
  graphviz `dot` backend. SAP's horizontal big-picture is now honoured: consumers
  LEFT → BTP CENTER → systems RIGHT, with containers that auto-size to their
  contents and respect the `position`/`zone` fields (which `dot` ignored). The
  graphviz dependency is removed.
- **Canonical molecules**: users render frameless (icon + label), the BTP layer
  carries a "SAP BTP" logo chip, and RIGHT-zone systems render as white backend
  boxes (icon-left + title + optional `subtitle`).
- **Fidelity pass**: canonical `arcSize` (32 BTP / 24 area / 16 inner / 50 pill),
  16px title, square service icons (48 L0/L1, 32 L2), Helvetica font, firewall
  stroke 3, 28px step circles. Reference docs + validator aligned to ground-truth.
- **Safer service resolution**: word-level matching (every query word must be a
  word in the canonical name) replaces the risky substring fuzzy match.

### Added

- **Preflight gate** (`scripts/preflight.py`): verifies the reference skills
  (`secondsky/sap-skills`, `sap-pce-expert`) and MCP servers (`mcp-sap-docs`, …)
  are installed before generating, with install hints for any gap.
- **Content grounding**: the generate flow now grounds every component in the SAP
  Discovery Center (via the `mcp-sap-docs` MCP) for canonical names + category
  (BTP-service vs SaaS-product) + deprecation, consults the SAP-domain skills, and
  runs a focused requirements interview — all before rendering.
- **Verification loop**: `scripts/check-composition.py` (zone overlaps, title band,
  legend) and `scripts/render-preview.py` (PNG preview via draw.io). CI runs both.
- IR fields: node `subtitle`; group `flow` and `zone`.
- Initial scaffold: plugin manifest, REUSE-compliant licensing, README and CONTRIBUTING.
- Skill `sap-diagram-generate` with 6 reference docs (Horizon palette, atomic design, levels L0/L1/L2, component groups, line styles + spacing, shape libraries index).
- Skill `sap-diagram-validate` with informational and `--strict` modes.
- Skill `sap-icons-resolve` for SAP service name → draw.io shape resolution.
- Agent `diagram-architect` autonomous orchestrator.
- Python scripts: `bootstrap-cache.sh`, `build-shape-index.py`, `generate-drawio.py`, `validate-drawio.py`.

## [0.1.0] - 2026-04-26

- Initial release (MVP).
