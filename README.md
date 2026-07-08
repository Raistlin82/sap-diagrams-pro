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

The plugin grounds the content **before** it draws:

1. **Preflight** — verifies the reference skills (`secondsky/sap-skills`, `sap-pce-expert`) and the documentation MCP (`mcp-sap-docs`) are installed; prints install hints for any gap (`scripts/preflight.py`).
2. **Ground** — looks up every component in the SAP Discovery Center (via the MCP) for its canonical name, category (BTP-service vs SaaS-product) and deprecation status.
3. **Consult** — invokes the SAP-domain skills for best-practice completeness (missing logging, Cloud Connector, identity trust, …).
4. **Interview** — asks a focused set of questions (level, runtime, identity, integration style, backends) derived from what the docs/skills surfaced, then confirms the inventory.
5. **Generate** — builds the **IR v2** JSON (gated by `scripts/validate-ir.py`) and runs `scripts/generate-drawio.py`. The deterministic pipeline is **skeleton layout** (slot layout + flow ordering, `_skeleton_layout.py`) → **channel router** (obstacle-aware edges + collision-free pill/label slots, `_channel_router.py`) → **style-contract molecules** (`_molecules.py`, driven by `assets/style-contract.json`), laying out consumers→BTP→systems horizontally with auto-sized containers (no graphviz dependency).
6. **Verify & save** — `validate-drawio.py` (palette/XML) + `check-composition.py` (geometric gate: piercing/overlaps/containment/channel discipline) with an optional **visual-rubric loop** (`apply-rubric-patches.py` consuming `references/visual-rubric.md`), then `render-preview.py` (PNG — draw.io when present, else the pure-Python renderer). Saves to `./diagrams/<title>-<level>.drawio`.

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
├── scripts/                         # deterministic engine (single source of truth)
│   ├── generate-drawio.py           # IR v2 → .drawio XML (pipeline entry point)
│   ├── validate-ir.py               # IR v2 pre-render gate
│   ├── validate-drawio.py           # .drawio → palette/XML compliance report
│   ├── check-composition.py         # geometric gate (uses _geom_checks.py)
│   ├── apply-rubric-patches.py      # visual-rubric patch-op consumer
│   ├── render-preview.py            # PNG preview (draw.io or pure renderer)
│   ├── _skeleton_layout.py          # slot layout + flow ordering
│   ├── _channel_router.py           # obstacle-aware edge/pill/label router
│   ├── _molecules.py                # style-contract-driven molecule emission
│   ├── _geom_checks.py              # geometry kernel (router + gate)
│   ├── _pure_render.py              # sandbox PNG renderer (no draw.io app)
│   ├── _drawio_io.py                # .drawio page (de)serialisation
│   ├── build-shape-index.py         # builds shape-index.json (offline)
│   ├── build-style-contract.py      # builds style-contract.json (offline)
│   ├── build-icon-atlas.py          # builds icon-atlas/ PNGs (offline)
│   └── bootstrap-cache.sh           # clones SAP repos to ~/.cache/
└── assets/
    ├── shape-index.json             # parsed SAP shape catalog (+ .schema.json)
    ├── style-contract.json          # canonical molecule style contract
    ├── canonical-pills.json         # canonical edge-pill catalog
    ├── brand-pack/                  # public brand chips (brand-pack.local/ gitignored)
    ├── icon-atlas/                  # pre-rasterized icon PNGs for the pure renderer
    └── fonts/                       # bundled Arimo (SIL OFL-1.1)
```

## Claude Desktop / claude.ai bundle

A self-contained **Agent Skill** port of the engine lives in
`packaging/claude-desktop-skill/`. Run `bash packaging/claude-desktop-skill/build.sh`
to assemble `dist/claude-desktop-skill/sap-diagram-generate.zip` — the full
perfect-diagrams engine (entry points + private modules), the style contract,
public brand pack, icon atlas, bundled fonts, shape index, canonical pills, and
the `visual-rubric.md` reference, all bundled so it runs entirely inside the
code-execution sandbox (draw.io **and** PNG preview, via the pure renderer). The
gitignored `assets/brand-pack.local/` is never included. See
[`packaging/claude-desktop-skill/README.md`](packaging/claude-desktop-skill/README.md).

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
