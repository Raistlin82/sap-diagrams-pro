<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Changelog

All notable changes to `sap-diagrams-pro` are documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial scaffold: plugin manifest, REUSE-compliant licensing, README and CONTRIBUTING.
- Skill `sap-diagram-generate` with 6 reference docs (Horizon palette, atomic design, levels L0/L1/L2, component groups, line styles + spacing, shape libraries index).
- Skill `sap-diagram-validate` with informational and `--strict` modes.
- Skill `sap-icons-resolve` for SAP service name → draw.io shape resolution.
- Agent `diagram-architect` autonomous orchestrator.
- Python scripts: `bootstrap-cache.sh`, `build-shape-index.py`, `generate-drawio.py`, `validate-drawio.py`.

## [0.1.0] - 2026-04-26

- Initial release (MVP).
