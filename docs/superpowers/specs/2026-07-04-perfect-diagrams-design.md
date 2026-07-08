<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Design — sap-diagrams-pro: perfect diagrams (skeleton grammar + channel routing + visual gate)

Status: **approved** (2026-07-04). Author: pairing session.
Builds on: [2026-06-13 quality design](2026-06-13-sap-diagrams-pro-quality-design.md).

## Problem

The June overhaul fixed macro-composition (zones, molecules sizing, safe icon
resolution) but output is still "barely sufficient", not hand-quality. The user
requires diagrams that are **perfect** — visually and in content. Content
grounding (preflight → Discovery Center MCP → domain skills → interview) works;
the residual gap is **visual**.

## Evidence

Regenerating `demo/nova/nova-L1.json` with the current engine and rendering it
next to the gold standard (`SAP_Task_Center_L1.drawio`) shows three defect
families:

1. **Macro-composition gaps.** No inner *Subaccount* frame (services float in a
   flat grid inside the blue box); services ordered by IR declaration, not by
   flow; identity services boxed *outside* the BTP perimeter; no NETWORK
   separator; external channels stacked in a tall top-center column.
2. **Edge chaos (dominant).** The zone engine places boxes but returns
   `edges: {}` — routing is left to draw.io defaults. Result: ≥8
   parallel/crossing lines between Integration Suite / CAP / Fiori / DOX /
   AI Core; edge labels collide with each other and with node captions; edges
   pierce container borders; step circle ① overlaps a group title, ③ overlaps
   the *Interface* pill; pills sit far from their edges.
3. **Blind pipeline.** `check-composition.py` reports **0 fail / 0 warn** on
   that output. The geometric checks (group overlap, title band, legend) cannot
   see any of the above, and nothing ever *looks* at the render before delivery.

## Target — "perfect", operationally

Perfect = **official SAP BTP Solution Diagram guideline + Gabriele's hand-made
conventions**, defined by a reference corpus:

- **Official**: the 11 editable examples, 31 shape libraries and
  `essentials.xml` (16 molecules) in `~/tools/btp-solution-diagrams`.
  Gold: `SAP_Task_Center_L1`.
- **Gabriele exemplars (archetype A — solution diagrams)**:
  `SAP_BTP_Architecture_20240909_Lutech_SSAM.drawio`,
  `SAP_BTP_Architecture_20240906_Lutech.drawio` (OneDrive `30. Alia/BTP
  Architecture/`), `brandart_arch_v01.drawio` (`43. Brandart/`).
  Conventions they add on top of the official guideline:
  - nested **Subaccount frames** (e.g. *Extension Test ⊃ Extension Production*)
    plus a separate *Governance* strip, each with the SAP BTP logo chip;
  - **product molecule**: light-blue box, icon + bold title, containing white
    **capability chips** (icon + label, e.g. BPA → Decision/Actions/Process…);
  - **custom-app molecule**: like product, for bespoke applications, with
    dev/runtime chips (BAS, Build Apps, HANA Cloud) and a runtime badge;
  - **badges**: hyperscaler logos (AWS/Azure) top-right of subaccounts,
    CLOUD FOUNDRY bottom-left;
  - **cloud tiers** on the RIGHT: *Public Cloud / Private Cloud / Any-Premise*
    boxes holding brand-name chips (Ariba, PCE→S/4HANA, HEC, ERP, 3rd party);
  - **NETWORK separator** (double vertical bar) before the tiers;
  - **personas** with device icons on the LEFT (occasionally right), frameless;
  - **protocol pills on edges**: SAML2, OIDC, SCIM, HTTPS, REST, SOAP, CIG;
  - **semantic flow colours**: default `#475E75`, identity green `#188918`,
    provisioning purple `#470BED`, master-data magenta `#CC00DC`, BTP blue
    `#0070F2`; dashed = async/optional;
  - **branding block**: customer logo + blue title top; partner watermark
    (Lutech) semi-transparent centre; Helvetica 12 (18 for section titles).
