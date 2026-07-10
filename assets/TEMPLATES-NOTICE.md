# SAP Reference Template Corpus — Attribution & Licensing

The files under `assets/templates/` are **real, editable SAP reference
architecture `.drawio` diagrams** redistributed verbatim from SAP open-source
repositories. They power the engine's future *scaffold* path: instead of
computing a layout, the selector copies the closest real SAP diagram (ranked via
`assets/template-index.json`) and applies surgical edits — the same
higher-fidelity approach used by `marianfoo/btp-drawio-skill`.

All committed templates are licensed **Apache-2.0** and are freely
redistributable. Copyright is retained by their original authors.

## Sources

Both source repositories declare `SPDX-License-Identifier: Apache-2.0` for all
paths (`path = "**"`) in their `REUSE.toml`, and ship the Apache-2.0 text under
`LICENSES/Apache-2.0.txt`. The `.drawio` assets are therefore clearly
redistributable and are **committed directly** into this repo (no cache-only
indexing was needed).

| Source repo | URL | License | Commit (pinned) | Path harvested | Committed templates |
|---|---|---|---|---|---|
| `SAP/architecture-center` | https://github.com/SAP/architecture-center | Apache-2.0 | `4635f734f2f82c6497bc53d5cf972b5cbfec5e52` | `docs/ref-arch/RAxxxx/**/drawio/*.drawio` | 145 |
| `SAP/btp-solution-diagrams` | https://github.com/SAP/btp-solution-diagrams | Apache-2.0 | `c274fdba123fd7162f82ffd13bb0e2949794b9cc` | `assets/editable-diagram-examples/*.drawio` | 11 |

Total committed: **156** `.drawio` templates (~26 MB).

> Copyright notice from both repos' `REUSE.toml`:
> `SPDX-FileCopyrightText = "2024 SAP SE or an SAP affiliate company and <repo> contributors"`

### Attribution (SPDX)

```
SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and architecture-center contributors
SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and btp-solution-diagrams contributors
SPDX-License-Identifier: Apache-2.0
```

The full Apache-2.0 license text is available at
<https://www.apache.org/licenses/LICENSE-2.0>.

## Harvest & naming

- De-duplicated by content hash (4 byte-identical duplicates within
  `architecture-center` collapsed to one copy each).
- `architecture-center` files are prefixed with their reference-architecture id
  (e.g. `RA0009_...`) to preserve provenance and avoid basename collisions
  across RA folders. `btp-solution-diagrams` filenames are unique and kept
  verbatim.
- Per-file provenance (`source`, original `sourcePath`) and the source commit
  SHAs are recorded in `assets/template-index.json`.

## Regenerating the index

```bash
python3 scripts/build-template-index.py \
  --architecture-center /tmp/architecture-center \
  --btp-solution-diagrams ~/tools/btp-solution-diagrams
```

The build script (`scripts/build-template-index.py`) is stdlib-only and
deterministic: entries are sorted by id and `meta.generatedAt` is derived from
the source repos' commit dates (never wall-clock time), so re-running on the
same inputs produces byte-identical output.

## No cache-only sources

Because both sources are Apache-2.0, nothing was cache-indexed under a `.local`
path. Should a future, non-permissively-licensed source be considered, follow
the repo's existing `brand-pack.local` pattern: do **not** commit its `.drawio`;
index it from a local cache path only and record the decision here.
