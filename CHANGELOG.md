<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Changelog

All notable changes to `sap-diagrams-pro` are documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed — diagram polish (0.5.1)

- **Governance ribbon**: a top-level `governance` group now renders as a full-width
  band across the top (SAP spec) instead of a content-sized box that misread as a
  second "SAP BTP" account floating above the diagram.
- **Cross-network pills**: `_channel_router` keeps edge pills/labels off the NETWORK
  separator bar + its "NETWORK" caption, so a cross-network edge (e.g. a `zero-copy`
  pill) no longer lands on the seam — resolved in the router, no rubric-loop nudge.
- **SAP BTP chip**: the governance/subaccount chip shows the SAP logo + "BTP" beside
  it (was overlapping into "SSAP BTP").


### Added — hybrid scaffold path (0.5.0)

- **Template corpus**: 156 real SAP reference `.drawio` diagrams (145 from
  `SAP/architecture-center` + 11 `SAP/btp-solution-diagrams` editable examples,
  both Apache-2.0) in `assets/templates/`, indexed by `scripts/build-template-index.py`
  → `assets/template-index.json` (level, family, service/label tokens, scenario aliases).
- **Scaffold-or-generate**: `scripts/select-template.py` ranks templates against a
  request (threshold 14.0); `scripts/scaffold-diagram.py` copies the closest match (or
  exits 3 → procedural fallback); `scripts/relabel.py` makes surgical label edits.
  Both SKILL.md files gain a "scaffold-or-generate" step: scaffold a real SAP diagram
  when a strong template matches (higher fidelity), else the procedural IR engine —
  full automation as the always-available fallback. Desktop degrades to the procedural
  path (templates not bundled — Skills file-cap).
- **SAP-likeness scorer** (`scripts/score-diagram.py`): 0–100 fingerprint — reference-free
  `--sap-like`, `--compare ref cand`, `--corpus <dir> --min-score`. Objective quality gate
  (nova-L1 95.6, gold replica 100). Wired into the shared gate (`--min-score 82`).
- **Validator hardening** (`scripts/validate-drawio.py`): bent-edge (orthogonal edge
  straight only if endpoint centers align on an axis — CRITICAL), missing
  `labelBackgroundColor=default` (WARNING), `arcSize` without `absoluteArcSize=1`
  (WARNING, capsule-pill exempt), plus a `--fix` autofix for the mechanical ones.
- **Semantic pill coloring**: edge pills colored by flow family (trust=pink,
  authentication=green, authorization=purple).

### Added — icon coverage + branding (0.4.0)

- **SAP icons re-harvested from the official `svg/` library** (129 clean service
  icons + newly-covered services: the Integration Suite capability family,
  Document Grounding, Data Enrichment, Process Visibility, UI Theme Designer,
  Dynamic Forms, Data Quality Services, and more). `scripts/build-shape-index.py`
  now reads the authoritative per-service SVGs (clean techIds) and keeps the 8
  `draw.io/` utility libraries for pills/connectors/brand. Backward-compat aliases
  are preserved so existing IRs still resolve.
- **Curated generic cloud-icon set** (`assets/generic-icons.json`, 213 icons from
  mingrammer/diagrams, MIT — see `assets/GENERIC-ICONS-NOTICE.md`): non-SAP
  components (PostgreSQL, Kafka, Redis, Kubernetes, AWS/Azure/GCP compute+storage,
  on-prem) now resolve to real vendor glyphs. shape-index = 342 services; atlas =
  585 entries.
- **Curated aliases** for the identity family (IAS→Identity Authentication, IPS,
  SCI) and Integration Suite capabilities (APIM, CPI, TPM, AEM, Open Connectors,
  Graph…); fixed a slug collision that mis-routed CPI onto Cloud Integration
  Automation.
- **Branding is opt-in**: the generate skill now asks whether to add a partner
  watermark / customer logo (default none — never assumes a company) and, if so,
  has the user supply the image; never defaults to a partner.

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
