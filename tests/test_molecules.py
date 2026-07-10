# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""tests/test_molecules.py — Task 5 (molecule emission from the style contract).

Two families:
  * Unit — the molecule builders in scripts/_molecules.py assemble cell dicts
    whose styles come byte-for-byte from assets/style-contract.json, with the
    placeholder resolution + text-badge fallback behaving per the Layer-3 spec.
  * Golden — the fixture tests/fixtures/ir-v2-sample.json rendered through
    generate-drawio.emit() carries, in its XML, molecule styles that match the
    contract byte-for-byte (asserted as a style-prefix, which is exact for the
    box/frame/chip/edge molecules and prefix-before-image= for image ones).
"""
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from conftest import load_script

ROOT = Path(__file__).resolve().parent.parent
V2_FIXTURE = ROOT / "tests" / "fixtures" / "ir-v2-sample.json"


@pytest.fixture(scope="module")
def M():
    return load_script("_molecules")


@pytest.fixture(scope="module")
def contract(M):
    return M.load_contract()


def _style(contract, name):
    return contract["molecules"][name]["style"]


def _style_tokens(style):
    return {
        key: value
        for token in filter(None, style.split(";"))
        for key, sep, value in [token.partition("=")]
        if sep
    }


# ─────────────────────────────────────────────────────────────────────────────
# load_contract / load_brand_packs
# ─────────────────────────────────────────────────────────────────────────────
def test_load_contract_has_required_molecules(contract):
    for name in ("product-box", "capability-chip", "subaccount-frame",
                 "tier-box-sap", "tier-box-nonsap", "custom-app-box", "db", "chip",
                 "edge-identity", "edge-firewall", "sap-btp-chip"):
        assert name in contract["molecules"]


def test_load_brand_packs_returns_public_asset(M):
    packs = M.load_brand_packs()
    # The public (committed) pack always contributes the SAP logo chip; the
    # .local pack may be absent (CI / Desktop) — never a failure.
    assert "sap-logo-chip" in packs
    assert packs["sap-logo-chip"]["dataUri"].startswith("data:image/")


def test_no_style_literals_in_molecules_source():
    # Belt-and-braces with tests/test_style_contract.py's guard.
    src = (ROOT / "scripts" / "_molecules.py").read_text(encoding="utf-8")
    assert "fillColor=#" not in src
    assert "strokeColor=#" not in src


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — product box: 1 box + 1 title + N capability chips, chips inside the
# box with product-box.padX margins on all sides.
# ─────────────────────────────────────────────────────────────────────────────
def test_product_box_box_title_and_three_chips(M, contract):
    node = NS(id="bpa", label="Build Process Automation", service="X",
              type="product", capabilities=[
                  {"label": "Workflow", "icon": "workflow"},
                  {"label": "Decision"},
                  {"label": "RPA", "icon": "rpa"},
              ])
    cells = M.product_box(node, contract, icon_resolver=lambda n: None)

    box = cells[0]
    titles = [c for c in cells if c["id"] == "title"]
    chips = [c for c in cells if c["id"].startswith("chip")]

    # exactly 1 box + 1 title + 3 chips
    assert len(cells) == 5
    assert len(titles) == 1 and len(chips) == 3

    assert box["style"].startswith(_style(contract, "product-box"))
    assert titles[0]["style"].startswith(_style(contract, "title-block"))
    assert titles[0]["value"] == "Build Process Automation"
    for ch in chips:
        assert ch["style"].startswith(_style(contract, "capability-chip"))

    pad_x = contract["molecules"]["product-box"]["geometry"]["padX"]
    eps = 1e-6
    for ch in chips:
        assert ch["x"] >= pad_x - eps, "chip breaches left padX margin"
        assert ch["x"] + ch["w"] <= box["w"] - pad_x + eps, "chip breaches right padX margin"
        assert ch["y"] >= pad_x - eps, "chip breaches top padX margin"
        assert ch["y"] + ch["h"] <= box["h"] - pad_x + eps, "chip breaches bottom padX margin"


def test_product_box_chip_embeds_resolved_icon(M, contract):
    node = NS(id="p", label="P", service="X", type="product",
              capabilities=[{"label": "Monitor", "icon": "monitor"}])
    uri = "data:image/svg+xml,PHN2Zz48L3N2Zz4="
    cells = M.product_box(node, contract, icon_resolver=lambda n: uri)
    chip = [c for c in cells if c["id"].startswith("chip")][0]
    # capability-chip style stays the verbatim prefix; the resolved icon is appended.
    assert chip["style"].startswith(_style(contract, "capability-chip"))
    assert f"image={uri}" in chip["style"]
    assert chip["value"] == "Monitor"


def test_product_box_icon_chip_stacks_icon_above_label_no_overlap(M, contract):
    """Regression for the icon/label-overlap defect: an icon-bearing capability
    chip must position its icon top-centered and its label BELOW it (stacked,
    not both centered on top of each other), and must NOT keep the contract's
    base 64x64 imageWidth/imageHeight (measured off the whole SSAM panel, not
    a per-icon footprint) -- that oversized icon is what swallowed the chip
    and sat on top of the label. A sibling text-only chip (no icon resolved)
    must be completely unaffected -- still the bare contract style, centered
    text, no image/vertical-label markers at all."""
    node = NS(id="p", label="P", service="X", type="product",
              capabilities=[{"label": "Decision", "icon": "decision"},
                            {"label": "Implementation"}])
    uri = "data:image/svg+xml,PHN2Zz48L3N2Zz4="
    cells = M.product_box(node, contract, icon_resolver=lambda n: uri if n == "decision" else None)
    chips = {c["value"]: c for c in cells if c["id"].startswith("chip")}
    icon_chip, text_chip = chips["Decision"], chips["Implementation"]

    chip_geo = contract["molecules"]["capability-chip"]["geometry"]
    icon_w, icon_h = chip_geo["iconW"], chip_geo["iconH"]

    # Icon-bearing chip: stacks icon-top (imageVerticalAlign=top) above a
    # bottom-anchored label (verticalAlign=bottom), sized to the real
    # per-icon footprint -- not the base style's whole-panel 64x64.
    assert "imageVerticalAlign=top" in icon_chip["style"]
    assert "verticalAlign=bottom" in icon_chip["style"]
    assert f"imageWidth={icon_w:g}" in icon_chip["style"]
    assert f"imageHeight={icon_h:g}" in icon_chip["style"]
    # The base contract style's own imageWidth=64/imageHeight=64 (a whole-panel
    # measurement -- see _capability_grid_geometry's docstring) is still
    # present verbatim (it's part of the required contract-style prefix,
    # asserted below), but the real per-icon override MUST come after it in
    # the ``;``-delimited style string so the last-write-wins mxgraph/pure-
    # renderer style parser actually uses 32x32, not 64x64, for the icon box.
    assert icon_chip["style"].index(f"imageWidth={icon_w:g}") > icon_chip["style"].index("imageWidth=64")
    assert icon_chip["style"].index(f"imageHeight={icon_h:g}") > icon_chip["style"].index("imageHeight=64")

    # Text-only sibling: no image, no vertical-label-position styling at all --
    # a pure regression guard that the icon-chip fix doesn't leak onto it.
    assert "image=" not in text_chip["style"]
    assert "imageVerticalAlign" not in text_chip["style"]
    assert text_chip["style"] == _style(contract, "capability-chip")


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — badge() fallback: empty brand packs → bordered text chip "AWS".
# ─────────────────────────────────────────────────────────────────────────────
def test_badge_hyperscaler_empty_pack_text_fallback(M, contract):
    cell = M.badge("hyperscaler", "aws", contract, {})
    assert cell["value"] == "AWS"
    assert cell["style"].startswith(_style(contract, "chip"))  # bordered chip


def test_badge_runtime_empty_pack_text_fallback(M, contract):
    cell = M.badge("runtime", "cloud-foundry", contract, {})
    assert cell["value"] == "Cloud Foundry"
    assert cell["style"].startswith(_style(contract, "chip"))


def test_badge_resolves_image_when_present(M, contract):
    packs = {"aws-badge": {"dataUri": "data:image/png,AAAA"}}
    cell = M.badge("hyperscaler", "aws", contract, packs)
    assert "image=data:image/png,AAAA" in cell["style"]
    # the badge-hyperscaler image style is the verbatim prefix (up to image=)
    base = _style(contract, "badge-hyperscaler")
    assert cell["style"].startswith(base[: base.index("image=")])


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — subaccount_frame includes a SAP BTP chip carrying image=@sap-btp-chip,
# resolvable from the brand pack, else placeholder text.
# ─────────────────────────────────────────────────────────────────────────────
def test_subaccount_frame_sap_btp_chip(M, contract):
    g = NS(id="sa", label="Test", kind=None, badges=None)
    cells = M.subaccount_frame(g, contract)

    assert cells[0]["style"].startswith(_style(contract, "subaccount-frame"))
    chips = [c for c in cells if c["id"] == "btpchip"]
    assert len(chips) == 1
    chip = chips[0]
    assert "image=@sap-btp-chip" in chip["style"]
    assert chip["style"].startswith(_style(contract, "sap-btp-chip"))
    assert chip["value"] == "BTP"  # logo reads "SAP", text reads "BTP" → "SAP BTP"
    assert chip["parent"] == "frame"  # child of the frame, not the diagram


def test_subaccount_sap_btp_chip_resolves_from_brand_pack(M, contract):
    g = NS(id="sa", label="Test", kind=None, badges=None)
    chip = [c for c in M.subaccount_frame(g, contract) if c["id"] == "btpchip"][0]
    packs = M.load_brand_packs()  # public pack has sap-logo-chip (alias target)
    resolved = M.resolve_cell(chip, packs, contract)
    assert "data:image/svg" in resolved["style"]
    assert "@sap-btp-chip" not in resolved["style"]


def test_subaccount_sap_btp_chip_strips_placeholder_when_absent(M, contract):
    g = NS(id="sa", label="Test", kind=None, badges=None)
    chip = [c for c in M.subaccount_frame(g, contract) if c["id"] == "btpchip"][0]
    resolved = M.resolve_cell(chip, {}, contract)  # empty pack
    assert "@sap-btp-chip" not in resolved["style"]
    assert resolved["style"].startswith(_style(contract, "sap-btp-chip"))
    assert resolved["value"] == "BTP"  # degrades to the text chip ("SAP" is the logo)


def test_subaccount_frame_emits_badge_slots(M, contract):
    g = NS(id="sa", label="Prod", kind=None,
           badges={"hyperscalers": ["aws"], "runtimes": ["kyma"]})
    cells = M.subaccount_frame(g, contract)
    ids = [c["id"] for c in cells]
    assert "badge-hyperscaler-aws" in ids
    assert "badge-runtime-kyma" in ids


# ─────────────────────────────────────────────────────────────────────────────
# cloud-tier kind → box variant (public/private = SAP blue, any-premise = grey).
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("kind,molecule", [
    ("public", "tier-box-sap"),
    ("private", "tier-box-sap"),
    ("any-premise", "tier-box-nonsap"),
])
def test_tier_box_variant_by_kind(M, contract, kind, molecule):
    g = NS(id="t", label="Tier", kind=kind, badges=None)
    cells = M.tier_box(g, contract)
    assert cells[0]["style"].startswith(_style(contract, molecule))


def test_tier_box_brand_chips(M, contract):
    g = NS(id="t", label="Private Cloud", kind="private",
           badges={"hyperscalers": ["azure"], "runtimes": ["cloud-foundry"]})
    ids = [c["id"] for c in M.tier_box(g, contract)]
    assert "badge-hyperscaler-azure" in ids
    assert "badge-runtime-cloud-foundry" in ids


@pytest.mark.parametrize("badges", [
    {"hyperscalers": ["azure"], "runtimes": ["cloud-foundry"]},
    {"runtimes": ["cloud-foundry", "kyma"]},
    {"runtimes": ["cloud-foundry"]},
])
def test_tier_box_badge_row_stays_within_frame(M, contract, badges):
    """Regression: the runtime badge renders as a ~130px text chip, so the frame
    must reserve that width. footprint() → _frame_min → _badge_row_size →
    _badge_slot_size (the shared geometry source) must account for the chip width;
    if it stale-reserved the 32px image-badge width, the chip would overflow the
    border. Exercise the PRODUCTION path: footprint sizes the frame, tier_box
    draws into that size. Every badge cell's right edge must fit the frame."""
    g = NS(id="t", label="Public Cloud", type="cloud-tier", kind="public", badges=badges)
    size = M.footprint(g, contract)            # what the layout reserves + passes down
    cells = M.tier_box(g, contract, size)
    frame_w = cells[0]["w"]
    for c in cells:
        if str(c["id"]).startswith("badge-"):
            right = c["x"] + c["w"]
            assert right <= frame_w + 0.5, (
                f"badge {c['id']} right edge {right} overflows frame width {frame_w}")


