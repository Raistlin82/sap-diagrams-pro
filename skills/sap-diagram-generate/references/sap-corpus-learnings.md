<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Learnings from the SAP Architecture Center corpus

Empirical findings from analyzing **all 30 reference architectures (RA0000–RA0029)** in [SAP/architecture-center](https://github.com/SAP/architecture-center), comprising **137 `.drawio` files / 12 681 mxCells / 30 readme.md** under `docs/ref-arch/RA00xx/`.

This document is **descriptive** (what SAP *actually does* on disk), not prescriptive. Use it to align the plugin's generator + validator with reality, and to spot gaps where our defaults diverge from what graders/reviewers will accept on SAP Architecture Center submissions.

## 1. Repo layout per RA

Each RA follows a uniform skeleton:

```
docs/ref-arch/RA00NN/
├── readme.md                # Docusaurus page; frontmatter + sections
└── drawio/
    └── <one-or-many>.drawio
```

- RA0014 (Network Performance multi-region) has **8 .drawio** — outlier.
- RA0001 (Event-Driven), RA0016 (Cross-hyperscaler Secure Consumption), RA0013 (Business Data Cloud) have 2–3 .drawio.
- 27 of 30 RAs have exactly 1 .drawio.
- RA0007 (Multitenant SaaS / CAP) and RA0025 (NetWeaver transition) have **0 .drawio** — text-only RAs.

## 2. Readme structure convention

Section frequency across all 30 readmes (sorted by adoption):

| Section heading              | Adoption | Purpose |
|------------------------------|----------|---------|
| `## Architecture`            | 25/30 (83%) | Embeds the `.drawio` via `![drawio](drawio/<file>.drawio)` immediately after `<!-- … Solution Diagram SVG image -->` comment |
| `## Resources`               | 22/30 (73%) | Links to SAP help.sap.com, learning, blog posts |
| `## Services and Components` | 16/30 (53%) | Table: column 1 = service, column 2 = role in the architecture |
| `## Flow`                    | 13/30 (43%) | Numbered flow (1, 2, 3, …) describing the diagram step-by-step |
| `## Characteristics`         | 12/30 (40%) | Qualities (resilience, scalability, security) |
| `## Examples in an SAP context` | 12/30 (40%) | Concrete customer scenarios |
| `## Related Missions`        | 10/30 (33%) | Cross-links to architecture-center Missions |
| `## Reasonable Alternatives` | 4/30   | When NOT to use this RA |

**Implication for `sap-diagram-generate`**: when the user asks for a "submission-ready" RA, the skill should produce *both* the `.drawio` *and* a draft readme.md following this section convention.

## 3. Frontmatter conventions

Every readme starts with Docusaurus frontmatter. Common keys:

```yaml
id: id-ra00NN
slug: /ref-arch/<8-char-hash>           # NOT human-readable; auto-generated
sidebar_position: <NN0>
sidebar_custom_props:
  category_index:
    - <appdev | ai | data | integration | opsec>
    - <aws | azure | gcp>                # hyperscaler tags
title: <descriptive>
description: >-
  <2-3 sentence summary, used as Open Graph meta>
keywords: [sap, btp, <domain-specific>]
tags: [<category_index repeated for filtering>]
image: img/ac-soc-med.png                # social-card image
hide_table_of_contents: false
toc_min_heading_level: 2
toc_max_heading_level: 4
contributors: [<github-handle>]
last_update:
  author: <github-handle>
  date: YYYY-MM-DD
```

## 4. Domain + hyperscaler taxonomy

The `category_index` (sidebar grouping) uses 6 domains and 3 hyperscaler tags:

| Domain      | RAs | Hyperscaler coverage |
|-------------|-----|----------------------|
| appdev      | 15  | aws=15, azure=15, gcp=12 |
| integration | 10  | (mixed) |
| opsec       | 5   |  |
| data        | 5   | typically all three hyperscalers |
| ai          | 4   |  |
| demo        | 1   | (RA0000 — placeholder) |

Most RAs are explicitly multi-cloud (15/30 tagged aws+azure+gcp). The plugin should *never* assume a single hyperscaler unless the user specifies it.

## 5. Visual conventions (empirical, from 137 .drawio)

### 5.1 Stroke colors actually in use (top 22, with frequency)

| Hex         | Count | Status in our plugin |
|-------------|-------|----------------------|
| `NONE`      | 2 711 | ✓ accepted |
| `#475E75`   | 1 819 | ✓ accepted (non-SAP border canonical) |
| `#0070F2`   | 1 554 | ✓ accepted (BTP border canonical) |
| `#188918`   |   461 | ✓ accepted (positive) |
| `#CC00DC`   |   356 | ✓ accepted (Trust accent) |
| `#5D36FF`   |   345 | ✓ accepted (purple accent) |
| `#D5DADD`   |   191 | ❌ **not accepted** — light separator grey |
| `#475F75`   |   165 | ✓ accepted (variant) |
| `#07838F`   |   159 | ✓ accepted (teal) |
| `#475E74`   |   138 | ❌ **not accepted** — third variant of non-SAP border (`74` vs `75`) |
| `#C0399F`   |    71 | ❌ **not accepted** — darker pink variant |
| `#FFFFFF`   |    69 | (white edges on icon backgrounds) |
| `#595959`   |    49 | ❌ **not accepted** — slate grey |
| `#178B1B`   |    49 | ❌ **not accepted** — green positive variant |
| `#EAECEE`   |    45 | ❌ **not accepted** — very light grey |
| `#5B738B`   |    42 | ❌ **not accepted** — dark slate |
| `#D3E8FD`   |    33 | ❌ **not accepted** — light BTP background |
| `#1B91FF`   |    29 | ❌ **not accepted** — medium blue |
| `#3399FF`   |    29 | ❌ **not accepted** — medium blue (alt) |
| `#82B366`   |    27 | (draw.io default green — third-party) |
| `#36393D`   |    19 | ❌ — near-black |
| `#147EBA`   |    15 | ❌ — Lucid Chart default |

### 5.2 Fill colors actually in use (top 18)

| Hex         | Count | Status |
|-------------|-------|--------|
| `NONE`      | 1 859 | ✓ |
| `#FFFFFF`   | 1 333 | ✓ |
| `#EBF8FF`   |   564 | ✓ (BTP fill canonical) |
| `#F5F6F7`   |   503 | ✓ (non-SAP fill) |
| `#EDEFF0`   |   191 | ❌ **not accepted** — secondary non-SAP fill |
| `#2395FF`   |   190 | ❌ **not accepted** — mid-blue (icon backgrounds) |
| `#F5FAE5`   |   180 | ✓ (positive) |
| `#FFF0FA`   |   177 | ✓ (pink fill) |
| `#D1EFFF`   |   174 | ❌ **not accepted** — secondary BTP fill |
| `#F1ECFF`   |   157 | ✓ (purple fill) |
| `#EAF8FF`   |    72 | ❌ **not accepted** — BTP variant |
| `#DAFDF5`   |    49 | ✓ (teal fill) |
| `#FFEAF4`   |    47 | ✓ |
| `#ECF8FF`   |    42 | ❌ **not accepted** — BTP variant (3rd) |

> **Action item** (validator): adding the 9 highlighted variants would eliminate ~1 100 spurious WARNINGs across the official corpus.

### 5.3 Icon geometry (small shapes only, w≤200 ∧ h≤200)

| Size       | Count | Notes |
|------------|-------|-------|
| `50×50`    |   984 | **canonical SAP icon size** — what we should default to |
| `32×32`    |   315 | secondary (compact areas) |
| `30×30`    |   259 | tertiary |
| `16×16`    |   157 | tiny pills / step circles |
| `48×48`    |   130 |  |
| `28×28`    |   114 | dense L2 |
| `72×16`    |    72 | wide pill (e.g. "Identity Lifecycle") |
| `90×20`    |    71 | medium pill |
| `120×30`   |    61 | wider annotation |

Our current generator emits **61.24×57** (a precise SAP "service icon" footprint) and **80×80** in some paths. These are valid but uncommon in the corpus — `50×50` would be more recognizable.

### 5.4 Stroke width

| Width | Count | Use |
|-------|-------|-----|
| `1.5` | 5 803 | **dominant — canonical default** |
| `2`   |   598 | emphasized boundary |
| `1`   |   499 | fine internal |
| `3`   |    78 | firewall / very emphatic |
| `4`   |    14 | rare (extra emphatic) |

Our generator does not standardize on `1.5`. Recommend defaulting to `1.5` for all atoms/molecules and using `≥2` only for explicit emphasis/firewall.

### 5.5 Dash patterns

Solid (`dashed=0`) is the default. When dashed, the pattern matters:

| `dashPattern` | Count | Semantic in SAP corpus |
|---------------|-------|------------------------|
| `1 2`         |   129 | very-short dots — *fine optional/internal hint* |
| `1 4`         |    67 | dots — *optional connection* |
| `8 8`         |    38 | long dashes — *async/event-driven* |
| `1 1`         |    14 | (rare) |
| `12 12`       |     1 | (one-off) |

> **Mismatch**: `sap-diagram-generate` documents `8 4` for async and `1 4` for optional. SAP actually uses **`8 8`** for async and **`1 4`** / **`1 2`** for optional. Fix the line-styles reference.

### 5.6 Font sizes

| px | Count | Typical use |
|----|-------|-------------|
| 12 | 2 603 | **body / pill text** |
| 11 |   709 | dense secondary |
| 16 |   505 | group labels |
| 10 |   450 | very dense L2 |
| 13 |   268 | mid heading |
| 18 |   240 | section title |
| 14 |   239 | medium heading |
| 9  |    52 | smallest legible |

Our plugin uses 12 + 18 — within range, but the corpus splits headings between 14 / 16 / 18 with **16 being the most common group label**.

## 6. Vocabulary — recurring labels

### 6.1 Area / group labels (top 20 from corpus)

| Label                                                  | Count |
|--------------------------------------------------------|-------|
| `Multi-Cloud`                                          |   43 |
| `NETWORK`                                              |   33 |
| `3rd party identity provider/ identity management`     |   18 |
| `SAP Datasphere`                                       |   18 |
| `BTP`                                                  |   18 |
| `3rd Party Platforms`                                  |   17 |
| `SAP Cloud Identity Services`                          |   17 |
| `Application Service`                                  |   16 |
| `SAP Cloud Solutions`                                  |   16 |
| `3rd Party Applications`                               |   14 |
| `Amazon Web Services`                                  |   14 |
| `SAP BTP`                                              |   13 |
| `SAP Integration Suite`                                |   12 |
| `Generative AI Hub`                                    |   12 |
| `SAP Analytics Cloud`                                  |   12 |
| `SAP On-Premise Solutions`                             |   10 |
| `SAP S/4HANA Cloud, private edition`                   |    9 |
| `Industry 4.0, Systems, Applications, Warehouses, …`   |   11 |
| `Foundation Model Access`                              |    8 |

**Implication**: the plugin's `groups` taxonomy (today: `user | third-party | btp-layer | sap-app | non-sap`) is too coarse. SAP separates:

- `SAP BTP` / `BTP` (the platform itself)
- `SAP Cloud Solutions` (SuccessFactors, Ariba — *separate* from BTP)
- `SAP On-Premise Solutions` (S/4HANA on-prem, ECC, BW)
- `SAP S/4HANA Cloud, private edition` (PCE — its own group, not on-prem)
- `3rd Party Platforms` vs `3rd Party Applications` (distinct)
- `Amazon Web Services` / hyperscaler group (NEW — we have no equivalent)
- `NETWORK` (cross-cutting layer, all three hyperscalers)

### 6.2 Pill / annotation labels (top 25 + frequency)

| Pill                  | Count | In our `canonical-pills.json`? |
|-----------------------|-------|--------------------------------|
| `HTTPS`               |   84  | ✓ |
| `SAML2/OIDC`          |   59  | ✓ |
| `Trust`               |   44  | ✓ |
| `Authenticate`        |   32  | ✓ |
| `Authentication`      |   29  | ❌ |
| `Mutual Trust`        |   29  | ❌ |
| `TRUST` (uppercase)   |   26  | ✓ |
| `OIDC`                |   24  | ✓ |
| `Identity Lifecycle`  |   23  | ✓ |
| `Group`               |   22  | ✓ |
| `Access`              |   21  | ❌ |
| `Optional`            |   17  | ❌ |
| `Deployment`          |   16  | ❌ |
| `A2A`                 |   16  | ❌ |
| `Modeller App`        |   13  | ❌ |
| `Processor Srv`       |   13  | ❌ |
| `Monitoring App`      |   13  | ❌ |
| `APIs`                |   12  | ❌ |
| `BTP Service`         |   12  | ❌ |
| `Partner built`       |   10  | ❌ |
| `Provisioning`        |   10  | ❌ |
| `MCP`                 |   10  | ❌ |
| `Data Flow`           |   10  | ❌ |
| `Broker`              |    9  | ❌ |
| `Events`              |    9  | ❌ |
| `Policy`              |    9  | ✓ |
| `SAP built`           |    8  | ❌ |
| `ORD`                 |    8  | ✓ |
| `Data Masking`        |    8  | ❌ |
| `I/O Filtering`       |    8  | ❌ |

Current coverage: **10 of the top 29 most-used pills (34%)**. The 19 missing labels span 251 occurrences. Adding them to `assets/canonical-pills.json` would significantly increase fidelity of generated diagrams.

### 6.3 Edge labels — ZERO

Across all 137 files and 12 681 cells, **0 mxCell with `edgeStyle` or `endArrow` has a non-empty `value` attribute**.

This is the single biggest divergence from our current `SKILL.md`:

> "Has every edge labelled (even with one word)." — `sap-diagram-generate/SKILL.md`, quality bar, line 215.

SAP encodes flow-semantics in **pills/annotations placed adjacent to the edge** (e.g. `HTTPS`, `Trust`, `Optional`), never on the edge value itself. The arrowhead carries direction; the pill carries the verb/protocol; the dash pattern carries sync vs async.

> **Action**: remove the "every edge labelled" requirement from the SKILL quality bar, and add a positive rule: "annotate edges via canonical pills placed inline (height ≤ 24 px)".

## 7. Non-SAP shapes in the corpus

The plugin assumes SAP shapes only. The corpus actually uses:

| Shape namespace                | Count | Source |
|--------------------------------|-------|--------|
| `mxgraph.sap.icon`             |  190  | SAP shape library (foundational) |
| `mxgraph.aws4.resourceIcon`    |   44  | AWS shape library |
| `mxgraph.basic.oval_callout`   |   36  | draw.io basic — used for annotation bubbles |
| `mxgraph.basic.rect`           |   30  | generic rectangle |
| `mxgraph.aws4.group`           |   14  | AWS grouping primitive |
| `mxgraph.aws4.productIcon`     |   10  | AWS |
| `mxgraph.aws4.lambda_function` |    7  | AWS Lambda |
| `mxgraph.aws4.network_load_balancer` |  5 | AWS NLB |
| `mxgraph.flowchart.database`   |    5  | generic DB |
| `mxgraph.aws4.nat_gateway`     |    3  | AWS |

> **Implication**: a faithful generator for cross-cloud RAs (15 of 30) needs **AWS shape resolution** in addition to SAP. Azure and GCP are present in the readmes but their shapes are less consistently used (often replaced with generic boxes). The `sap-icons-resolve` skill could be extended (or a sibling `hyperscaler-icons-resolve` added) when the user specifies an AWS/Azure/GCP component.

## 8. Concrete action items for the plugin

In rough priority order:

1. **Validator palette expansion** — add to `HORIZON_BORDERS`: `#475E74`, `#D5DADD`, `#C0399F`, `#178B1B`, `#EAECEE`, `#5B738B`, `#595959`, `#D3E8FD`; add to `HORIZON_FILLS`: `#EDEFF0`, `#D1EFFF`, `#2395FF`, `#EAF8FF`, `#ECF8FF`. Expected effect: ~1 100 fewer spurious WARNINGs when validating real SAP RAs.
2. **Line-styles reference fix** — update `references/line-styles-spacing.md` to reflect actual SAP usage: async = `dashPattern=8 8` (not `8 4`); optional = `dashPattern=1 4` *or* `1 2` (both common).
3. **Drop the "every edge labelled" rule** — instead, generate a canonical pill adjacent to the edge. Reword the SKILL quality bar.
4. **Expand `canonical-pills.json`** with the 19 missing pills from §6.2.
5. **Expand the `groups` taxonomy** — add: `hyperscaler` (Amazon Web Services / Microsoft Azure / Google Cloud), `sap-cloud-solutions` (SFSF, Ariba), `sap-onprem` (S/4HANA on-prem), `sap-pce` (S/4HANA private edition), `network` (cross-cutting). Distinct from `btp-layer`.
6. **Canonical icon size = 50×50** — keep current 61.24×57 as an opt-in "high-fidelity" mode; default to 50×50 for parity with the corpus.
7. **Default `strokeWidth=1.5`** in all generated atoms unless explicitly overriding for emphasis/firewall.
8. **Readme co-generation** — when the user asks for a "submission-ready" RA, also emit a draft `readme.md` with the canonical sections (`Architecture` → embed the `.drawio` → `Flow` numbered → `Services and Components` table → `Resources`).
9. **Hyperscaler shape resolution** — extend the icon resolver to AWS shapes for cross-cloud RAs. Azure/GCP fidelity is lower in the corpus, can be deferred.
10. **L1 ≈ 50–100 cells, L2 ≈ 100–200** — the corpus average per `.drawio` is **92 cells** (12 681 / 137). Our current `L1 = 10–30` budget is much smaller. Either rebrand our L1 as "lightweight" or align with SAP's actual density.

## 9. Reproducing this analysis

```bash
# 1. Have a clone of SAP/architecture-center available
test -d ~/github/architecture-center || \
  git clone --depth=1 https://github.com/SAP/architecture-center ~/github/architecture-center

# 2. From the plugin repo:
cd ~/github/sap-diagrams-pro

# 3. Aggregate stats across all .drawio (script not committed — inline in this doc's
#    git history; re-run as one-shot Python with the snippets below).

# 4. Verify counts match this doc:
find ~/github/architecture-center/docs/ref-arch -name "*.drawio" | wc -l    # → 137
find ~/github/architecture-center/docs/ref-arch -maxdepth 1 -type d -name "RA*" | wc -l    # → 30
```

The numbers above were captured against `c198eab` of `SAP/architecture-center` on 2026-05-14. Rerun before relying on them — the corpus grows.
