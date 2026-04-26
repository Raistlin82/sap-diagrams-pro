<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Contributing to sap-diagrams-pro

Thanks for considering a contribution. This plugin is open-source under Apache 2.0 and aims to stay aligned with the official [SAP BTP Solution Diagram Guideline](https://sap.github.io/btp-solution-diagrams/).

## Ways to contribute

- **Report a guideline drift**: if SAP updates the official guideline and the plugin's references / templates fall out of sync, open an issue with the upstream commit reference.
- **Add a reference architecture template**: bring a new `.drawio` template aligned with one of the [SAP Architecture Center RAs](https://architecture.learning.sap.com).
- **Improve the layout engine**: the Python `generate-drawio.py` greedy positioning algorithm has room for improvement (especially L2 with 50+ shapes).
- **Add validation rules**: extend `validate-drawio.py` to catch new guideline violations.
- **Translate**: the SKILL.md descriptions are English-only today.

## Development setup

```bash
git clone https://github.com/Raistlin82/sap-diagrams-pro
cd sap-diagrams-pro

# Bootstrap the SAP repo cache (one-time)
bash scripts/bootstrap-cache.sh

# Build the shape index from the cache
python3 scripts/build-shape-index.py

# Run the validator on a sample diagram
python3 scripts/validate-drawio.py examples/sample-L1.drawio
```

## Pull request guidelines

- Run `reuse lint` before pushing to keep REUSE compliance.
- Keep `SKILL.md` files lean (1500-2000 words). Move detail to `references/`.
- Add an entry to `CHANGELOG.md` under `## [Unreleased]`.
- For changes touching the layout engine, include a before/after `.drawio` example in `examples/`.
- For new validation rules, document the corresponding section of the SAP guideline that justifies them.

## Code of conduct

This project follows the [Contributor Covenant Code of Conduct](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). By participating, you agree to abide by its terms.

## Reporting security issues

Do not open public issues for security-related findings. Email <gabriele@key2.it> with the details and we'll triage privately.
