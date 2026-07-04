<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Perfect Diagrams Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `sap-diagram-generate` emit draw.io diagrams that pass a corpus-derived visual bar (official SAP gold + Gabriele's hand-made conventions) on Claude Code, claude.ai/Desktop and CI alike.

**Architecture:** Three layers per the approved spec (`docs/superpowers/specs/2026-07-04-perfect-diagrams-design.md`): (0/1) a style contract + brand pack + IR v2 molecule grammar, (2) skeleton slot layout with flow ordering and channel-based edge routing emitting explicit waypoints, (3) a visual gate — pure-Python PNG renderer, geometric FAIL-blocking checks, and a vision rubric loop in the SKILL.

**Tech Stack:** Python 3.12 stdlib + Pillow (pure renderer only), pytest (dev/CI), draw.io CLI (delivery render when present), existing `shape-index.json` pipeline.

**Branch:** execute on `design/perfect-diagrams` (already created; spec committed).

**Conventions for every task:** run tests from the repo root `/Users/gabriele.rendina/tools/sap-diagrams-pro`; `python3 -m pytest tests -q` must be green before each commit; commit messages end with the Claude co-author trailer. Styles NEVER appear as literals in engine code — always read from `assets/style-contract.json` (Task 2 adds an automated guard).

**License guard (tightens the spec safely):** the committed `assets/brand-pack/` contains ONLY assets derived from the Apache-2.0 `btp-solution-diagrams` repo (SAP logo chip, SAP BTP chip, brand-name text molecules). Hyperscaler marks (AWS/Azure/GCP), Cloud Foundry, RISE WITH SAP, Lutech and customer logos are trademarks → they live in gitignored `assets/brand-pack.local/`, harvested from the exemplars on this machine. When a `.local` asset is missing the engine emits a neutral **text badge** (e.g. bordered chip "AWS") + preflight WARNING — never a hard fail.

**Reference corpus paths (this machine):**
- Official: `/Users/gabriele.rendina/tools/btp-solution-diagrams` (11 examples in `assets/editable-diagram-examples/`, libraries in `assets/shape-libraries-and-editable-presets/draw.io/`)
- Exemplars: `/Users/gabriele.rendina/Library/CloudStorage/OneDrive-LUTECHSPA/Progetti/30. Alia/BTP Architecture/SAP_BTP_Architecture_20240909_Lutech_SSAM.drawio`, same dir `…20240906_Lutech.drawio`, `/Users/gabriele.rendina/Library/CloudStorage/OneDrive-LUTECHSPA/Progetti/43. Brandart/brandart_arch_v01.drawio` (OneDrive may need hydration; beware stale snapshot folders).

---

## File Structure (target)

```
assets/
  style-contract.json          # single source of truth: molecule → exact style + geometry (committed)
  style-contract.schema.json   # JSON schema for the contract (committed)
  brand-pack/                  # public-safe data-URI assets + index.json (committed)
  brand-pack.local/            # customer/partner/trademark assets (gitignored)
  icon-atlas/                  # pre-rasterized 96px PNGs + index.json (committed)
scripts/
  harvest-brand-assets.py      # dev-only: exemplars → brand packs
  build-style-contract.py      # dev-only: corpus → style-contract.json
  build-icon-atlas.py          # dev-only: SVGs → icon-atlas/
  _molecules.py                # IR v2 molecule → mxCell dicts (styles from contract)
  _skeleton_layout.py          # slot layout + flow ordering (replaces _zone_layout.py)
  _channel_router.py           # gutters, lanes, ports, waypoints, pill/label slots
  _geom_checks.py              # shared geometry predicates (overlap, crossing, containment)
  _pure_render.py              # PIL renderer for our emitted vocabulary
  validate-ir.py               # IR v2 validation with actionable errors
  apply-rubric-patches.py      # findings JSON → IR layoutHints patches
  generate-drawio.py           # MODIFIED: IR v2 parsing, molecule emission, router wiring
  check-composition.py         # MODIFIED: v2 geometric gate (FAIL-blocking)
  render-preview.py            # MODIFIED: --engine auto|drawio|pure
skills/sap-diagram-generate/
  SKILL.md                     # MODIFIED: Step 8 = visual gate loop
  references/visual-rubric.md  # ~25 binary checks + patch mapping
tests/                         # pytest suite + fixtures
```

---

### Task 0: pytest scaffolding + safety net

**Files:**
- Create: `tests/conftest.py`, `tests/test_smoke.py`
- Modify: `.github/workflows/engine-smoke-test.yml` (add pytest step)

- [ ] **Step 1: Write conftest + failing smoke test**

```python
# tests/conftest.py
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_script(name: str):
    """Import a scripts/ module even when its filename contains dashes."""
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
```

```python
# tests/test_smoke.py
from conftest import load_script


def test_zone_icon_size():
    zl = load_script("_zone_layout")
    assert zl.icon_size("L1") == 48
    assert zl.icon_size("L2") == 32


def test_generator_imports():
    gen = load_script("generate-drawio")
    assert hasattr(gen, "emit")
```

- [ ] **Step 2: Run: `python3 -m pytest tests -q`** — if pytest is missing: `python3 -m pip install --user pytest`. Expected: **2 passed**.
- [ ] **Step 3: Add CI step** — in `.github/workflows/engine-smoke-test.yml`, after existing setup, add `pip install pytest pillow` and a step `python3 -m pytest tests -q`.
- [ ] **Step 4: Commit** — `test: pytest scaffolding + engine smoke tests`

---

## Phase 1 — Layer 0: assets & style contract

### Task 1: brand-pack harvester

**Files:**
- Create: `scripts/harvest-brand-assets.py`, `assets/brand-pack.manifest.json`, `tests/test_harvest.py`, `tests/fixtures/mini-exemplar.drawio`

Manifest (committed) classifies what to harvest; the script never decides confidentiality by itself:

```json
{
  "assets": [
    {"key": "sap-logo-chip",  "public": true,  "source": "official", "official_ref": "sap_brand_names.xml:SAP (Default)"},
    {"key": "sap-btp-chip",   "public": true,  "source": "official", "official_ref": "sap_brand_names.xml:SAP BTP (Text Only)"},
    {"key": "aws-badge",      "public": false, "source": "exemplar", "match": {"value_regex": "(?i)aws|amazon", "mime": "image/(png|svg\\+xml)"}},
    {"key": "azure-badge",    "public": false, "source": "exemplar", "match": {"value_regex": "(?i)azure", "mime": "image/(png|svg\\+xml)"}},
    {"key": "cf-badge",       "public": false, "source": "exemplar", "match": {"value_regex": "(?i)cloud ?foundry"}},
    {"key": "rise-badge",     "public": false, "source": "exemplar", "match": {"value_regex": "(?i)rise"}},
    {"key": "lutech-logo",    "public": false, "source": "exemplar", "match": {"value_regex": "(?i)lutech"}}
  ]
}
```

- [ ] **Step 1: Fixture** — hand-write `tests/fixtures/mini-exemplar.drawio`: an uncompressed `<mxfile><diagram><mxGraphModel>` with two vertices whose styles embed tiny `image=data:image/png;base64,…` (any 1×1 PNG) and values "AWS" and "Lutech".
- [ ] **Step 2: Failing tests**

```python
# tests/test_harvest.py
import json, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

def run_harvest(tmp_path, sources):
    out = subprocess.run([sys.executable, ROOT/"scripts/harvest-brand-assets.py",
                          "--manifest", ROOT/"assets/brand-pack.manifest.json",
                          "--out-public", tmp_path/"pub", "--out-local", tmp_path/"loc",
                          *map(str, sources)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return tmp_path

def test_exemplar_assets_go_local(tmp_path):
    run_harvest(tmp_path, [ROOT/"tests/fixtures/mini-exemplar.drawio"])
    loc = json.loads((tmp_path/"loc/index.json").read_text())
    assert {"aws-badge", "lutech-logo"} <= set(loc)      # matched by value_regex
    assert loc["aws-badge"]["dataUri"].startswith("data:image/")

def test_public_assets_come_from_official_repo_only(tmp_path):
    run_harvest(tmp_path, [ROOT/"tests/fixtures/mini-exemplar.drawio"])
    pub = json.loads((tmp_path/"pub/index.json").read_text()) if (tmp_path/"pub/index.json").exists() else {}
    assert all(v["source"] == "official" for v in pub.values())
```

- [ ] **Step 3: Run** `python3 -m pytest tests/test_harvest.py -q` → FAIL (script missing).
- [ ] **Step 4: Implement `scripts/harvest-brand-assets.py`** — argparse (`--manifest`, `--out-public`, `--out-local`, positional source `.drawio` files + optional `--official-repo` path). Decompress drawio pages (base64 → raw inflate → urldecode, same helper as validators), find `image=data:` styles, attach the nearest cell `value` (self or parent) for regex matching, write `{key: {dataUri, source, from, license_note}}` into the right pack's `index.json`. `source: official` entries are extracted from the official libraries' XML (style string containing `image=data:` in `sap_brand_names.xml`).
- [ ] **Step 5: Run tests** → PASS. **Commit** `feat(assets): brand-pack harvester with public/local confidentiality split`.
- [ ] **Step 6: Dev-only run (this machine)** — run against the three exemplars + `--official-repo ~/tools/btp-solution-diagrams`, writing to `assets/brand-pack/` and `assets/brand-pack.local/`. Commit ONLY `assets/brand-pack/` (+ REUSE entry in `REUSE.toml`). Verify `git status` shows `brand-pack.local` ignored.

### Task 2: style contract

**Files:**
- Create: `scripts/build-style-contract.py`, `assets/style-contract.schema.json`, `tests/test_style_contract.py`
- Create (artifact, committed): `assets/style-contract.json`

Required molecule keys (schema `required`): `title-block`, `btp-area`, `subaccount-frame`, `governance-strip`, `product-box`, `capability-chip`, `custom-app-box`, `tier-box-sap`, `tier-box-nonsap`, `backend-box`, `persona`, `service-icon`, `chip`, `db`, `legend`, `network-separator`, `badge-hyperscaler`, `badge-runtime`, `watermark`, `pill-protocol`, `pill-interface`, `step-circle`, plus edge families `edge-default`, `edge-identity`, `edge-provisioning`, `edge-master-data`, `edge-transport`, `edge-firewall`. Every entry: `{style: str, geometry: {w,h,padX,padTop,padBottom,gap…}, source: "official"|"exemplar", notes}`.

- [ ] **Step 1: Failing tests**

```python
# tests/test_style_contract.py
import json, re
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
REQUIRED = {"title-block","btp-area","subaccount-frame","governance-strip","product-box",
  "capability-chip","custom-app-box","tier-box-sap","tier-box-nonsap","backend-box",
  "persona","service-icon","chip","db","legend","network-separator","badge-hyperscaler",
  "badge-runtime","watermark","pill-protocol","pill-interface","step-circle",
  "edge-default","edge-identity","edge-provisioning","edge-master-data","edge-transport","edge-firewall"}
HORIZON = {"#0070F2","#EBF8FF","#475E75","#F5F6F7","#1D2D3E","#556B82","#188918","#F5FAE5",
  "#C35500","#FFF8D6","#D20A0A","#FFEAF4","#07838F","#DAFDF5","#5D36FF","#F1ECFF",
  "#CC00DC","#FFF0FA","#470BED","#5B738B","#FFFFFF","#ffffff","none"}

def contract():
    return json.loads((ROOT/"assets/style-contract.json").read_text())

def test_required_molecules_present():
    assert REQUIRED <= set(contract()["molecules"])

def test_styles_parse_and_colors_in_palette():
    for name, m in contract()["molecules"].items():
        for pair in filter(None, m["style"].split(";")):
            assert "=" in pair or pair.isalnum(), f"{name}: bad token {pair}"
        for col in re.findall(r"(?:fill|stroke|font)Color=([^;]+)", m["style"]):
            assert col in HORIZON, f"{name}: off-palette {col}"

def test_edge_families_semantics():
    m = contract()["molecules"]
    assert "strokeColor=#188918" in m["edge-identity"]["style"]
    assert "strokeColor=#470BED" in m["edge-provisioning"]["style"]
    assert "strokeColor=#CC00DC" in m["edge-master-data"]["style"]
    assert "dashed=1" in m["edge-transport"]["style"]

def test_no_style_literals_in_engine_sources():
    # the guard: engine code must not hardcode styles
    for f in ["_molecules.py", "_skeleton_layout.py", "_channel_router.py"]:
        p = ROOT/"scripts"/f
        if p.exists():
            assert "fillColor=#" not in p.read_text(), f"{f} hardcodes styles"
```

- [ ] **Step 2: Run** → FAIL (no contract). 
- [ ] **Step 3: Implement `build-style-contract.py`** — reads the official libraries (`essentials.xml`, `area_shapes.xml`, `annotations_and_interfaces.xml`, `connectors.xml`, `numbers.xml`, `sap_brand_names.xml` — mxlibrary JSON inside `<mxlibrary>`) and, when given `--exemplar` paths, the Lutech files; selects the canonical cell per molecule via a mapping table in the script (documented per key: which library entry / which exemplar cell, e.g. product-box ≈ SSAM "SAP Build Process Automation" container style, capability-chip ≈ its "Decision" child); normalizes (strip `image=` payloads → `image=@{asset-key}` placeholders resolved at emit time from brand packs); writes contract + validates against schema. Geometry numbers come from the source cells' mxGeometry.
- [ ] **Step 4: Dev run** producing `assets/style-contract.json`; run tests → PASS (contract tests only; the literal-guard passes vacuously until the engine files exist).
- [ ] **Step 5: Commit** `feat(assets): style contract extracted from official + exemplar corpus` (schema + artifact + script).

### Task 3: icon atlas

**Files:**
- Create: `scripts/build-icon-atlas.py`, `tests/test_icon_atlas.py`; artifact `assets/icon-atlas/` (committed)

- [ ] **Step 1: Failing test** — `index.json` exists; every entry's PNG file exists; PIL opens it; size == 96×96; every `service-icon` name referenced by `assets/shape-index.json` services with `drawioStyle` containing `image=data:image/svg` has an atlas entry (skip-list allowed for broken SVGs, must be empty at first).
- [ ] **Step 2: Implement** — for each service/genericIcon in `shape-index.json`, decode the embedded SVG data-URI and rasterize at 96×96. Rasterizer resolution order: `resvg` CLI → `cairosvg` module → error with `brew install resvg` hint. `--only <name>` for incremental runs.
- [ ] **Step 3: Dev run** → commit `assets/icon-atlas/` (expect ~600 files, ~2-3 MB) + REUSE entry. Run tests → PASS. **Commit** `feat(assets): pre-rasterized icon atlas for the pure renderer`.

---

## Phase 2 — IR v2 + molecules

### Task 4: IR v2 parsing + validate-ir.py

**Files:**
- Modify: `scripts/generate-drawio.py` (dataclasses `Group`, `Node`, `Edge`, `Diagram` — around lines 200-300)
- Create: `scripts/validate-ir.py`, `tests/test_ir_v2.py`, `tests/fixtures/ir-v2-sample.json`

New fields (all optional → v1 IRs unchanged): Group: `kind` (cloud-tier), `badges: {hyperscalers: [], runtimes: []}`, types `subaccount|governance|cloud-tier|custom-app` accepted. Node: `type` (`product|chip|db`), `capabilities: [{label, icon?}]`. Edge: `pill: str`, `flowFamily: identity|provisioning|master-data|transport|default`. Metadata: `branding: {customerLogo?, partnerWatermark?}`, `badges`. Diagram: `layoutHints: []` (consumed by Task 13).

- [ ] **Step 1: Fixture `ir-v2-sample.json`** — a small archetype-A IR: 1 governance group (Cloud ALM product w/ 4 capabilities), btp group with nested `subaccount` "Test" ⊃ `subaccount` "Production" containing one `product` (BPA, 4 capabilities) + 2 services, `cloud-tier` right (private: PCE chip), personas left, identity group top-level, 5 edges with flowFamily/pill mix.
- [ ] **Step 2: Failing tests** — v1 demos still parse (`demo/nova/nova-L{0,1,2}.json` → `Diagram` without error, field defaults None); v2 fixture parses with capabilities list; `validate-ir.py` CLI: exit 0 + "OK" on both; exit 2 with message listing allowed values on a crafted bad IR (`"flowFamily": "identiy"` → error text contains `identity`).
- [ ] **Step 3: Implement** dataclass fields + parse; `validate-ir.py` walks the parsed IR and re-checks enums/references (group refs exist, parent cycles, capability shape), printing `ERROR <where>: <what>. Allowed: <values>`.
- [ ] **Step 4: Tests PASS. Commit** `feat(ir): IR v2 — subaccounts, products, tiers, flow families, branding`.

### Task 5: molecule emission

**Files:**
- Create: `scripts/_molecules.py`, `tests/test_molecules.py`
- Modify: `scripts/generate-drawio.py` (`_group_style`/`_node_style`/emit vertex paths)

`_molecules.py` public API (each returns a list of cell dicts `{id, value, style, x, y, w, h, parent}` in PARENT-RELATIVE coords; caller offsets):

```python
def load_contract() -> dict
def product_box(node, contract, icon_resolver) -> list[dict]      # box + title row + capability chips grid
def custom_app_box(group, contract) -> list[dict]                 # frame + runtime badge slot
def subaccount_frame(group, contract) -> list[dict]               # frame + "SAP BTP" chip + hyperscaler/runtime badges
def tier_box(group, contract) -> list[dict]                       # public|private|any-premise + brand chips
def persona(node, contract, icon_resolver) -> list[dict]
def pill(edge, contract) -> dict                                  # protocol pill vertex (positioned by router)
def step_circle(node, contract) -> dict
def network_separator(x, y0, y1, contract) -> list[dict]
def branding_block(metadata, contract, brand_packs) -> list[dict] # customer logo + title (+ watermark cell)
def badge(kind, name, contract, brand_packs) -> dict              # image badge or text-chip fallback
```

- [ ] **Step 1: Failing tests** — build a product node with 3 capabilities → cells: 1 box (contract `product-box` style) + 1 title + 3 chips (`capability-chip` style), every chip bbox inside box bbox with `geometry.padX` margins; `badge("hyperscaler","aws",…)` with empty brand packs → text-chip cell whose value == "AWS" (fallback path); `subaccount_frame` includes a chip cell with `image=@sap-btp-chip` resolved from brand pack (or placeholder text if absent); no `fillColor=#` literal in `_molecules.py` (Task 2 guard now bites).
- [ ] **Step 2: Implement**; wire into `generate-drawio.py`: group type `subaccount|governance|cloud-tier|custom-app` → molecule frames; node type `product` → `product_box`; `db`/`chip` → contract styles; edge `flowFamily` → edge style from contract (replacing per-style literals for the new families); `pill` cells created but positioned (0,0) until the router (Task 8e) places them.
- [ ] **Step 3: Golden mini-test** — generate `tests/fixtures/ir-v2-sample.json` → parse output XML: every emitted style string that came from a molecule matches the contract byte-for-byte (assert via substring `style.startswith(contract_style)`).
- [ ] **Step 4: Tests PASS. Commit** `feat(engine): molecule emission from the style contract`.

---

## Phase 3 — skeleton layout

### Task 6: slots + flow ordering

**Files:**
- Create: `scripts/_skeleton_layout.py`, `tests/test_skeleton_layout.py`
- Modify: `scripts/generate-drawio.py` (emit(): call skeleton instead of zone)
- Delete (end of task): `scripts/_zone_layout.py` (move `_pack`, `_text_w`, `_footprint`, `icon_size` into `_skeleton_layout.py` unchanged)

Slot model:

```python
SLOTS = ("branding", "left", "top", "center", "right", "bottom")
# assignment: personas/user→left · governance→top · btp-layer/subaccount(top-level)→center
# cloud-tier/sap-app/non-sap/third-party/external→right · legend/caption→bottom
# identity: group whose nodes resolve to Cloud Identity Services family →
#   parented to btp group ⇒ bottom band INSIDE the btp frame; top-level ⇒ own slot just below center frame
# explicit `zone`/`position` still override, as today
```

Flow ordering inside each lane: compute `rank(node)` = longest-path depth in the full edge DAG (cycles broken at back-edges by IR order); sort each lane's siblings by `(rank, ir_index)`; edge-less nodes keep `ir_index` after ranked ones.

- [ ] **Step 1: Failing tests**

```python
# tests/test_skeleton_layout.py (core assertions)
def test_columns_nova():   # nova-L1: personas left of btp, tiers right, deterministic
    lay1, lay2 = compute(nova), compute(nova)
    assert lay1 == lay2
    assert max(x_right(lay1, n) for n in PERSONAS) < box(lay1, "btp").x
    assert box(lay1, "sap-cloud").x > box(lay1, "btp").x + box(lay1, "btp").w

def test_flow_order_in_lane():  # is(rank0) → cap(rank1) → fiori: x(is) < x(cap)
def test_nested_subaccounts():  # v2 fixture: prod frame fully inside test frame, both inside btp
def test_identity_slot_by_parent():  # parented→inside btp bbox; top-level→below btp, same column
def test_governance_above_center()
```

- [ ] **Step 2: Implement `_skeleton_layout.compute_layout(diagram, shape_index)`** returning the same dict shape as before (`groups/nodes/edges/canvas`) + new `"meta": {"slots": {...}, "lanes": {...}}` consumed by the router. Measurement reuses `_pack`/`_footprint`; product/tier molecules get footprints from contract geometry.
- [ ] **Step 3: Wire into `emit()`**, delete `_zone_layout.py`, fix imports (`icon_size` now from `_skeleton_layout`). Run FULL suite + `python3 scripts/generate-drawio.py demo/nova/nova-L1.json --out /tmp/n1.drawio` → generates.
- [ ] **Step 4: Commit** `feat(layout): skeleton slot layout with flow ordering (replaces zone layout)`.

### Task 7: NETWORK separator + branding placement

- [ ] **Step 1: Failing tests** — layout meta contains separator segment with `center.x_max < sep.x < right.x_min` spanning the right stack's y-range when a `cloud-tier`/backend group exists; `metadata: {"networkSeparator": false}` removes it; branding block at top-left before title; watermark cell centered, `opacity=` from contract.
- [ ] **Step 2: Implement** in `_skeleton_layout` (position) + `_molecules.network_separator`/`branding_block` emission in generator.
- [ ] **Step 3: Tests PASS. Commit** `feat(layout): NETWORK separator + branding/watermark slots`.

---

## Phase 4 — channel router

### Task 8: `scripts/_channel_router.py` (five sub-milestones, one commit each)

**Files:** Create `scripts/_channel_router.py`, `scripts/_geom_checks.py`, `tests/test_router.py`, `tests/test_geom_checks.py`. Modify `generate-drawio.py` (use router waypoints; write `<Array as="points">` mxPoints; keep `exitX/entryX` from router ports).

Data model:

```python
@dataclass
class Channel:            # a rectangular corridor between slots
    id: str               # "v:left-center", "h:governance-center", "ring:top"…
    axis: str             # "v" | "h"
    rect: Rect            # reserved space (width grows: base 24 + 12*lanes_used, from contract geometry)
    lanes: dict[str,int]  # edge_id → lane index (offset = (i - (n-1)/2) * 12)

def route(diagram, layout) -> RouteResult:
    # RouteResult.waypoints: edge_id → [(x,y)…]   RouteResult.ports: edge_id → (exitXY, entryXY)
    # RouteResult.pill_pos / label_pos: edge_id → (x,y)   RouteResult.channels: [Channel]
```

- [ ] **8a — region graph + channel assignment.** Failing tests: adjacent-column edge uses exactly the shared vertical gutter (waypoints' x within gutter rect); left→right long edge crosses both gutters via one horizontal corridor; same input twice → identical result. Implement: build channels from `layout["meta"]["slots"]` gaps (the skeleton already reserves ZONE_HGAP-style gaps; router owns their widths), BFS over region adjacency for the segment sequence. Commit `feat(router): region graph + deterministic channel assignment`.
- [ ] **8b — lane offsets.** Failing test: 5 edges through one gutter → 5 distinct offsets, pairwise segment distance ≥ 10px, no two polylines share a segment. Implement per-channel lane allocator (sort by (src.y, dst.y, id) → stable). Commit.
- [ ] **8c — port distribution.** Failing test: 3 edges leaving one box's right side get distinct `exitY` fractions ordered by target y; entry side chosen facing the last segment. Implement per-side barycenter spread (fractions evenly in [0.25, 0.75]). Commit.
- [ ] **8d — waypoint emission.** Failing test: generated `.drawio` for the v2 fixture has, for EVERY edge, `<Array as="points">` with ≥1 `<mxPoint>` matching router output ±1px, plus `exitX/exitY/entryX/entryY` in style; draw.io re-open sanity via `validate-drawio.py` (0 CRITICAL). Implement in `emit()` (replace the empty `zone_result["edges"]` path — the hook already exists at generate-drawio.py:1530). Commit.
- [ ] **8e — pill & label slots + geometry predicates.** First implement `_geom_checks.py`: `rects_overlap(a,b,pad=0)`, `seg_intersects_rect(p,q,rect)`, `segments_cross(p1,q1,p2,q2)`, `point_in_rect` — each with direct unit tests (`tests/test_geom_checks.py`, include collinear/touching edge cases). Then failing router tests: pill/label positions on the edge's longest segment midpoint, shifted along the channel to the first free slot; assert NO pill/label rect overlaps any node/box/other pill/label rect and no label rect is crossed by a foreign edge segment (use `_geom_checks`). Implement slot grid per channel (slot pitch = pill height + 6). Commit `feat(router): collision-free pill and label slots + shared geometry predicates`.

### Task 9: crossing reduction + budget

- [ ] **Step 1: Failing test** — crafted 4-edge "X" case: without ordering ≥2 crossings, router output ≤1; `route(...).crossings` int exposed; nova-L1 end-to-end crossings ≤ 6 (budget; tune after visual check but pin a number).
- [ ] **Step 2: Implement** greedy lane-order swap pass (bubble until no improvement, max 3 sweeps — deterministic).
- [ ] **Step 3: Commit** `feat(router): greedy crossing reduction with exposed budget metric`.

---

## Phase 5 — pure renderer

### Task 10: `scripts/_pure_render.py`

**Files:** Create `scripts/_pure_render.py`, `tests/test_pure_render.py`.

Renders OUR vocabulary only, from the emitted `.drawio` XML: rounded rects (absoluteArcSize honored), dash patterns (dashed 6-4, dotted 2-3 scaled), polylines + `blockThin` arrowheads (endSize 4 → triangle), ellipses, pills, text (PIL `ImageFont.truetype(DejaVuSans, size)`; horizontal/vertical align per style; `fontStyle=1` → bold variant), images (data-URIs + `@atlas:` names → `assets/icon-atlas/`), watermark opacity. CLI: `python3 scripts/_pure_render.py in.drawio --out out.png --scale 2`.

- [ ] **Step 1: Failing tests** — render the v2 fixture output: PNG size == canvas×scale; pixel at title (x+4,y+4 of title cell) ≈ `#0070F2` (±20/channel); pixel on btp frame border ≈ `#0070F2`; icon region non-empty (stddev > 0); a dashed edge row has alternating background pixels; missing-atlas icon → grey placeholder circle, exit 0 with WARN on stderr.
- [ ] **Step 2: Implement** (Pillow import guarded: exit 3 "pip install pillow" if absent — preflight recommended-item added).
- [ ] **Step 3: Tests PASS. Commit** `feat(render): pure-Python PNG renderer for the emitted vocabulary`.

### Task 11: `render-preview.py --engine auto|drawio|pure`

- [ ] **Step 1: Failing test** — `--engine pure` produces a PNG with draw.io PATH hidden (`env PATH=/usr/bin:/bin`); `--engine auto` picks drawio when the binary resolves, else pure (test by monkeypatching the finder).
- [ ] **Step 2: Implement** (default `auto`; keep current graceful-skip ONLY for `--engine drawio` explicitly).
- [ ] **Step 3: Commit** `feat(render): engine auto-selection — drawio when present, pure otherwise`.

---

## Phase 6 — gate, rubric, SKILL loop

### Task 12: geometric gate v2

**Files:** Modify `scripts/check-composition.py`; Create `tests/test_gate.py`, `tests/fixtures/bad-nova-L1.drawio` (generate once from current `main` engine BEFORE this branch's layout lands — `git stash` not needed: regenerate via `git show main:scripts/_zone_layout.py` copy in tmp, or simply commit the pre-existing bad output captured in scratch during the brainstorm).

New FAIL-blocking checks (reuse `_geom_checks`): `EDGE_CROSS_BUDGET` (crossings > budget from metadata or default 8), `EDGE_THROUGH_BOX` (segment intersects a non-endpoint node/group rect), `TEXT_OVERLAP` (any two text-bearing cell rects), `CAPTION_OUT` (node caption outside its parent frame), `PILL_COLLISION`, `PORT_CONGESTION` (two edges same side same fraction), `CHANNEL_DISCIPLINE` (edge segment outside every channel rect ± tolerance — requires channels serialized into the XML as an invisible metadata cell `sapdp:channels` JSON; add that to emit in this task).

- [ ] **Step 1: Failing tests** — `bad-nova-L1.drawio` → ≥3 distinct FAIL codes and exit code 1; freshly generated nova-L1 → 0 FAIL exit 0.
- [ ] **Step 2: Implement**; ensure `sys.exit(1)` on any FAIL (verify current behavior, fix if only prints).
- [ ] **Step 3: Commit** `feat(gate): geometric FAIL checks — crossings, overlaps, containment, channel discipline`.

### Task 13: visual rubric + patch application

**Files:** Create `skills/sap-diagram-generate/references/visual-rubric.md`, `scripts/apply-rubric-patches.py`, `tests/test_rubric_patches.py`.

Rubric: ~25 binary checks in 4 groups (composition / routing / typography / semantics), EACH row: `id | look at | pass criterion | patch` where patch ∈ mechanical vocabulary:

```json
{"op": "set_group_flow",   "group": "btp-core", "value": "row"}
{"op": "set_zone",         "group": "ops",      "value": "right"}
{"op": "order_override",   "group": "btp-core", "value": ["is","cap","fiori"]}
{"op": "nudge_label",      "edge": "e12",       "value": "next-slot"}
{"op": "channel_prefer",   "edge": "e15",       "value": "h:center-bottom"}
{"op": "set_icon_size",    "value": "S|M|L"}
{"op": "toggle_separator", "value": true}
```

Patches land in `diagram.layoutHints[]` (IR v2 field from Task 4); layout/router consume them (order_override beats rank sort; channel_prefer beats BFS choice — wire both, small changes).

- [ ] **Step 1: Failing tests** — applying a findings JSON adds hints; re-applying same findings is idempotent; unknown op → exit 2 listing vocabulary; `order_override` changes sibling order in layout.
- [ ] **Step 2: Implement script + hint consumption; write the rubric doc** (checks derived from the corpus analysis in the spec: subaccount frame present when >3 BTP services; flow reads L→R; identity placement; pills on lines; no text collisions; NETWORK bar when tiers exist; palette discipline; Helvetica only; personas frameless; badges corners; watermark subtle…). Each check names its patch op.
- [ ] **Step 3: Commit** `feat(rubric): visual rubric + mechanical patch vocabulary (layoutHints)`.

### Task 14: SKILL loop rewrite

**Files:** Modify `skills/sap-diagram-generate/SKILL.md` (Step 8), `agents/diagram-architect.md`, `skills/sap-diagram-generate/references/interactive-workflow.md`.

- [ ] **Step 1: Rewrite Step 8** as: `validate-drawio` → `check-composition` (FAIL ⇒ fix IR, regenerate — max 2) → `render-preview --engine auto` → **Read the PNG** → evaluate EVERY rubric check → findings JSON → `apply-rubric-patches.py` → regenerate → re-render; ≤3 vision iterations; deliver only when gate+rubric green, with a scorecard table (gate results, rubric pass count, crossings, iterations) in the final report; explicit user override allowed and logged.
- [ ] **Step 2: Manual dry-run** of the SKILL text against the v2 fixture (follow the steps by hand once) — confirm no dead references, commands exist.
- [ ] **Step 3: Commit** `docs(skill): Step 8 = visual gate loop with rubric findings and patches`.

---

## Phase 7 — the diagram exam + CI + bundle

### Task 15: regenerate demos

- [ ] Regenerate `demo/nova/nova-L{0,1,2}.drawio` + PNGs via the full new pipeline (including one rubric loop pass); gate green on all; commit `chore(demo): regenerate NOVA demos with the new engine`.

### Task 16: gold replica exam

- [ ] Author `demo/replicas/task-center-L1.json` (IR transcription of the gold's inventory: Build Work Zone → Task Center → Destination + Connectivity, identity inside BTP, 3 right tiers, Task Data pills, SAML2/OIDC green edge); generate; render side-by-side with `~/tools/btp-solution-diagrams/assets/editable-diagram-examples/SAP_Task_Center_L1.drawio`; iterate rubric until family-indistinguishable; commit IR + output `feat(demo): SAP_Task_Center_L1 replica (gold fidelity exam)`.

### Task 17: Brandart replica exam (LOCAL only)

- [ ] Author inventory-only IR at `~/.cache/sap-diagrams-pro/exams/brandart-L1.json` (NOT in repo); generate with `assets/brand-pack.local`; produce side-by-side PNG pair in the same folder; **stop and request Gabriele's visual approval** (this is the human acceptance gate). Nothing committed.

### Task 18: CI golden tests

- [ ] Extend `.github/workflows/engine-smoke-test.yml`: `pip install pytest pillow` → `python3 -m pytest tests -q` → generate nova-L1 + task-center replica → `validate-drawio.py` + `check-composition.py` (exit-code enforced) → `render-preview.py --engine pure` smoke. No draw.io, no LLM, no OneDrive access in CI (skip markers for dev-only tests: `@pytest.mark.devonly` + `-m "not devonly"` in CI). Commit `ci: golden generate + geometric gate + pure-render smoke`.

### Task 19: bundle + docs + memory

- [ ] Update `packaging/claude-desktop-skill/build.sh` manifest: add `_molecules.py`, `_skeleton_layout.py`, `_channel_router.py`, `_geom_checks.py`, `_pure_render.py`, `apply-rubric-patches.py`, `validate-ir.py`, `assets/style-contract.json`, `assets/brand-pack/`, `assets/icon-atlas/`, rubric reference; EXCLUDE `brand-pack.local`. Rebuild zip. Update `README.md`, `CHANGELOG.md`, `skills/sap-diagram-generate/references/*` touched by renames. Update the project memory file (`sap-diagrams-pro-quality-overhaul.md`) with the new pipeline + exam corpus. Commit `feat(dist): Desktop bundle with the perfect-diagrams engine`.

---

## Definition of done (from the spec)

Geometric gate + rubric green on exams 15/16/17 **+ Gabriele's visual approval on all three**, CI green, Desktop bundle rebuilt. Then merge via PR per repo flow.