# ─────────────────────────────────────────────────────────────────────────────
# Remaining node / frame / decorative molecules.
# ─────────────────────────────────────────────────────────────────────────────
def test_db_and_chip_cells(M, contract):
    db = M.db_cell(NS(id="d", label="HANA"), contract)
    assert db["style"].startswith(_style(contract, "db"))
    assert db["value"] == "HANA" and db["parent"] is None
    chip = M.chip_cell(NS(id="c", label="PCE"), contract)
    assert chip["style"].startswith(_style(contract, "chip"))
    assert chip["value"] == "PCE"


def test_custom_app_box_frame(M, contract):
    g = NS(id="ca", label="Custom App", badges=None)
    cells = M.custom_app_box(g, contract)
    assert cells[0]["style"].startswith(_style(contract, "custom-app-box"))
    # FIX-A: the label is its OWN top-left cell, not the frame value (which
    # draw.io would middle-centre over the packed children).
    assert cells[0]["value"] == ""
    title = [c for c in cells if c["id"] == "frame-title"]
    assert title and title[0]["value"] == "Custom App"


def test_governance_strip_frame(M, contract):
    g = NS(id="gov", label="Governance", badges=None)
    cells = M.governance_strip(g, contract)
    assert cells[0]["style"].startswith(_style(contract, "governance-strip"))
    assert cells[0]["value"] == ""  # FIX-A: title is its own top-left cell
    title = [c for c in cells if c["id"] == "frame-title"]
    assert title and title[0]["value"] == "Governance"


