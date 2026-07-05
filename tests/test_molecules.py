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
    assert chip["value"] == "SAP BTP"
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
    assert resolved["value"] == "SAP BTP"  # degrades to the text chip


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
    assert cells[0]["value"] == "Custom App"


def test_governance_strip_frame(M, contract):
    g = NS(id="gov", label="Governance", badges=None)
    cells = M.governance_strip(g, contract)
    assert cells[0]["style"].startswith(_style(contract, "governance-strip"))


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


def test_network_separator(M, contract):
    cells = M.network_separator(500, 100, 700, contract)
    line = [c for c in cells if c["id"] == "sep-line"][0]
    label = [c for c in cells if c["id"] == "sep-label"][0]
    assert line["style"].startswith(_style(contract, "network-separator"))
    assert line.get("edge") is True and line["h"] == 700.0 - 100.0  # y1 - y0
    assert label["style"].startswith(_style(contract, "network-separator-label"))


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


def test_golden_branding_and_diagram_badges_warn_on_unresolved_assets(capsys):
    # End-to-end proof (FIX-1): render the v2 fixture (customerLogo="acme",
    # partnerWatermark="lutech", metadata.badges.hyperscalers=["azure"]) with
    # NO .local brand pack. Before the fix, only "azure" warned — via the
    # group-badge path (cloud-tier-right's own hyperscaler badge, wired
    # through `_place_molecule`) — because `_emit_branding_and_badges`
    # received icon_resolver/warnings but never passed them into
    # `branding_block`/`badge`. Now all three degrade-and-warn identically.
    gen = load_script("generate-drawio")
    diagram = gen.parse_json(json.loads(V2_FIXTURE.read_text(encoding="utf-8")))
    gen.emit(diagram, layout="auto")
    stderr = capsys.readouterr().err
    assert "WARNING" in stderr
    assert "'acme'" in stderr, "customer logo (metadata.branding) must warn — was silent pre-fix"
    assert "'lutech'" in stderr, "partner watermark (metadata.branding) must warn — was silent pre-fix"
    assert "'azure'" in stderr, "hyperscaler badge must keep warning (already worked pre-fix)"


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
    "db", "chip", "sap-btp-chip", "pill-protocol",
    "edge-identity", "edge-provisioning", "edge-master-data",
    "edge-transport", "edge-firewall", "edge-default",
])
def test_golden_molecule_style_present_and_contract_exact(golden_styles, contract, name):
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


def test_golden_sap_btp_chip_resolved_to_image(golden_styles, contract):
    # The SAP BTP chip resolves via the public brand pack (sap-logo-chip alias):
    # its emitted style is the contract text chip + a real SVG dataUri.
    base = _style(contract, "sap-btp-chip")
    hits = [s for s in golden_styles if s.startswith(base) and "data:image/svg" in s]
    assert hits, "SAP BTP chip did not resolve to the brand-pack image"


def test_golden_v1_style_paths_untouched(golden_styles):
    # The v1 orthogonal edge base still appears (e5 has no flowFamily), proving
    # the v1 edge path is untouched by the flow-family wiring.
    assert any(s.startswith("edgeStyle=orthogonalEdgeStyle;") for s in golden_styles)
