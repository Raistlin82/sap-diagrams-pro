# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Claude Code plugin (`.claude-plugin/plugin.json`) that generates SAP-compliant draw.io architecture diagrams from natural-language descriptions, following the official [SAP BTP Solution Diagram Guideline](https://sap.github.io/btp-solution-diagrams/).

Three skills + one orchestrator agent drive a deterministic Python engine:

- `skills/sap-diagram-generate/` — primary slash command; interactive 10-step flow that parses input, consults SAP-domain skills, confirms with user, builds a JSON intermediate, then calls the engine.
- `skills/sap-diagram-validate/` — read-only compliance check against the guideline.
- `skills/sap-icons-resolve/` — service name → draw.io shape lookup (mostly internal).
- `agents/diagram-architect.md` — autonomous orchestrator for multi-level (L0+L1+L2) or reverse-engineering tasks.

## Commands

```bash
# One-time: clone SAP source-of-truth repos to ~/.cache/sap-diagrams-pro/
bash scripts/bootstrap-cache.sh                # idempotent
bash scripts/bootstrap-cache.sh --refresh      # force pull

# Rebuild the shape catalog from the cached SAP libraries
python3 scripts/build-shape-index.py           # writes assets/shape-index.json

# Generate a diagram from a JSON intermediate
python3 scripts/generate-drawio.py input.json --out diagram.drawio
python3 scripts/generate-drawio.py input.json --out diagram.drawio --layout dot     # graphviz (preferred)
python3 scripts/generate-drawio.py input.json --out diagram.drawio --layout greedy  # force fallback

# Validate a .drawio against the SAP guideline
python3 scripts/validate-drawio.py diagram.drawio
python3 scripts/validate-drawio.py diagram.drawio --strict   # exit 1 on CRITICAL
python3 scripts/validate-drawio.py diagram.drawio --json     # machine-readable

# CI checks (run locally before pushing)
python3 scripts/_ci_check_index.py             # shape-index integrity
python3 scripts/_ci_check_skills.py            # SKILL.md frontmatter
reuse lint                                     # REUSE / SPDX compliance

# End-to-end smoke test (mirrors .github/workflows/engine-smoke-test.yml)
for level in L0 L1 L2; do
  python3 scripts/generate-drawio.py "skills/sap-diagram-generate/examples/${level}-example.json" \
    --layout dot --out "out/${level}.drawio"
  python3 scripts/validate-drawio.py "out/${level}.drawio" --strict
done
```

Prerequisites: Python 3.10+ (CI uses 3.12), `git`, and `graphviz` (provides `dot`) for the primary layout backend.

## Architecture

### Data flow

```
user prompt → SKILL.md (sap-diagram-generate)
            → JSON intermediate (groups + nodes + edges + metadata)
            → scripts/generate-drawio.py
                ├─ layout=dot      (graphviz, primary)
                └─ layout=greedy   (built-in 3×3 grid fallback)
            → .drawio XML
            → scripts/validate-drawio.py → severity report
```

The **JSON intermediate** (see `skills/sap-diagram-generate/examples/L{0,1,2}-example.json`) is the contract between the markdown skills and the Python engine. Schema is documented in `sap-diagram-generate/SKILL.md` and validated against `assets/shape-index.schema.json`.

### License-clean asset boundary

SAP shape libraries and reference architectures are **not** bundled. `bootstrap-cache.sh` clones `SAP/btp-solution-diagrams` and `SAP/architecture-center` to `~/.cache/sap-diagrams-pro/` (override via `SAP_DIAGRAMS_CACHE`). `build-shape-index.py` parses the cached XML and writes `assets/shape-index.json` — that derived index *is* committed.

If editing the engine: never reach for SAP assets outside `~/.cache/sap-diagrams-pro/`. The plugin must stay installable under 200 KB.

### Canonical pills catalog

`assets/canonical-pills.json` is a hand-curated catalog of 42 SAP-canonical pill labels (e.g. `SAML2/OIDC`, `Group`, `OIDC`, `ORD`) harvested from the 138 official SAP `.drawio` files. Each maps a label to its canonical color family + exact stroke/fill hex. `generate-drawio.py` normalizes labels (strip whitespace, lowercase) to look these up so authors don't have to hardcode hex values.

### Determinism is a hard requirement

`generate-drawio.py` is designed to produce **byte-identical XML** for byte-identical JSON input — IDs are derived from `id` fields via a short hash prefix. The smoke test workflow depends on this. If you touch the engine, preserve this property (no `datetime.now()` without a deterministic alternative, no unordered dict iteration in output paths, no random IDs).

### Horizon palette tolerance

`validate-drawio.py` accepts known SAP minor variants of palette colors (e.g. `#475E75` *and* `#475F75`, three purples `#5D36FF`/`#470BED`/`#4628EC`). SAP's own libraries are internally inconsistent; matching them strictly produces spurious WARNINGs. When adding rules, prefer to add the SAP variant to the accepted set over flagging the user.

### L3 is a non-standard plugin extension

L0/L1/L2 follow the official SAP guideline. **L3 is a plugin-specific deployment view** (Kubernetes, ingress, network policies) and is explicitly non-canonical for SAP Architecture Center submissions. The generator emits a leading comment in L3 outputs flagging this. Do not promote L3 patterns into L1/L2 reference docs.

## Conventions specific to this repo

- **REUSE / SPDX**: every script and markdown file has `SPDX-FileCopyrightText` and `SPDX-License-Identifier`. The CI workflow greps `head -3` for these on `scripts/*.{py,sh}` — adding a script without them fails CI.
- **SKILL.md frontmatter**: required keys are `name`, `description`, `version`. `_ci_check_skills.py` enforces this. Keep SKILL.md lean (1500–2000 words); push detail to `references/`.
- **Shape-index integrity**: `meta.totalServices` in `assets/shape-index.json` must equal `len(services)`. `_ci_check_index.py` enforces this after every `build-shape-index.py` run.
- **No auto-fix in the validator**: by deliberate design. If a diagram has WARNINGs, the agent regenerates from JSON rather than mutating the file.
- **Settings location**: `.claude/sap-diagrams-pro.local.md` (project) or `~/.claude/sap-diagrams-pro.local.md` (user). Both are gitignored. Keys: `btp_repo_path`, `arch_center_repo_path`, `default_level`, `output_dir`, `validation_strictness`, `auto_consult_skills`.
- **Generated diagrams** live in `./diagrams/` and are gitignored. Committed example outputs live under `demo/` (nova, interactive) as canonical fixtures — don't repurpose them as scratch.
