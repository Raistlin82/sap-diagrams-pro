# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""tests/test_engine_fixes.py — the four engine-level visual fixes the diagram
exam surfaced (recur on every diagram, not per-diagram):

  * FIX-1 — the auto-legend must not overlap any placed zone/group rect (it used
    to be pushed up into the last right-column zone when tall). Proven by
    generating the nova demos and checking the legend rect against every group
    rect, plus a drift guard that the skeleton's reserved legend height matches
    what the emitter draws.
  * FIX-2 — step-number badges must not cover a group title band or a node's
    interface pill.
  * FIX-3 — a governance frame carries the "SAP BTP" chip (subaccount-style);
    an identity group (Cloud Identity Services / a btp-layer holding identity)
    does NOT — while a real subaccount/btp-layer keeps its chip.
  * FIX-4 — an unresolved runtime badge (e.g. cloud-foundry with no brand asset)
    degrades to the SAME text-chip fallback + WARNING as a hyperscaler badge,
    never a fuzzy icon-glyph match.
"""
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from conftest import load_script

ROOT = Path(__file__).resolve().parent.parent
NOVA_L0 = ROOT / "demo" / "nova" / "nova-L0.json"
NOVA_L1 = ROOT / "demo" / "nova" / "nova-L1.json"
V2_FIXTURE = ROOT / "tests" / "fixtures" / "ir-v2-sample.json"

# Title band height where a zone/group's title text sits (matches
# check-composition.ZONE_HEADER_H; verticalAlign=top, fontSize=14).
ZONE_HEADER_H = 30.0
_GROUP_ID_RE = re.compile(r"^g-[0-9a-f]{8}$")


@pytest.fixture(scope="module")
def gen():
    return load_script("generate-drawio")


@pytest.fixture(scope="module")
def sl():
    return load_script("_skeleton_layout")


@pytest.fixture(scope="module")
def M():
    return load_script("_molecules")


@pytest.fixture(scope="module")
def contract(M):
    return M.load_contract()


def _emit_root(gen, path_or_ir):
    ir = path_or_ir
    if isinstance(path_or_ir, Path):
        ir = json.loads(path_or_ir.read_text(encoding="utf-8"))
    return ET.fromstring(gen.emit(gen.parse_json(ir), layout="auto"))


def _cells_by_id(root):
    return {c.get("id"): c for c in root.iter("mxCell")}


def _abs_rect(cid, cells):
    """Absolute (x, y, w, h) of a cell, resolving its parent chain."""
    c = cells.get(cid)
    if c is None:
        return None
    g = c.find("mxGeometry")
    if g is None or g.get("x") is None:
        return None
    x, y = float(g.get("x")), float(g.get("y"))
    w, h = float(g.get("width", 0)), float(g.get("height", 0))
    p = c.get("parent")
    while p and p in cells and p != "1":
        pg = cells[p].find("mxGeometry")
        if pg is not None and pg.get("x") is not None:
            x += float(pg.get("x"))
            y += float(pg.get("y"))
        p = cells[p].get("parent")
    return (x, y, w, h)


def _overlap_area(a, b):
    ox = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    oy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    return ox * oy


# ─────────────────────────────────────────────────────────────────────────────
# FIX-1 — legend does not overlap any zone/group rect
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("demo", [NOVA_L0, NOVA_L1])
def test_legend_does_not_overlap_any_group(gen, demo):
    root = _emit_root(gen, demo)
    cells = _cells_by_id(root)
    legends = [cid for cid in cells if cid and cid.startswith("legend-")]
    assert legends, f"{demo.name}: expected an auto-legend box"
    groups = [cid for cid in cells
              if cid and _GROUP_ID_RE.match(cid) and cells[cid].get("vertex") == "1"]
    assert groups, f"{demo.name}: expected group frames to test against"
    for lid in legends:
        lr = _abs_rect(lid, cells)
        for gid in groups:
            gr = _abs_rect(gid, cells)
            if gr is None:
                continue
            assert _overlap_area(lr, gr) == 0.0, (
                f"{demo.name}: legend {lr} overlaps group {gid} {gr}")


def test_skeleton_reserves_legend_height_the_emitter_draws(gen, sl):
    """Drift guard: the skeleton's reserved legend footprint uses the SAME row
    selection + height formula the emitter draws with, so the reserved bottom
    band and the drawn legend box line up (that alignment is what keeps the
    legend clear of content)."""
    ir = json.loads(NOVA_L1.read_text(encoding="utf-8"))
    diagram = gen.parse_json(ir)
    fp = sl.legend_footprint(diagram)
    assert fp is not None, "nova-L1 uses several line styles → a legend"
    _w, reserved_h = fp
    root = _emit_root(gen, NOVA_L1)
    cells = _cells_by_id(root)
    (lid,) = [cid for cid in cells if cid and cid.startswith("legend-")]
    drawn_h = float(cells[lid].find("mxGeometry").get("height"))
    assert reserved_h == drawn_h, (
        f"reserved legend height {reserved_h} != drawn {drawn_h}")
    # And the row count matches the emitted label rows.
    n_labels = sum(1 for cid in cells if cid and cid.startswith("leglbl-"))
    assert sl.legend_row_count(diagram) == n_labels


# ─────────────────────────────────────────────────────────────────────────────
# FIX-2 — step badges clear group titles and interface pills
# ─────────────────────────────────────────────────────────────────────────────
def test_step_badges_clear_titles_and_pills(gen):
    root = _emit_root(gen, NOVA_L1)
    cells = _cells_by_id(root)
    steps = [cid for cid in cells if cid and cid.startswith("st-")]
    assert steps, "nova-L1 carries numbered step badges"
    groups = [cid for cid in cells
              if cid and _GROUP_ID_RE.match(cid) and cells[cid].get("vertex") == "1"]
    pills = [cid for cid in cells if cid and cid.startswith("if-")]
    for s in steps:
        sr = _abs_rect(s, cells)
        for gid in groups:
            gr = _abs_rect(gid, cells)
            if gr is None:
                continue
            title_band = (gr[0], gr[1], gr[2], ZONE_HEADER_H)
            assert _overlap_area(sr, title_band) == 0.0, (
                f"step badge {s} {sr} covers title band of {gid} {title_band}")
        for pid in pills:
            pr = _abs_rect(pid, cells)
            if pr is None:
                continue
            assert _overlap_area(sr, pr) == 0.0, (
                f"step badge {s} {sr} covers interface pill {pid} {pr}")


# ─────────────────────────────────────────────────────────────────────────────
# FIX-3 — governance chip present; identity chip absent
# ─────────────────────────────────────────────────────────────────────────────
def test_governance_strip_carries_btp_chip(M, contract):
    g = NS(id="gov", label="Governance", badges=None)
    with_chip = M.governance_strip(g, contract, show_chip=True)
    chips = [c for c in with_chip if c["id"] == "btpchip"]
    assert chips and chips[0]["value"] == "BTP"  # logo reads "SAP", text reads "BTP"
    # title sits to the RIGHT of the chip (one header line)
    title = [c for c in with_chip if c["id"] == "frame-title"][0]
    assert title["x"] >= chips[0]["x"] + chips[0]["w"]
    # suppressible
    no_chip = M.governance_strip(g, contract, show_chip=False)
    assert not any(c["id"] == "btpchip" for c in no_chip)


def test_governance_frame_has_chip_in_emitted_diagram(gen):
    root = _emit_root(gen, V2_FIXTURE)
    gov_frame = gen._stable_id("g", "governance")
    chip_ids = [el.get("id") for el in root.iter("mxCell")
                if (el.get("id") or "").startswith(gov_frame)
                and (el.get("id") or "").endswith("-btpchip")]
    assert chip_ids, "governance frame must stamp a SAP BTP chip (FIX-3)"


def test_identity_group_has_no_btp_chip(gen):
    root = _emit_root(gen, V2_FIXTURE)
    identity_cell = gen._stable_id("g", "identity")   # a btp-layer identity group
    btp = gen._stable_id("g", "btp")                  # the real btp-layer container
    badges_on = {}
    for el in root.iter("mxCell"):
        cid = el.get("id") or ""
        if "btpbadge" in cid or cid.endswith("-btpchip"):
            badges_on.setdefault(el.get("parent"), []).append(cid)
    assert identity_cell not in badges_on, (
        f"identity group must NOT carry a SAP BTP chip; got {badges_on.get(identity_cell)}")
    # …but the real btp-layer container still does (chip-once rule intact).
    assert btp in badges_on, "the non-identity btp-layer must keep its SAP BTP badge"


# ─────────────────────────────────────────────────────────────────────────────
# FIX-4 — runtime badge is ALWAYS a text chip (never a wordmark image)
#
# Runtime brand assets (Cloud Foundry, Kyma) are wide wordmark logos that squish
# into an illegible blob at the 32px runtime-badge size. The engine therefore
# renders runtimes as a deterministic SAP-blue text chip — the intended form,
# not a degradation — so it produces NO warning and ignores any pack image.
# ─────────────────────────────────────────────────────────────────────────────
def _fuzzy_resolver(_key):
    # Simulates the icon resolver fuzzy-matching ANY key to some glyph.
    return "data:image/png,FUZZYGLYPH"


@pytest.mark.parametrize("name,label", [
    ("cloud-foundry", "Cloud Foundry"),
    ("kyma", "Kyma"),
])
def test_runtime_badge_is_text_chip(M, contract, name, label):
    warns = []
    cell = M.badge("runtime", name, contract, {}, _fuzzy_resolver, warns)
    # NOT the fuzzy glyph — the deterministic text chip with the friendly label.
    assert "FUZZYGLYPH" not in cell.get("style", "")
    assert cell.get("value") == label
    assert cell["style"].startswith(M._fallback_chip_style(contract))
    # It is the intended form, not a fallback: no WARNING is emitted.
    assert not warns, f"runtime chip must not warn, got {warns}"


def test_hyperscaler_still_degrades_to_fallback_with_warning(M, contract):
    """Hyperscaler badges keep the image-with-text-fallback behaviour: an empty
    pack degrades AWS to a bordered text chip + a WARNING. (Contrast the runtime
    chip above, which is the intended form and never warns.)"""
    w_aws = []
    aws = M.badge("hyperscaler", "aws", contract, {}, _fuzzy_resolver, w_aws)
    assert aws["style"].startswith(M._fallback_chip_style(contract))
    assert w_aws


def test_runtime_badge_ignores_pack_image(M, contract):
    """Even when the brand pack DOES carry the CF asset (cf-badge alias), the
    runtime badge stays a text chip — the wordmark image is illegible at badge
    size, so it is deliberately never used."""
    packs = {"cf-badge": {"dataUri": "data:image/png,REALCF"}}
    warns = []
    cell = M.badge("runtime", "cloud-foundry", contract, packs, _fuzzy_resolver, warns)
    assert "REALCF" not in cell.get("style", "")
    assert cell["style"].startswith(M._fallback_chip_style(contract))
    assert cell.get("value") == "Cloud Foundry"
    assert not warns