# ─────────────────────────────────────────────────────────────────────────────
# FIX-A (Task 6 review): frame titles must be their OWN top-band cell (top-left,
# beside any chip), never the frame `value` (draw.io middle-centres that over the
# packed children). FIX-B: the "SAP BTP" chip is stamped ONLY on the outermost
# BTP container; nested subaccounts suppress it and show just their own name.
# ─────────────────────────────────────────────────────────────────────────────
def test_subaccount_frame_title_is_own_top_cell_not_frame_value(M, contract):
    g = NS(id="sa", label="Extension Test", kind=None, badges=None)
    cells = M.subaccount_frame(g, contract)
    assert cells[0]["value"] == "", "frame value must be empty (no middle-centred title)"
    title = [c for c in cells if c["id"] == "frame-title"]
    assert len(title) == 1
    t = title[0]
    assert t["value"] == "Extension Test"
    assert t["parent"] == "frame"
    assert t["style"].startswith(_style(contract, "title-block"))
    assert "align=left" in t["style"]
    assert t["y"] < 40, "title sits in the top band, not middle-centred"
    # with a chip present, the title sits to its RIGHT (chip + title on one line)
    chip = [c for c in cells if c["id"] == "btpchip"][0]
    assert t["x"] >= chip["x"] + chip["w"], "title must sit to the right of the chip"


