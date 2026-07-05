<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Visual Rubric — Binary Checks for the Vision Loop (Step 8)

Source: the approved design spec ([`2026-07-04-perfect-diagrams-design.md`](../../../docs/superpowers/specs/2026-07-04-perfect-diagrams-design.md)), the gold `SAP_Task_Center_L1.drawio`, `assets/style-contract.json`, and defects observed in the engine's own renders while building the channel router (Task 8).

## What this is

`check-composition.py` is a **geometric** gate: it runs against coordinates and catches things a bounding-box computation can prove (overlaps, containment, crossing counts once Task 12 lands). It cannot see the picture. The spec's "blind pipeline" evidence is exactly this: a render with ≥8 crossing edges, a step badge sitting on top of a group title, and a pill cut in half by a step circle — and `check-composition.py` reported **0 fail / 0 warn**, because none of that is expressible as a coordinate predicate without also modelling text metrics, icon identity and human legibility.

This rubric is the other half: **26 binary checks** (the spec's "~25") that a vision-capable Claude applies by *looking at* the rendered PNG, each one mapped to a mechanical patch a script can apply without judgement. It is Layer 3 of the design ("the eye"), and it runs everywhere an LLM is present (Claude Code, claude.ai, Desktop) — **never in CI**, where the geometric gate alone is authoritative.

## The loop (SKILL.md Step 8)

1. Render the current `.drawio` (`render-preview.py --engine auto`).
2. **Read the PNG.** Walk every check below against it, with the source `.drawio` / IR JSON open alongside — the image tells you *what* is wrong, the IR tells you the group/edge/node **id** a patch needs to name.
3. Emit **findings JSON**: one object per failing check, shape `{rule, location, patch}`:

   ```json
   [
     {
       "rule": "route-no-pierce",
       "location": "edge 'Process insights' (audit-log → cloud-alm) cuts through the Build Process Automation box and two frame borders",
       "patch": {"op": "channel_prefer", "edge": "e-audit-almm", "value": "V2"}
     },
     {
       "rule": "comp-legend-present",
       "location": "Legend panel (bottom-right) lists 2 line styles; the diagram uses 4 distinct edge colors",
       "patch": null
     }
   ]
   ```

   `patch` is one of the seven ops below, or **`null`** for a check whose fix isn't mechanical (see "Manual findings"). Never invent an eighth op to force a fix through.
4. `apply-rubric-patches.py` writes every non-null patch into `diagram.layoutHints[]` and regenerates; `null`-patch findings pass through unchanged into the iteration report.
5. Regenerate, re-render, re-check. **Max 3 iterations.**
6. Deliver only when the geometric gate (`check-composition.py`) **and** every rubric check are green, or the user explicitly overrides — log the override and any residual findings in the final report.

If no render engine is available at all (no draw.io, no Pillow), skip this loop entirely and say so as a WARNING — the geometric gate is still authoritative and generation must not dead-end.

## Patch-op vocabulary

Every patch is one of these seven ops, written into `diagram.layoutHints[]` (IR v2, `scripts/generate-drawio.py`). No other op exists — a check that needs something else is **manual**.

| op | args | what it targets | value domain (grounded in the engine) |
|---|---|---|---|
| `set_group_flow` | `group`, `value` | a group's `flow` field — intra-group child packing | `row` \| `col` \| `grid` |
| `set_zone` | `group`, `value` | a group's `zone` field — which column it lands in | `left` \| `center` \| `right` |
| `order_override` | `group`, `value` (list of node ids) | sibling order inside a group/lane — beats the default topological-rank sort | any permutation of that group's own node ids |
| `nudge_label` | `edge`, `value` | an edge's pill/label position — shifts it to the next free slot on its channel | `"next-slot"` |
| `channel_prefer` | `edge`, `value` | which reserved corridor an edge is routed through — beats the router's default (BFS) choice | a channel id: `V0`, `V1`, … (vertical gutters, numbered left→right by column gap) or `Htop` / `Hbot` (the horizontal corridors above/below the content band) |
| `set_icon_size` | `value` | global service-icon render size | `S` \| `M` \| `L` |
| `toggle_separator` | `value` | forces the NETWORK bar on/off regardless of the auto-detection rule | `true` \| `false` |

```json
{"op":"set_group_flow","group":"<id>","value":"row|col|grid"}
{"op":"set_zone","group":"<id>","value":"left|center|right"}
{"op":"order_override","group":"<id>","value":["nid","nid",...]}
{"op":"nudge_label","edge":"<id>","value":"next-slot"}
{"op":"channel_prefer","edge":"<id>","value":"<channel-id>"}
{"op":"set_icon_size","value":"S|M|L"}
{"op":"toggle_separator","value":true|false}
```

Two grounding notes on the table above:

- **Channel ids are read off the current layout, not memorized.** `_channel_router.py` assigns them per-diagram (`V0`, `V1`, … by column-gap order; `Htop`/`Hbot` for the two horizontal corridors) — they are not stable across diagrams or router versions, so a finding must cite the id actually present in *this* render's routing, never a cached one from a previous run.
- **This vocabulary is the contract, not yet the wiring.** As of Task 8e, `layoutHints` is parsed into the IR (`generate-drawio.py`) but `_skeleton_layout.py` / `_channel_router.py` don't consume it yet — `apply-rubric-patches.py` (Task 13-code) is what wires `order_override` into the rank sort and `channel_prefer` into the BFS choice. This doc defines what each op must mean once wired; it does not claim today's engine already reacts to them.

**Why Semantics (Group 4) is 100% manual:** all seven ops move or reorder things already on the canvas. None of them recolor a box, swap an icon, or edit text. Any check whose fix is "make this border blue instead of grey" or "use the identity-green edge family instead of default grey" is always `patch: null` — the fix is an IR content edit (a `type` / `flowFamily` / `service` field), not a layout hint. Composition and Routing lean the other way, because slot / order / channel *are* what the seven ops control.

## Manual findings

A `null` patch is not a lesser finding — it still counts against "green," and still belongs in the findings JSON and the final scorecard. It means a human (or a follow-up content-editing pass over the IR) has to fix this; no layout hint can. `apply-rubric-patches.py` must never error or silently drop these — it passes them straight through into the loop's report.

---

## Group 1 — Composition (10 checks)

| id | look at | pass criterion | patch |
|---|---|---|---|
| `comp-subaccount-frame` | the BTP container's interior, when it holds more than 3 service boxes | at least one inner white "Subaccount"-labelled frame groups them — they are not floating loose in a flat grid against the outer blue border | manual — a new frame is a new IR group, not a layout hint |
| `comp-personas-left` | every frameless person/device (persona) icon | all of them sit left of the BTP container's left edge, and left of every other group | `set_zone` `{"group":"<persona-group>","value":"left"}` |
| `comp-btp-central` | the SAP-BTP-chip container | it sits between the LEFT and RIGHT columns and is the single largest bordered container on the canvas | `set_zone` `{"group":"<btp-group>","value":"center"}` |
| `comp-tiers-right` | every backend / tier / SaaS / on-prem / third-party group | each sits to the right of the BTP container (and right of the NETWORK bar, when one is present) | `set_zone` `{"group":"<tier-group>","value":"right"}` |
| `comp-network-separator` | the gap between the center content and any right-hand tier stack | a vertical grey bar labelled NETWORK is present whenever a tier/backend group sits outside the BTP container; absent when none does | `toggle_separator` `{"value": true}` (or `false` to remove a spurious one) |
| `comp-identity-placement` | the identity cluster (IAS / XSUAA / Authorization) | it is nested at the bottom of the BTP frame, or rendered as its own BTP-blue frame directly under/beside it — never folded into a generic "Ops"/third-party box on the far right | `set_zone` `{"group":"<identity-group>","value":"center"}` — works whenever the group isn't already pure-identity-typed (the engine auto-centers those); the common real failure is a *mixed* identity+ops group, which takes the override normally. Splitting the group is the better long-term fix (manual) |
| `comp-governance-top` | a Governance-labelled frame, if one exists | it sits above the BTP container's top edge, not beside or below it | manual — no `set_zone` value places a group in the top band; a misclassified group needs its IR `type`/`position` fixed |
| `comp-legend-present` | every distinct edge color/dash pattern used anywhere in the diagram | if ≥2 distinct treatments are used, a Legend panel is visible and lists a swatch for **every one** of them, not just a default subset | manual — legend content isn't in the op vocabulary |
| `comp-branding-logo` | the top-left corner, left of/above the title | when `metadata.branding.customerLogo` is set, a logo (or its text-chip fallback) renders there without overlapping the title | manual |
| `comp-branding-watermark` | the area behind the diagram content | when a partner watermark is configured, it is large, clearly lower-contrast than every foreground element, and never darkens text/icons it sits behind | manual |

**Grounding.** The gold `SAP_Task_Center_L1` nests everything inside a "Subaccount" frame and keeps "SAP Cloud Identity Service" as its own labelled block. The archetype-A IR v2 sample render (end of Task 8) reproduces both correctly — nested `Test ⊃ Production` subaccounts, and a standalone bottom "SAP BTP" identity frame holding XSUAA + Identity Authentication. The NOVA L1 render at the same point in the engine shows the live counter-examples that motivated `comp-subaccount-frame`, `comp-tiers-right` and `comp-identity-placement`: 15+ BTP services float directly against the outer blue border with no inner frame; "On-Prem" (Cloud Connector + S/4HANA on-prem) sits bottom-left — left of the personas' own column — instead of right of the NETWORK bar; and "IAS / XSUAA-Keycloak / Cloud ALM" are merged into one generic grey right-hand box indistinguishable from a third-party tier. `comp-legend-present`'s failure mode is visible in the archetype-A render: at least four distinct edge/pill colors are on the canvas (grey default, purple flowFamily, green SAML2/OIDC, dashed grey CTMS) but the Legend panel lists only two.

---

## Group 2 — Routing (5 checks)

| id | look at | pass criterion | patch |
|---|---|---|---|
| `route-orthogonal` | every edge segment | purely horizontal or vertical — no diagonal segments anywhere | manual — the router is Manhattan-only by construction; a diagonal is an engine bug, not a layout preference |
| `route-no-pierce` | every edge's path against every box it doesn't start or end at | no segment cuts through a node or container interior; the sole exception is the NETWORK bar, which a right-bound edge may cross exactly once | `channel_prefer` `{"edge":"<id>","value":"<channel-id>"}` |
| `route-no-collinear-overlap` | pairs of edges sharing a channel | no two edges are drawn on top of each other (a visually doubled/thicker single line) — parallel edges keep a visible gap | `channel_prefer` on one edge of the pair (or `nudge_label` if only the labels, not the lines, coincide) |
| `route-crossing-budget` | every X-intersection between two unrelated edges | total crossings ≤ 8 (or the diagram's declared budget in metadata) | `order_override` on the busiest group first; `channel_prefer` on the worst individual offenders |
| `route-left-to-right` | the primary flow, persona → BTP → backend | arrows generally point rightward/downward; no edge backtracks across more than one column in a long perimeter detour | `order_override` (shortens the rank-based path); `channel_prefer` if reordering alone isn't enough |

**Grounding.** `route-orthogonal` has no counter-example in either Task-8 render — it's kept because it's an explicit design invariant, and a pure-renderer or hint-misuse regression could violate it silently. `route-no-pierce`'s textbook failure is the archetype-A render's "Process insights" edge: a near-straight vertical line from Audit Log to Cloud ALM that cuts through the Build Process Automation box and three frame borders instead of using the reserved gutter. `route-crossing-budget` and `route-no-collinear-overlap` are both visible in the NOVA L1 render's center BTP block, where the Integration Suite / Fiori / CAP / DOX / AI Core / Event Mesh row produces well over 8 crossings and several dashed/dotted lines fuse into single strokes. `route-left-to-right`'s failure is the same render's long trunk line that runs from the top External-Channels area straight down the far-left margin, past all three personas, to Cloud Connector — a backward perimeter detour instead of a short local connection. Task 9 (greedy crossing reduction) had not landed as of these renders, so `route-crossing-budget` in particular should improve once it does — that doesn't retire the check, since layout hints can still be needed on top of the router's own reduction pass.

---

## Group 3 — Typography (6 checks)

| id | look at | pass criterion | patch |
|---|---|---|---|
| `type-font-family` | every text element on the canvas | one sans-serif family throughout (Helvetica/Arial, or the pure renderer's substitute) — no serif, monospace, script or mixed family | manual — font family is a style-contract/engine setting, not an IR field |
| `type-title-style` | the diagram's main title (top-left, above all content) | rendered in Horizon blue `#0070F2`, bold, visibly larger than every group/node/edge label — group and organism titles are *also* bold and large but stay the default dark text color; only the one diagram title is blue | manual |
| `type-label-no-overlap` | every label (node captions, group titles, edge labels, pill text) | no label's box overlaps another label, a node/icon, a group border, or a foreign edge | `nudge_label` when the offender is an edge label/pill; `order_override` or `set_group_flow` when it's a node caption or group title crowded by its own siblings |
| `type-pill-on-edge` | every protocol/annotation pill (`Authenticate`, `Authorize`, `OIDC`, `SAML2/OIDC`, `SCIM`, `Trust`…) — **not** the node-top `Interface` badge, a different molecule | the pill sits centred on its own edge, with the line visibly passing behind its mid-height — never floating beside the line or resting on a different edge | `nudge_label` `{"edge":"<id>","value":"next-slot"}` |
| `type-step-no-cover` | every numbered step circle (①②③…) | it does not overlap a group title or a node's `Interface` badge; it sits clear in its box's corner with its own margin | `order_override` (moves the stepped node away from the crowded corner); manual if that doesn't clear it — the corner inset itself is fixed geometry, not hint-driven |
| `type-no-tofu` | every glyph rendered | no `.notdef` boxes (☐), replacement characters (�), or unexpected symbol substitutions | manual — a missing glyph means the source text uses a character outside the font's coverage |

**Grounding.** The gold's title cell is literally `font-family: arial; font-size: 16px; color: rgb(0, 112, 242)` (`#0070F2`) bold — the exact rule `type-title-style` encodes — while the gold's organism titles ("Subaccount", "SAP Cloud Solutions", "3rd Party Applications") are the same 16px bold arial but in the default dark color, which is why the check calls out that distinction rather than demanding blue everywhere. `type-label-no-overlap` and `type-step-no-cover` are the two most visible defects in the NOVA L1 render: step badge "①" sits directly on top of the "External Channels" group title, and step badge "③" overlaps the node-top `Interface` badge on CAP Backend so badly only "…terface" remains legible. That collision is also why `type-pill-on-edge` explicitly disambiguates the edge-attached protocol pill (`pill-protocol` in the style contract) from the node-top `Interface` badge (`pill-interface`, rendered per `atomic-design.md`'s node-level `interface` field) — they are two different molecules and only the former is `nudge_label`-patchable. Neither Task-8 render shows a `type-pill-on-edge` failure (SAML2/OIDC, SCIM, Authenticate, Authorize, CTMS all sit correctly on their lines in both) — the check is retained because the pill/label slot allocator (Task 8e) is new and the corpus mandates the rule explicitly. `type-font-family` and `type-no-tofu` have no counter-example either; both guard regressions the geometric gate cannot see at all, since it never touches font metrics or glyph coverage.

---

## Group 4 — Semantics (5 checks)

| id | look at | pass criterion | patch |
|---|---|---|---|
| `sem-palette` | every fill and border color on the canvas | every color matches an approved `(border, fill)` pair from `assets/style-contract.json`'s `meta.palette` — a superset of `horizon-palette.md`'s core set that also legitimizes contract-only structural tones (the NETWORK bar's `#5B738B`, the step-circle's gradient endpoint) — no pure black, no off-palette hue, no gradient outside that one sanctioned exception | manual |
| `sem-edge-families` | the color/dash of every edge, read against what its label says it represents | identity/authentication flows are green solid, provisioning/SCIM flows are purple, master-data flows are magenta, transport/change flows are grey dashed, firewall boundaries are thick (`strokeWidth=3`) grey — no edge's color contradicts its own label | manual — `flowFamily` is IR content |
| `sem-icons-match` | every service icon glyph against its caption | the pictogram is the canonical SAP icon for the named service (or a neutral placeholder box when genuinely unresolved) — never a mismatched/generic icon substituted for a resolvable service, and never an icon overlapping its own label | manual — icon resolution is the `service` field + molecule emission, not a layout hint |
| `sem-border-sap-vs-nonsap` | the border color of every system box | SAP-owned/BTP-affiliated boxes are blue (`#0070F2`); non-SAP/third-party/generic-external boxes are grey (`#475E75`) — no confirmed SAP box is grey and no confirmed non-SAP box is blue | manual — border color follows the group's `type` |
| `sem-one-chip-per-container` | every BTP-blue outer container, including nested subaccounts and any standalone identity/governance box | each independent outer container shows exactly one "SAP BTP" chip; nested inner frames carry only their own text label — never a repeated chip down a nesting chain ("staircase") | manual — chip emission follows nesting/type |

**Grounding.** None of the seven ops touch color, icon choice or text content, so every Semantics check is `patch: null` by construction (see "Why Semantics is 100% manual" above). `sem-icons-match`'s concrete failure is the archetype-A render's Cloud ALM "Monitor" capability chip: a generic laptop glyph rendered overlapping its own label, while the sibling chips "Analyze"/"Automate"/"Alert" in the same 2×2 grid show no icon at all. `sem-one-chip-per-container` has no failure in either Task-8 render — the archetype-A sample is in fact the positive exemplar: the outer "SAP BTP" frame and the separate bottom identity frame each carry exactly one chip, and the nested "Test"/"Production" subaccount frames correctly carry plain text labels with no extra chip. The check is retained anyway because the spec calls the "no staircase" rule out explicitly and deeper subaccount nesting (2+ levels) hasn't been exercised yet. `sem-palette` and `sem-border-sap-vs-nonsap` also pass cleanly on both renders (e.g. NOVA L1's "Non-SAP Storage" box is correctly grey; "On-Prem" is correctly blue because Cloud Connector and S/4HANA-on-prem are both SAP-affiliated) — both checks stay because they're cheap to apply and guard the single most common authoring mistake per `component-groups.md`.

---

## Quick reference — auto-patchable vs. manual

12 of the 26 checks resolve with a mechanical patch; 14 are always `patch: null` — content/style fixes only a human, or a follow-up authoring pass over the IR, can make. This split is not arbitrary: it falls directly out of the seven ops being layout/routing-only (see "Why Semantics is 100% manual").

| Auto-patchable (12) | Manual (14) |
|---|---|
| `comp-personas-left`, `comp-btp-central`, `comp-tiers-right`, `comp-network-separator`, `comp-identity-placement` | `comp-subaccount-frame`, `comp-governance-top`, `comp-legend-present`, `comp-branding-logo`, `comp-branding-watermark` |
| `route-no-pierce`, `route-no-collinear-overlap`, `route-crossing-budget`, `route-left-to-right` | `route-orthogonal` |
| `type-label-no-overlap`, `type-pill-on-edge`, `type-step-no-cover` | `type-font-family`, `type-title-style`, `type-no-tofu` |
| — | `sem-palette`, `sem-edge-families`, `sem-icons-match`, `sem-border-sap-vs-nonsap`, `sem-one-chip-per-container` |

## Common mistakes

- **Inventing an eighth op.** If a check seems to need one, it doesn't — mark it manual. The seven ops are the entire vocabulary `apply-rubric-patches.py` understands; an unrecognized op is a hard error (exit 2 listing the vocabulary), not a soft skip.
- **Treating `patch: null` as "no finding."** It still counts against "green"; it still belongs in the findings JSON and the final scorecard.
- **Re-deriving what `check-composition.py` already proves.** If the geometric gate already FAILs on a crossing-budget or overlap violation, don't re-litigate the exact number here — confirm visually if useful, but the gate is authoritative for anything it can compute. The rubric exists for what only a look at the actual pixels can catch: a step circle sitting on a title, a mismatched icon, a font substitution.
- **Guessing a channel id.** `channel_prefer` values are read off the *current* layout (`V0`, `V1`, …, `Htop`/`Hbot`), never memorized from a previous run — they are not stable across diagrams or router versions.
- **Running more than 3 iterations.** If checks are still red after 3 patch-and-regenerate cycles, stop and report the residual list — don't loop indefinitely chasing a diminishing return.
- **Skipping the loop silently.** If no render engine is available, say so in the report as a WARNING; never deliver without at least the geometric gate having run.

## References

- [`2026-07-04-perfect-diagrams-design.md`](../../../docs/superpowers/specs/2026-07-04-perfect-diagrams-design.md) — the approved design this rubric implements (Layer 3).
- [`horizon-palette.md`](horizon-palette.md), [`line-styles-spacing.md`](line-styles-spacing.md) — the palette and edge-family rules `sem-palette` / `sem-edge-families` enforce.
- [`atomic-design.md`](atomic-design.md), [`component-groups.md`](component-groups.md) — the molecule/organism vocabulary the Composition and Semantics checks assume.
- [`../../../assets/style-contract.json`](../../../assets/style-contract.json) — the single source of truth for every color/geometry value referenced above.
- `scripts/check-composition.py` — the geometric, code-based sibling gate; run before this rubric, not instead of it.
