<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Design — sap-diagrams-pro: hand-quality SAP diagrams (content + visual)

Status: **approved** (2026-06-13). Author: pairing session.

## Problem

Generated `.drawio` diagrams do not match the quality a human would produce by
hand: block **positioning** is wrong and the **type of SAP blocks** is off, even
though all official assets are available. Goal: make the generator produce output
that matches the official SAP BTP Solution Diagram Guideline gold standard.

## Diagnosis (evidence-backed)

Pipeline today: NL → Claude authors a JSON IR → `generate-drawio.py` renders
`.drawio`, icons resolved via `shape-index.json`. Three root causes:

1. **Layout (dominant).** `emit()` defaults to `layout="auto"` → uses graphviz
   `dot` when present, else a crude greedy 3×3 grid.
   - The **dot backend ignores the `position` field entirely** (`_dot_layout.py`
     never reads it) and lays out purely by edge topology with `rankdir=TB`.
     Empirically, on `demo/interactive/cap-bwz-bpa-v2.json`, "Users" (intended
     `top-left`) renders at x=1226 (mid-right), "On-Prem PCE" (`bottom-left`) at
     x=68 mid-left, BTP sub-lanes scrambled. The SAP big-picture axis is
     **horizontal** (consumers → BTP → systems), not vertical.
   - The **greedy backend honors positions but uses fixed cells** → nodes
     overflow groups, sub-groups overlap, single-column packing
     (`cols = (gw-16)//(NODE_W+GAP)` collapses to 1).
   - Neither auto-sizes containers around their contents.

2. **Canonical molecules unused.** `essentials.xml` ships the real SAP organisms
   (`btp-base-layer` 288×250 with SAP logo, `user-and-client`, `on-premise`
   202×70, `cloud-solutions`, `3rd-party`, `legend`, `cloud-connector`, …) but
   they are only emitted if the IR lists `presets[]` by hand, which the SKILL
   never guides. Result: users boxed in a grey rect instead of free icon+label;
   BTP a flat rect with no logo badge / Subaccount frame; backends scattered.

3. **Fidelity drift.** Group `arcSize=12` (canonical 24 area / 16 inner / 32 BTP
   base); title 20px blue + forced suffix (canonical 16px); icons forced to
   61.24×57 distorted (canonical square 24/32/48); font `72,…` (diagrams use
   Helvetica/Arial); step/interface/pill overlaps; firewall `strokeWidth=4`
   (canonical 3). The reference docs themselves encode wrong values
   (`arcSize 8/12`, font 18/13/11/10), so the validator never catches drift and
   even flags the generator's own title.

Content correctness is also weak: the SKILL's "consult SAP skills" steps are
stubs; there is no grounding in authoritative SAP sources, so the inventory
("which components, named how, classified BTP-service vs SaaS-product") is
guessed rather than verified.

## Ground-truth constants (from btp-solution-diagrams XML + guideline)

```
STROKE_NORMAL=1.5  STROKE_FIREWALL=3
ARC_BTP_BASE=32  ARC_AREA=24  ARC_INNER/BOX=16  ARC_PILL=50   (all absoluteArcSize=1)
ICON_SERVICE: S=24 / M=32 / L=48 (square). label S:10/#556B82  M,L:12-14/#1D2D3E
GENERIC_ICON: S=16 / M=24 (rendered ~40-48 for crispness). variants SAP|Non-SAP|Highlight
NUMBER (step) = 30x30 ellipse, east gradient, Helvetica 12, white digit
PILL: rounded arcSize=50, ~57x16 (interface) / ~125x16 (annotation)
FONT = Helvetica,Arial (NOT the 72 brand font — that is website-only)
EDGE: endArrow=blockThin endSize=4; solid=sync dashed=async dotted=optional thick=firewall(3)
COLORS: BTP #0070F2/#EBF8FF · non-SAP #475E75/#F5F6F7 · title #1D2D3E body #556B82
  semantic pos #188918/#F5FAE5 crit #C35500/#FFF8D6 neg #D20A0A/#FFEAF4
  accent teal #07838F/#DAFDF5 indigo #5D36FF/#F1ECFF pink #CC00DC/#FFF0FA
  flow: trust=pink auth=green authz=indigo firewall=thick grey
TITLE = 16px bold (#1D2D3E or #0070F2), suffix "- SAP BTP Solution Diagram" (canonical)
```

## Design — two fronts

### Front A: content correctness (runtime, BEFORE any visual)

**Phase 0 — Preflight (dependency gate), always runs.**
- Reference skills (`secondsky/sap-skills` + `sap-pce-expert`): detect via
  filesystem (`~/.claude/plugins/marketplaces/sap-skills/plugins/`, cache).
  Missing → instruct `npx skills add secondsky/sap-skills`.
- MCP servers: verify `mcp-sap-docs` (tools `mcp__sap-docs__*`), plus
  `sap-note-search`, `sap-cds-mcp`, `sap-fiori-mcp` reachable. Missing → guide
  install (marianfoo/mcp-sap-docs, `claude mcp add`). Claude cannot self-install
  an MCP; it instructs the user.
