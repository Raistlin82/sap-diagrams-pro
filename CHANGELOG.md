<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Changelog

All notable changes to `sap-diagrams-pro` are documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed — perfect-diagrams engine (deterministic pipeline)

- **New render pipeline** replacing the graphviz `dot` backend, staged end-to-end:
  **IR v2** (`scripts/validate-ir.py` gate) → **skeleton layout**
  (`scripts/_skeleton_layout.py`, slot layout + flow ordering, successor to the
  removed `_zone_layout.py`) → **channel router** (`scripts/_channel_router.py`,
  obstacle-aware edge routing + collision-free pill/label slots) →
  **style-contract molecules** (`scripts/_molecules.py`, driven by
  `assets/style-contract.json`) → **geometric gate + visual-rubric loop**
  (`scripts/check-composition.py` with the `_geom_checks.py` kernel, plus
  `scripts/apply-rubric-patches.py` consuming `references/visual-rubric.md`) →
  **draw.io emission** or **pure-Python render** (`scripts/_pure_render.py` +
  bundled Arimo fonts + `assets/icon-atlas/`, no draw.io app required). SAP's
  horizontal big-picture (consumers LEFT → BTP CENTER → systems RIGHT) is honoured
  with auto-sizing containers; the graphviz dependency is removed.
- **Canonical molecules**: users render frameless (icon + label), the BTP layer
  carries a "SAP BTP" logo chip, and RIGHT-zone systems render as white backend
  boxes (icon-left + title + optional `subtitle`).
- **Fidelity pass**: canonical `arcSize` (32 BTP / 24 area / 16 inner / 50 pill),
  16px title, square service icons (48 L0/L1, 32 L2), Arimo (Helvetica-metric)
  font, firewall stroke 3, 28px step circles. Reference docs + validator aligned
  to ground-truth.
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
- **Verification loop**: `scripts/check-composition.py` (geometric gate — piercing,
  overlaps, containment, channel discipline; `_geom_checks.py` kernel) and
  `scripts/render-preview.py` (PNG preview — draw.io when present, else the bundled
  pure renderer). CI runs both.
- **Desktop / claude.ai bundle refresh** (`packaging/claude-desktop-skill/`): the
  self-contained Agent Skill zip now ships the full perfect-diagrams engine —
  `generate-drawio.py`, `validate-ir.py`, `validate-drawio.py`,
  `check-composition.py`, `apply-rubric-patches.py`, `render-preview.py`, the
  private modules (`_skeleton_layout.py`, `_channel_router.py`, `_molecules.py`,
  `_geom_checks.py`, `_pure_render.py`, `_drawio_io.py`), plus
  `assets/style-contract.json`, `assets/brand-pack/` (public chips only),
  `assets/icon-atlas/`, the bundled Arimo fonts, `shape-index.json`,
  `canonical-pills.json`, and the `visual-rubric.md` reference. The gitignored
  `assets/brand-pack.local/` (trademarks / customer logos) is excluded by a build
  guard. The pure renderer means the skill now produces a PNG preview in the
  code-execution sandbox (no draw.io app needed).
- IR fields: node `subtitle`; group `flow` and `zone`.
- Initial scaffold: plugin manifest, REUSE-compliant licensing, README and CONTRIBUTING.
- Skill `sap-diagram-generate` with 6 reference docs (Horizon palette, atomic design, levels L0/L1/L2, component groups, line styles + spacing, shape libraries index).
- Skill `sap-diagram-validate` with informational and `--strict` modes.
- Skill `sap-icons-resolve` for SAP service name → draw.io shape resolution.
- Agent `diagram-architect` autonomous orchestrator.
- Python scripts: `bootstrap-cache.sh`, `build-shape-index.py`, `generate-drawio.py`, `validate-drawio.py`.

## [0.1.0] - 2026-04-26

- Initial release (MVP).