def test_tier_box_title_is_own_top_left_cell(M, contract):
    g = NS(id="t", label="Public Cloud", kind="public", badges=None)
    cells = M.tier_box(g, contract)
    assert cells[0]["value"] == ""
    title = [c for c in cells if c["id"] == "frame-title"]
    assert title and title[0]["value"] == "Public Cloud"
    assert "align=left" in title[0]["style"]
    assert title[0]["y"] < 40


# FIX-1 (review): the frame title hugs the TOP of its reserved band. The contract
# title-block is verticalAlign=middle; every frame builder appends
# verticalAlign=top (and keeps align=left) so the title sits at the top-left, not
# floating mid-cell.
@pytest.mark.parametrize("builder,group", [
    ("subaccount_frame", NS(id="sa", label="A", kind=None, badges=None)),
    ("governance_strip", NS(id="gov", label="G", badges=None)),
    ("tier_box", NS(id="t", label="T", kind="public", badges=None)),
    ("custom_app_box", NS(id="ca", label="C", badges=None)),
])
def test_frame_title_cell_is_top_aligned(M, contract, builder, group):
    cells = getattr(M, builder)(group, contract)
    title = [c for c in cells if c["id"] == "frame-title"][0]
    assert "verticalAlign=top" in title["style"], f"{builder}: title not top-aligned"
    assert "align=left" in title["style"]


