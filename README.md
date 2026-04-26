<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# sap-diagrams-pro

> Claude Code plugin that generates **SAP-compliant draw.io architecture diagrams** using the official SAP BTP Solution Diagram Guideline (atomic design + Horizon palette) and SAP Architecture Center reference architectures.

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![REUSE status](https://img.shields.io/badge/REUSE-compliant-brightgreen.svg)](https://reuse.software/)

## Why this plugin

Producing a SAP-compliant solution diagram today requires:

1. Manually loading 7 different draw.io shape libraries from the SAP GitHub repository.
2. Memorising the SAP Horizon color palette (`#0070F2` BTP border, `#EBF8FF` BTP fill, `#475E75` non-SAP border, etc.).
3. Replicating atomic design conventions (atoms / molecules / organisms) by hand.
4. Choosing the right detail level (L0 executive / L1 architect / L2 technical).
5. Following spacing, line-style, numbering and naming conventions documented across the SAP guideline.

This plugin automates 1-5. You describe the architecture in natural language and choose a level — the plugin produces a `.drawio` file that opens in [draw.io](https://drawio.com) (or [diagrams.net](https://diagrams.net)) and is ready to embed in proposals, blogs, missions or [SAP Architecture Center](https://architecture.learning.sap.com) submissions.

## Features

- **Three detail levels** following the official SAP guideline:
  - `L0` — executive overview (5-10 boxes, no technical detail)
  - `L1` — architect mid-detail (15-30 elements, main flows)
  - `L2` — technical implementation (30+ elements, named services)
- **Horizon palette enforcement** — primary, semantic and accent colors validated against the official guideline.
- **Atomic design system** — atoms (colors, lines, icons, text), molecules (styled shapes), organisms (component groups: User, 3rd-party, BTP Layer).
- **Generative-first strategy** — agent autonomously selects the right shape library and reference architecture template from your description.
- **Validation skill** — checks an existing `.drawio` against the SAP guideline and reports CRITICAL / WARNING / INFO issues.
- **Auto-download cache** — at first invocation the plugin clones the official SAP repos to `~/.cache/sap-diagrams-pro/`. Plugin install stays under 200KB; updates pull straight from upstream.

## Installation

```bash
# Install via the skills CLI (preferred)
npx skills add Raistlin82/sap-diagrams-pro

# Or install locally for development
git clone https://github.com/Raistlin82/sap-diagrams-pro ~/github/sap-diagrams-pro
ln -s ~/github/sap-diagrams-pro ~/.claude/plugins/sap-diagrams-pro
```

### Prerequisites

- **Claude Code** (any recent version)
- **Python 3.10+** (for the diagram generation engine)
- **git** (for the auto-download cache bootstrap)
- **draw.io** desktop app or [drawio.com](https://drawio.com) web (to open generated `.drawio` files)

## Usage

### Generate a diagram (primary workflow)

```
/sap-diagrams-pro:sap-diagram-generate L1 NOVA Invoice Suite — CAP backend on Kyma, S/4HANA proxies, DOX inbound, Integration Suite for FatturaPA
```

The plugin:

1. Parses your description and identifies SAP services / non-SAP components.
2. Picks the closest reference architecture from `~/.cache/sap-diagrams-pro/architecture-center/` as the layout starting point.
3. Resolves each named service to the correct draw.io icon from the SAP shape library.
4. Generates a JSON intermediate representation (nodes + edges + level + layout hints).
5. Runs `scripts/generate-drawio.py` to produce a deterministic, valid `.drawio` XML file.
6. Saves to `./diagrams/<title>.drawio` (configurable).

### Validate an existing diagram

```
/sap-diagrams-pro:sap-diagram-validate path/to/diagram.drawio
```

Default mode is informational — produces a report. Add `--strict` to fail on CRITICAL issues (useful for CI pipelines).

### Resolve a service icon (helper)

The `sap-icons-resolve` skill is auto-invoked by the generator when a service name is ambiguous. You can also invoke it manually to look up a single icon's draw.io style snippet.

## Configuration

Settings live in `.claude/sap-diagrams-pro.local.md` (project-local) or `~/.claude/sap-diagrams-pro.local.md` (user-global). Both are gitignored by default.

```markdown
# sap-diagrams-pro user settings

btp_repo_path: ~/.cache/sap-diagrams-pro/btp-solution-diagrams
arch_center_repo_path: ~/.cache/sap-diagrams-pro/architecture-center
default_level: L1
output_dir: ./diagrams
validation_strictness: informational  # informational | strict
```

If you already have the SAP repos cloned locally (e.g. for contributing upstream), point the paths to your local clone to skip the auto-download.

## Architecture

```
sap-diagrams-pro/
├── .claude-plugin/
│   └── plugin.json                  # Manifest
├── skills/
│   ├── sap-diagram-generate/        # Primary skill (slash command)
│   │   ├── SKILL.md
│   │   ├── references/              # 6 ref docs (palette, atomic, levels, ...)
│   │   ├── examples/                # JSON intermediate examples per level
│   │   └── templates/               # Base .drawio templates per level
│   ├── sap-diagram-validate/        # Validation skill (slash command)
│   └── sap-icons-resolve/           # Helper skill (auto-invoked)
├── agents/
│   └── diagram-architect.md         # Autonomous orchestrator for complex diagrams
├── scripts/
│   ├── bootstrap-cache.sh           # Clones SAP repos to ~/.cache/
│   ├── build-shape-index.py         # Parses shape XML → shape-index.json
│   ├── generate-drawio.py           # JSON intermediate → .drawio XML
│   └── validate-drawio.py           # .drawio → compliance report
└── assets/
    └── shape-index.schema.json      # JSON schema for the shape index
```

## Compliance with SAP guideline

This plugin enforces the rules documented at <https://sap.github.io/btp-solution-diagrams/> including:

- [Atomic Design System](https://sap.github.io/btp-solution-diagrams/docs/btp_guideline/atomic) — atoms / molecules / organisms.
- [Foundation colors](https://sap.github.io/btp-solution-diagrams/docs/btp_guideline/foundation) — Horizon primary, semantic, accent palettes.
- [Component Groups](https://sap.github.io/btp-solution-diagrams/docs/btp_guideline/comp_groups) — User, 3rd-party, BTP Layer.
- [Diagram Components](https://sap.github.io/btp-solution-diagrams/docs/btp_guideline/diagr_comp) — areas, icons, lines, numbers, product names, text.

## Trademarks

"SAP" and "SAP Business Technology Platform" are trademarks or registered trademarks of SAP SE in Germany and other countries. This project is independent and not affiliated with, endorsed by, or sponsored by SAP SE.

## License

Apache 2.0 — see [LICENSE](LICENSE) and [REUSE.toml](REUSE.toml) for full attribution.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports and feature requests via [GitHub Issues](https://github.com/Raistlin82/sap-diagrams-pro/issues).