- Tooling: Python3 + draw.io (render). graphviz dropped.
- Output: ✅/❌ table + install commands. Degraded mode only with explicit
  user consent (INFO that grounding is reduced).

**Phase 1 — Content grounding + requirements interview (gate before visuals).**
1. Parse NL → draft inventory.
2. Ground via MCP `sap-docs`: `sap_discovery_center_search` for canonical name +
   category (drives BTP-service vs SaaS-product classification and icon set) +
   `isDeprecated`; `search`/`fetch` for capability/architecture facts.
3. Consult domain skills in parallel (best-practices, connectivity, pce-expert,
   cloud-identity, api-style, …) → CRITICAL/WARNING/INFO findings (missing
   logging/audit/alert, Cloud Connector, Private Link, IAS↔XSUAA trust, C1 APIs).
4. Structured interview via `AskUserQuestion` — questions **derived** from what
   docs/skills surfaced; ask only what is ambiguous (level; runtime CF/Kyma;
   identity; sync/async; backends; connectivity; observability; in/out channels).
5. Confirm consolidated inventory (canonical names) + findings + answers
   (accept / apply suggestions / amend).
6. Only then build the IR → Front B.

### Front B: visual engine (deterministic, no graphviz)

**`_zone_layout.py`** replaces `_dot_layout.py`. Bottom-up:
1. **Intrinsic sizing**: each node → molecule footprint; each leaf group packs
   its nodes (single row if ≤4; balanced grid else; gap 32). Group size = content
   bbox + padding + top reserve for label/badge. Parents size to fit sub-groups.
2. **Zone assignment**: each top-level group → column LEFT/CENTER/RIGHT from
   `type`+`position` (user→LEFT, btp-layer→CENTER, sap-app/non-sap/third-party→
   RIGHT) and band TOP/MIDDLE/BOTTOM from `position`.
3. **Placement**: CENTER anchored; LEFT left, RIGHT right (zone gap ~80);
   columns centered vertically; reserved top band (title) + bottom band
   (level caption + legend).
4. **Edge anchors**: exit/entry by relative position; `orthogonalEdgeStyle`;
   light manhattan waypoints only where a render shows crossings.

**Canonical molecules** (composed from atoms following essentials recipes):
- user/client (LEFT): generic icon + label, **no box**.
- BTP (CENTER): blue area arcSize=32 + SAP BTP logo badge (top-left, extracted
  from essentials/brandNames) + inner white "Subaccount" frame arcSize=16; lanes
  as inner frames.
- backend (RIGHT): white rounded box arcSize=16 (blue for SAP, grey for
  non-SAP) + icon + title + optional `subtitle`, stacked.
- legend organism (bottom-right): line samples + pill samples.

**Fidelity pass**: apply ground-truth constants above; fix step/interface/pill
geometry to remove overlaps; square icons at canonical size (48 L0/L1, 32 L2).

### IR schema additions

- node: `subtitle` (string), keep `service|genericIcon|boxStyle|interface|step|stepKind`.
- group: optional `flow` ("row"|"column"|"grid"), optional `zone` override
  ("left"|"center"|"right"), `frameless` (bool; default true for `user`).
- metadata: optional `iconSize` ("S"|"M"|"L").
- resolver: tighten `ShapeIndex.resolve` — exact/alias first; fuzzy only on
  word-boundary and only when unambiguous (avoid dangerous substring matches).

### Verification loop

- `scripts/render-preview.py <drawio> [--out png]` — wraps draw.io CLI
  (macOS app path + `drawio` on PATH; timeout-kill; graceful skip in CI).
- `scripts/check-composition.py <drawio>` — asserts: zones present, BTP central,
  no TRUE overlaps among top-level groups (exclude intentional step/badge/pill
  children), title present & sized, legend if ≥2 line styles.
- `scripts/preflight.py` — Phase 0 dependency report (JSON + text).
- SKILL Step 10 = validate + render + check-composition.

## Implementation plan

1. Spec (this doc) + `preflight.py` (run live).
2. `_zone_layout.py` + wire into `emit()`, drop graphviz. Verify: render demo vs gold.
3. Canonical molecules. Verify: render vs gold.
4. Fidelity pass. Verify: render + check-composition.
5. `render-preview.py` + `check-composition.py` + SKILL Step 10.
6. References + validator + resolver aligned to ground-truth.
7. SKILL.md + diagram-architect.md rewrite (preflight + grounding + interview),
   IR-aware. Regenerate demos (cap-bwz-bpa, nova L0/L1/L2), compare to gold.

Note: implementation orders the visual engine (2–6) before the SKILL/agent
rewrite (7) so the IR schema is final before the prompt layer is written against
it; `preflight.py` (part of Phase 1) ships first as a self-contained gate.

## Non-goals

- PowerPoint/Lucid output. - Auto-installing MCP servers. - Submitting anything
  outside the local filesystem. - Keeping graphviz.