# FIX-5 (review): a long custom-app label spans the full frame width at the top,
# so the runtime badge row must sit BELOW the title band — never under the title
# text (the old row was top-anchored at y=8, overlapping a long title).
def test_custom_app_box_runtime_badges_below_title(M, contract):
    g = NS(id="ca", label="A Very Long Custom Application Title Indeed",
           badges={"runtimes": ["cloud-foundry", "kyma"]})
    cells = M.custom_app_box(g, contract)
    title = [c for c in cells if c["id"] == "frame-title"][0]
    badges = [c for c in cells if c["id"].startswith("badge-")]
    assert badges, "expected runtime badge slots"
    title_bottom = title["y"] + title["h"]
    for b in badges:
        assert b["y"] >= title_bottom, (
            f"badge {b['id']!r} at y={b['y']} overlaps the title band "
            f"(bottom {title_bottom})")


def test_subaccount_shows_chip_rule(M):
    # Outermost BTP container (top-level, or parented to a non-BTP group) shows
    # the chip; a subaccount nested in a btp-layer/subaccount suppresses it.
    assert M.subaccount_shows_chip("subaccount", None) is True
    assert M.subaccount_shows_chip("subaccount", "governance") is True
    assert M.subaccount_shows_chip("subaccount", "btp-layer") is False
    assert M.subaccount_shows_chip("subaccount", "subaccount") is False
    # FIX-3: a governance frame is a top-level BTP container → it carries the chip.
    assert M.subaccount_shows_chip("governance", None) is True
    # A btp-typed group is not a chip-bearing frame via this rule (its chip is the
    # btp-layer logo badge, emitted separately — and suppressed on identity groups).
    assert M.subaccount_shows_chip("cloud-tier", None) is False


def test_subaccount_frame_suppresses_chip_when_nested(M, contract):
    g = NS(id="sa", label="Production", kind=None, badges=None)
    with_chip = M.subaccount_frame(g, contract, show_chip=True)
    assert any(c["id"] == "btpchip" for c in with_chip)
    no_chip = M.subaccount_frame(g, contract, show_chip=False)
    assert not any(c["id"] == "btpchip" for c in no_chip)
    # the nested subaccount still shows its OWN name, at the top-left
    title = [c for c in no_chip if c["id"] == "frame-title"]
    assert title and title[0]["value"] == "Production"
    assert title[0]["x"] < 40, "no chip ⇒ title sits at the frame's top-left"


def test_persona_resolves_icon(M, contract):
    node = NS(id="u", label="IT Admin", genericIcon="user")
    uri = "data:image/svg+xml,PHN2Zz48L3N2Zz4="
    cells = M.persona(node, contract, icon_resolver=lambda n: uri)
    assert len(cells) == 1
    assert f"image={uri}" in cells[0]["style"]
    assert "@{persona}" not in cells[0]["style"]
    assert cells[0]["value"] == "IT Admin"


def test_pill_and_step_circle(M, contract):
    p = M.pill(NS(id="e1", pill="SAML2/OIDC", label="Login"), contract)
    assert p["style"].startswith(_style(contract, "pill-protocol"))
    assert p["value"] == "SAML2/OIDC" and (p["x"], p["y"]) == (0.0, 0.0)
    s = M.step_circle(NS(id="n1", step=3), contract)
    assert s["style"].startswith(_style(contract, "step-circle"))
    assert s["value"] == "3"


@pytest.mark.parametrize("edge", [
    NS(id="e1", pill="Trust", label=""),
    NS(id="e2", pill="SAML2/OIDC", label="", kind="trust"),
])
def test_pill_trust_semantics_override_harvested_green(M, contract, edge):
    tokens = _style_tokens(M.pill(edge, contract)["style"])
    assert tokens["strokeColor"] == "#CC00DC"
    assert tokens["fillColor"] == "#FFF0FA"
    assert tokens["fontColor"] == "#CC00DC"


@pytest.mark.parametrize("label", ["Authenticate", "Authentication", "SAML2/OIDC", "OIDC"])
def test_pill_authentication_semantics_stay_green(M, contract, label):
    tokens = _style_tokens(M.pill(NS(id="e1", pill=label, label=""), contract)["style"])
    assert tokens["strokeColor"] == "#188918"
    assert tokens["fillColor"] == "#F5FAE5"
    assert tokens["fontColor"] == "#188918"


@pytest.mark.parametrize("label,stroke", [
    ("Authorization", "#5D36FF"),
    ("Role", "#5D36FF"),
    ("Policy", "#5D36FF"),
    ("SCIM", "#470BED"),
])
def test_pill_authorization_semantics_are_purple(M, contract, label, stroke):
    tokens = _style_tokens(M.pill(NS(id="e1", pill=label, label=""), contract)["style"])
    assert tokens["strokeColor"] == stroke
    assert tokens["fillColor"] == "#F1ECFF"
    assert tokens["fontColor"] == stroke