- **Archetype B (landscape/migration, SNAM)** — system boxes with client
  chips, CR transport routes, responsibility-coloured steps, multi-panel
  stories — is **out of scope now**; the IR must not preclude it later.

## Decisions (brainstorm 2026-07-04)

1. Perfection target = corpus above (option B: gold + user style).
2. Scope: archetype **A first, B-ready** IR.
3. **Equal quality everywhere**: a pure-Python PNG renderer is in scope so the
   visual gate runs on Claude Code, claude.ai/Desktop and CI alike. draw.io
   stays the delivery renderer when installed.
4. Content pipeline unchanged.
5. Approach **C — layered**: grammar (structure) + channels (routing) + eye
   (verification). A general-purpose orthogonal router is explicitly rejected.

## Design

### Layer 0 — Assets & style contract

All Layer 0 scripts are **dev-machine-only build steps with committed
outputs** (they read the exemplars from the live OneDrive root
`/Users/gabriele.rendina/Library/CloudStorage/OneDrive-LUTECHSPA/` — beware
stale snapshot folders and cloud-only files needing hydration).

- `scripts/harvest-brand-assets.py`: extract the base64 logos embedded in the
  exemplars into data-URIs, split by confidentiality:
  `assets/brand-pack/` (committed, public-safe: SAP chip, RISE WITH SAP,
  Cloud Foundry badge, curated AWS/Azure/GCP marks replacing the fragile
  external URLs found in the exemplars) and `assets/brand-pack.local/`
  (**gitignored**: customer/partner logos — Lutech, SNAM, Brandart). REUSE
  entries added for everything committed.
- `scripts/build-style-contract.py`: extract the **exact mxGraph style string**
  of every molecule (subaccount frame, product box, capability chip, tier box,
  persona, pill, edge families, badges, title block) from the exemplars + the
  official libraries into `assets/style-contract.json` — the single source of
  truth consumed by the generator *and* the validators. No style literals in
  Python. (June lesson: trust the XML, never the prose.)
- `scripts/build-icon-atlas.py` (build-time, dev machine only): pre-rasterize
  the service/generic icon SVGs into a committed PNG atlas for the pure
  renderer.

### Layer 1 — IR v2 (additive; existing IRs stay valid)

- Group types: `subaccount` (nestable; SAP BTP chip; badges), `governance`,
  `cloud-tier` (`kind: public|private|any-premise`), `custom-app`.
- Node types: `product` with `capabilities: [{label, icon}]`, `chip`, `db`.
  The asymmetry is intentional: `product` is a *leaf* molecule (a box whose
  chips are data, not addressable nodes), while `custom-app` is a *group*
  because it contains addressable service nodes and carries a runtime badge.
- Edge: `pill` (protocol), `flowFamily`
  (`identity|provisioning|master-data|transport|default` → colour + dash from
  the style contract).
- `metadata.branding` (customer logo ref, partner watermark ref),
  `metadata.badges` (hyperscalers, runtimes). Refs into
  `assets/brand-pack.local/` that are absent on the current machine (CI,
  Desktop bundle, other hosts) degrade gracefully: preflight WARNING and the
  logo/watermark is omitted — never a hard fail.
- NETWORK separator (double vertical bar) auto-inserted between the CENTER
  column and the **entire RIGHT tier stack** — both corpora place every
  external tier (Public/Private Cloud, Any-Premise, 3rd party) right of the
  bar; opt-out flag (e.g. Brandart omits it).
- Preflight validates IR v2 with actionable errors.

### Layer 2a — Skeleton layout + flow ordering