def test_network_separator(M, contract):
    cells = M.network_separator(500, 100, 700, contract)
    line = [c for c in cells if c["id"] == "sep-line"][0]
    label = [c for c in cells if c["id"] == "sep-label"][0]
    assert line["style"].startswith(_style(contract, "network-separator"))
    assert line.get("edge") is True and line["h"] == 700.0 - 100.0  # y1 - y0
    assert line["points"] == [(500.0, 100.0), (500.0, 700.0)]
    assert label["style"].startswith(_style(contract, "network-separator-label"))
    # The "NETWORK" caption sits near the BOTTOM of the bar (gold standard
    # SAP_Task_Center_L1), not at its top.
    assert label["y"] > (100 + 700) / 2
    # FIX-3 (review): the caption is CENTERED on the separator x so it reads
    # INSIDE the gutter — its horizontal midpoint is the bar's x (previously it
    # was hard-left-aligned at x - label_w + 2, overhanging the center column).
    assert label["x"] + label["w"] / 2 == pytest.approx(500.0)


def test_branding_block_fallbacks(M, contract):
    meta = {"title": "Archetype A", "branding": {"customerLogo": "acme",
            "partnerWatermark": "lutech"}}
    cells = M.branding_block(meta, contract, {})  # empty packs → all fall back
    by_id = {c["id"]: c for c in cells}
    assert by_id["customer-logo"]["value"] == "ACME"
    assert by_id["customer-logo"]["style"].startswith(_style(contract, "chip"))
    assert by_id["watermark"]["value"] == "Lutech"  # text fallback (no image)
    assert by_id["brand-title"]["value"] == "Archetype A"


# ─────────────────────────────────────────────────────────────────────────────
# FIX-1 (review round): badge()/branding_block() used to silently drop
# icon_resolver/warnings (they defaulted to None), so a customer logo or
# partner watermark that fell back to a text-badge never appended a preflight
# WARNING — unlike every group-level badge slot (hyperscaler/runtime), which
# already threaded warnings through `_place_molecule`. Both now accept and
# forward icon_resolver/warnings so ALL badge/branding assets share one
# resolution + warning path.
# ─────────────────────────────────────────────────────────────────────────────
def test_branding_block_appends_warnings_when_unresolved(M, contract):
    meta = {"title": "Archetype A", "branding": {"customerLogo": "acme",
            "partnerWatermark": "lutech"}}
    warnings: list[str] = []
    M.branding_block(meta, contract, {}, icon_resolver=None, warnings=warnings)
    assert any("'acme'" in w for w in warnings), "customer logo must warn when unresolved"
    assert any("'lutech'" in w for w in warnings), "partner watermark must warn when unresolved"


def test_badge_appends_warning_when_unresolved(M, contract):
    warnings: list[str] = []
    M.badge("hyperscaler", "aws", contract, {}, icon_resolver=None, warnings=warnings)
    assert any("'aws'" in w for w in warnings)


def test_branding_and_badges_warn_on_unresolvable_assets(capsys):
    # End-to-end proof (FIX-1), DETERMINISTIC regardless of whether the
    # gitignored .local brand pack is hydrated: point every branding/badge
    # slot at names that exist in NO pack (public or .local), so all three
    # code paths degrade-and-warn. Before the fix, the customer-logo and
    # watermark paths (metadata.branding → branding_block → badge) silently
    # dropped the WARNING because `_emit_branding_and_badges` received
    # icon_resolver/warnings but never threaded them through; only the
    # group-badge path (via `_place_molecule`) warned. Now all three do.
    gen = load_script("generate-drawio")
    ir = {
        "metadata": {
            "title": "Warn Test",
            "branding": {"customerLogo": "zzz-no-logo",
                         "partnerWatermark": "zzz-no-watermark"},
        },
        "groups": [{"id": "tier1", "type": "cloud-tier", "kind": "public",
                    "label": "Public", "badges": {"hyperscalers": ["zzz-no-badge"]}}],
        "nodes": [{"id": "n1", "label": "Sys", "group": "tier1"}],
        "edges": [],
    }
    diagram = gen.parse_json(ir)
    gen.emit(diagram, layout="auto")
    stderr = capsys.readouterr().err
    assert "WARNING" in stderr
    assert "zzz-no-logo" in stderr, "customer logo (metadata.branding) must warn — was silent pre-fix"
    assert "zzz-no-watermark" in stderr, "partner watermark (metadata.branding) must warn — was silent pre-fix"
    assert "zzz-no-badge" in stderr, "hyperscaler badge must warn on unresolvable asset"


def test_flow_family_style_maps_all_six(M, contract):
    mapping = {
        "identity": "edge-identity", "provisioning": "edge-provisioning",
        "master-data": "edge-master-data", "transport": "edge-transport",
        "firewall": "edge-firewall", "default": "edge-default",
    }
    for fam, mol in mapping.items():
        assert M.flow_family_style(fam, contract) == _style(contract, mol)


# ─────────────────────────────────────────────────────────────────────────────
# Placeholder resolution mechanics.
# ─────────────────────────────────────────────────────────────────────────────
def test_resolve_style_placeholders_idempotent(M):
    style = "shape=image;image=@{aws};aspect=fixed;"
    packs = {"aws-badge": {"dataUri": "data:image/png,AAAA"}}
    once, unresolved = M.resolve_style_placeholders(style, packs)
    assert unresolved == [] and "image=data:image/png,AAAA" in once
    twice, unresolved2 = M.resolve_style_placeholders(once, packs)
    assert twice == once and unresolved2 == []  # already-resolved is untouched


def test_resolve_style_placeholders_via_icon_resolver(M):
    style = "shape=image;image=@{service};"
    resolved, unresolved = M.resolve_style_placeholders(
        style, {}, icon_resolver=lambda n: "data:image/svg+xml,ZZZ" if n == "service" else None)
    assert unresolved == [] and "image=data:image/svg+xml,ZZZ" in resolved


def test_display_name_humanises_keys(M):
    assert M.display_name("aws") == "AWS"
    assert M.display_name("cloud-foundry") == "Cloud Foundry"
    assert M.display_name("azure-badge") == "Azure"  # suffix stripped
    assert M.display_name("some-thing") == "Some Thing"


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — GOLDEN: the fixture rendered through emit() carries contract-exact
# molecule styles in its XML.
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def golden_root():
    gen = load_script("generate-drawio")
    diagram = gen.parse_json(json.loads(V2_FIXTURE.read_text(encoding="utf-8")))
    xml = gen.emit(diagram, layout="auto")
    return gen, ET.fromstring(xml)


@pytest.fixture(scope="module")
def golden_styles(golden_root):
    _gen, root = golden_root
    return [el.get("style", "") for el in root.iter("mxCell")]


def _contract_prefix(contract, name):
    """Contract style, truncated before any image= placeholder (image molecules
    have their dataUri substituted at emit time; the pre-image prefix is exact)."""
    s = contract["molecules"][name]["style"]
    i = s.find("image=@")
    return s[:i] if i != -1 else s


def _cell_style_by_id(root, cell_id):
    for el in root.iter("mxCell"):
        if el.get("id") == cell_id:
            return el.get("style", "")
    return None


@pytest.mark.parametrize("name", [
    "product-box", "capability-chip", "title-block",
    "subaccount-frame", "governance-strip", "tier-box-nonsap",
    "db", "chip", "pill-protocol",
    "edge-identity", "edge-provisioning", "edge-master-data",
    "edge-transport", "edge-firewall", "edge-default",
])
def test_golden_molecule_style_present_and_contract_exact(golden_styles, contract, name):
    # NB: "sap-btp-chip" is intentionally not here — FIX-B suppresses the chip on
    # the v2 fixture's (all-nested) subaccounts, so it's covered by the dedicated
    # test_golden_sap_btp_chip_resolved_to_image (top-level subaccount) instead.
    prefix = _contract_prefix(contract, name)
    assert any(s.startswith(prefix) for s in golden_styles), \
        f"no emitted cell carries the {name!r} contract style"


# ─────────────────────────────────────────────────────────────────────────────
# FIX-4 (review round): product-box/custom-app-box and subaccount-frame/
# tier-box-sap are BYTE-IDENTICAL contract style strings (no image= token to
# even prefix-truncate on). The loose "any cell in the whole document" check
# above can't discriminate them — e.g. the "custom-app-box" parametrization
# was satisfied by an unrelated product-box cell (bpa/cloud-alm's product
# nodes), never actually looking at the custom-app-1 group's own cell, so a
# dispatch bug in `_group_molecule_cells` (e.g. routing "custom-app" to the
# wrong builder) would go undetected. Pin each ambiguous molecule to the
# SPECIFIC emitted cell id for a known IR entity — computed the exact same
# way generate-drawio.py computes it — so the right builder having run for
# THAT entity is what's actually asserted.
# ─────────────────────────────────────────────────────────────────────────────
def test_golden_custom_app_box_cell_pinned_by_id(golden_root, contract):
    gen, root = golden_root
    cell_id = gen._stable_id("g", "custom-app-1")  # fixture's only custom-app group
    style = _cell_style_by_id(root, cell_id)
    assert style is not None, f"no emitted cell for the custom-app-1 group (id {cell_id!r})"
    assert style.startswith(_contract_prefix(contract, "custom-app-box"))