Typed slots replace free column packing: LEFT personas · TOP governance ·
CENTER BTP with nested subaccounts · RIGHT tier stack · BOTTOM legend + level
caption · branding on top. **Identity** gets a dedicated slot at the bottom of
the CENTER column; whether it nests *inside* the BTP frame follows the IR
parent (inside if parented to the `btp` group — the gold's convention — or
just below the frame if top-level — the Lutech exemplars' convention). Both
are canonical in the corpus. Within each lane, nodes are ordered
**topologically along the main flow** (Kahn; stable tie-break = IR order;
edge-less nodes keep IR order at the end). Containers keep auto-sizing
bottom-up with per-molecule padding from the contract.

### Layer 2b — Channel routing

- Reserve **gutters** between slots; width scales with the number of edges
  assigned through them.
- Each edge becomes a Manhattan path through channel segments; within a
  channel, edges get parallel **lanes at fixed offsets** — overlap-free by
  construction. Ports are distributed along box sides ordered by target
  direction (per-side barycenter). Crossings are allowed only where
  perpendicular channels meet (as in the hand-made corpus).
- Pills and labels occupy **reserved slots** on their channel (midpoint
  preference, shift to the next free slot); text never intersects text, boxes
  or foreign edges.
- The emitted `.drawio` carries **explicit waypoints** + exit/entry anchors so
  draw.io never re-routes.

### Layer 3 — Visual gate (everywhere)

- `scripts/_pure_render.py`: PIL renderer for **our emitted vocabulary only**
  (rounded rects, text, atlas icons, polylines + arrowheads + dash patterns,
  pills, ellipses, watermark). Goal: geometric fidelity, not pixel parity with
  draw.io. `render-preview.py --engine auto|drawio|pure` (auto = drawio if the
  binary exists, else pure).
- `check-composition.py` v2 — **geometric gate**, all FAIL-blocking:
  edge-crossing count vs budget, edge-through-node/container, text-text and
  text-edge overlap, caption-outside-container, port congestion, channel
  discipline, step/pill collision.
- `skills/sap-diagram-generate/references/visual-rubric.md`: ~25 binary
  checks derived from the corpus,
  each mapped to a **mechanical patch** (IR/layout-hint change: flow-order
  override, group `flow`, channel preference, label shift…). The skill renders,
  **looks at the PNG**, emits findings JSON `{rule, location, patch}`, applies
  patches, regenerates. Max 3 iterations. Deliver only when geometric gate and
  rubric are green (or the user explicitly overrides).
- SKILL.md Step 8 becomes this loop; the Desktop bundle ships the same scripts
  (pure renderer → the loop runs on claude.ai too; if PIL is unavailable,
  degrade to the geometric gate with a warning).

## Acceptance — the diagram exam

1. `demo/nova` L0/L1/L2 regenerated.
2. **Brandart replica** from an inventory-only IR, judged side-by-side with the
   original (must read as the same family). **Local-only exam** — the
   Brandart-derived IR and logos never enter the public repo.
3. **SAP_Task_Center_L1 replica** from IR (gold fidelity).

Done = geometric gate + rubric green on all three **+ Gabriele's visual
approval**. Exams 1 and 3 become golden tests in CI. In CI the gate is pure
render + geometric checks only; the vision rubric runs where an LLM is present
(Claude Code, claude.ai) — never in CI.

## Implementation phases

1. Layer 0: harvest brand pack, style contract, icon atlas.
2. IR v2 parsing + molecule emission (against the contract).
3. Skeleton layout + flow ordering (replaces the zone-layout core).
4. Channel router (waypoints, ports, pill/label slots).
5. Pure renderer + `--engine auto`.
6. Geometric gate v2 + visual rubric + SKILL loop rewrite.
7. Exam: regenerate demos + the two replicas; golden tests in CI; references
   and Desktop bundle rebuild.

Each phase ends with a render compared against the corpus.

## Non-goals

- Archetype B rendering (landscape/migration) — IR must not preclude it.
- PowerPoint/Lucid output; auto-installing MCP servers.
- Pixel-parity between the pure renderer and draw.io.
- A general-purpose orthogonal edge router.