def test_golden_tier_box_sap_cell_pinned_by_id(golden_root, contract):
    gen, root = golden_root
    cell_id = gen._stable_id("g", "cloud-tier-public")  # fixture's kind="public" tier
    style = _cell_style_by_id(root, cell_id)
    assert style is not None, f"no emitted cell for the cloud-tier-public group (id {cell_id!r})"
    assert style.startswith(_contract_prefix(contract, "tier-box-sap"))


def test_golden_subaccount_frame_cell_pinned_by_id(golden_root, contract):
    gen, root = golden_root
    cell_id = gen._stable_id("g", "subaccount-test")  # nested subaccount frame
    style = _cell_style_by_id(root, cell_id)
    assert style is not None, f"no emitted cell for the subaccount-test group (id {cell_id!r})"
    assert style.startswith(_contract_prefix(contract, "subaccount-frame"))


def test_golden_product_box_cell_pinned_by_id(golden_root, contract):
    gen, root = golden_root
    cell_id = gen._stable_id("n", "bpa")  # fixture's product node
    style = _cell_style_by_id(root, cell_id)
    assert style is not None, f"no emitted cell for the bpa product node (id {cell_id!r})"
    assert style.startswith(_contract_prefix(contract, "product-box"))


def test_golden_sap_btp_chip_resolved_to_image(contract):
    # The SAP BTP chip resolves via the public brand pack (sap-logo-chip alias):
    # its emitted style is the contract text chip + a real SVG dataUri. FIX-B
    # stamps the chip only on the OUTERMOST BTP container, so this uses a diagram
    # with a TOP-LEVEL subaccount (the v2 fixture's subaccounts are both nested,
    # which correctly suppresses their chips).
    gen = load_script("generate-drawio")
    ir = {
        "metadata": {"title": "chip", "level": "L1"},
        "groups": [{"id": "sa", "type": "subaccount", "label": "Prod", "position": "center"}],
        "nodes": [{"id": "n", "label": "Event Mesh", "group": "sa", "service": "Event Mesh"}],
        "edges": [],
    }
    root = ET.fromstring(gen.emit(gen.parse_json(ir), layout="auto"))
    styles = [el.get("style", "") for el in root.iter("mxCell")]
    base = _style(contract, "sap-btp-chip")
    hits = [s for s in styles if s.startswith(base) and "data:image/svg" in s]
    assert hits, "SAP BTP chip did not resolve to the brand-pack image"


def test_golden_nested_subaccounts_suppress_btp_chip():
    # FIX-B: the v2 fixture's subaccounts (btp ⊃ test ⊃ production) are all
    # nested, so NONE of them stamps a redundant "SAP BTP" chip — only the
    # outer btp-layer badge remains. Guards against the chip "staircase".
    gen = load_script("generate-drawio")
    root = ET.fromstring(gen.emit(
        gen.parse_json(json.loads(V2_FIXTURE.read_text(encoding="utf-8"))), layout="auto"))
    for gid in ("subaccount-test", "subaccount-production"):
        frame_id = gen._stable_id("g", gid)
        chip_ids = [el.get("id") for el in root.iter("mxCell")
                    if el.get("id", "").startswith(frame_id) and el.get("id", "").endswith("-btpchip")]
        assert not chip_ids, f"nested subaccount {gid!r} must not stamp a SAP BTP chip"
        # …but it DOES show its own name in a top-band title cell.
        title_id = f"{frame_id}-frame-title"
        title = _cell_style_by_id(root, title_id)
        assert title is not None, f"{gid!r} must carry its own top-band title cell"


def test_golden_v1_style_paths_untouched(golden_styles):
    # The v1 orthogonal edge base still appears (e5 has no flowFamily), proving
    # the v1 edge path is untouched by the flow-family wiring.
    assert any(s.startswith("edgeStyle=orthogonalEdgeStyle;") for s in golden_styles)
