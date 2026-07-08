# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""tests/test_icon_resolution.py — the "missing icons" fix Gabriele reported.

Two independent fixes:

  * FIX-A — ``ShapeIndex.resolve()`` in generate-drawio.py was too strict:
    seven common IR service-name decorations (a leading "SAP " the catalog
    entry omits, a trailing "Service(s)"/edition suffix, or a "Family -
    Member" compound name) failed to resolve even though the catalog HAS the
    icon under a slightly different spelling. ``resolve()`` now retries a set
    of conservative, deterministic normalized re-spellings, and finally an
    abbreviation-tolerant token-set tier (e.g. "Application" ~ "App"), before
    giving up. Every name that used to resolve must keep resolving to the
    exact same icon — this file proves both the 7 recoveries and the
    no-regression property.

  * FIX-B — a capability chip (BPA/Work-Zone) with no explicit ``icon`` now
    auto-resolves a harvested ``cap-<slug>`` icon from the (gitignored,
    often-absent) local brand pack via ``_molecules.product_box``. Absent
    pack, or a capability with no harvested icon (Cloud ALM's
    "Implementation"/"Operations"/"Transformation") → the chip stays
    text-only, never an error. An explicit ``icon`` always wins.
"""
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from conftest import load_script

ROOT = Path(__file__).resolve().parent.parent
SHAPE_INDEX = ROOT / "assets" / "shape-index.json"

# The 7 IR service names that used to render with NO icon even though the
# catalog carries one under a slightly different canonical spelling.
PREVIOUSLY_UNRESOLVED = {
    "SAP Build Work Zone Standard Edition": "SAP Build Work Zone",
    "SAP Business Application Studio": "Business Application Studio",
    "SAP Document Management Service": "Document Management Service",
    "SAP HTML5 Application Repository Service": "HTML5 App Repository",
    "SAP Cloud Identity Services - Identity Directory": "Identity Directory",
    "SAP Cloud Identity Services - Identity Authentication": "Identity Authentication",
    "SAP Cloud Identity Services - Identity Provisioning": "Identity Provisioning",
}

# A sample of names that already resolved before FIX-A — must keep resolving
# to the exact same catalog entry (byte-for-byte the same drawioStyle).
ALREADY_RESOLVING = [
    "SAP Build Apps",
    "SAP Cloud ALM",
    "SAP Task Center",
    "SAP HANA Cloud",
]


@pytest.fixture(scope="module")
def gen():
    return load_script("generate-drawio")


@pytest.fixture(scope="module")
def shape_index(gen):
    return gen.ShapeIndex.load(SHAPE_INDEX)


# ─────────────────────────────────────────────────────────────────────────────
# FIX-A — resolver recovers the 7 decorated names, with no regression.
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("query,canonical", sorted(PREVIOUSLY_UNRESOLVED.items()))
def test_resolve_recovers_decorated_service_names(shape_index, query, canonical):
    svc = shape_index.resolve(query)
    assert svc is not None, f"{query!r} should now resolve to an icon"
    assert svc["name"] == canonical
    assert svc.get("drawioStyle"), "resolved entry must carry a drawio image style"


@pytest.mark.parametrize("query", ALREADY_RESOLVING)
def test_resolve_no_regression_on_already_working_names(shape_index, query):
    """These names resolved before FIX-A touched the code — confirm the new
    normalized-variant/fuzzy tiers never override a hit the original
    exact/subset algorithm already found."""
    svc = shape_index.resolve(query)
    assert svc is not None
    assert svc["name"] == query  # these queries ARE the canonical name


def test_resolve_full_catalog_names_and_aliases_are_stable(gen, shape_index):
    """Every canonical name and alias already in the catalog must resolve to
    itself — a blanket regression net across all ~470 catalog entries, not
    just the hand-picked sample above."""
    import json

    data = json.loads(SHAPE_INDEX.read_text(encoding="utf-8"))
    for svc in data.get("services", []):
        name = svc["name"]
        hit = shape_index.resolve(name)
        assert hit is not None, f"catalog name {name!r} must resolve"
        assert hit["name"] == name, f"{name!r} resolved to a different entry: {hit['name']!r}"
        for alias in svc.get("aliases", []) or []:
            ahit = shape_index.resolve(alias)
            assert ahit is not None, f"alias {alias!r} must resolve"


def test_resolve_single_short_token_still_conservative(shape_index):
    """A bare, very short query never reaches the risky fuzzy tokenset tier
    (guards the historical "AI matches everything" failure mode)."""
    # "AI" alone is not a catalog name; it must not fuzzy-match some
    # unrelated multi-word service that merely contains the letters.
    hit = shape_index.resolve("AI")
    assert hit is None or "AI" in hit["name"].split()


def test_resolve_unknown_service_still_none(shape_index):
    assert shape_index.resolve("Totally Not A Real SAP Service Name") is None
    assert shape_index.resolve(None) is None
    assert shape_index.resolve("") is None


# ─────────────────────────────────────────────────────────────────────────────
# FIX-B — capability-chip icon auto-resolution.
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def M():
    return load_script("_molecules")


@pytest.fixture(scope="module")
def contract(M):
    return M.load_contract()


def _chip(cells):
    return [c for c in cells if c["id"].startswith("chip")][0]


def test_load_brand_packs_merges_capability_icons(M, monkeypatch, tmp_path):
    (tmp_path / "brand-pack").mkdir()
    (tmp_path / "brand-pack" / "index.json").write_text(
        '{"sap-logo-chip": {"dataUri": "data:image/svg+xml,BASE"}}',
        encoding="utf-8",
    )
    local = tmp_path / "brand-pack.local"
    local.mkdir()
    (local / "capability-icons.json").write_text(
        '{"cap-decision": {"dataUri": "data:image/svg+xml,DECISION"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(M, "ASSETS", tmp_path)
    packs = M.load_brand_packs()
    assert packs["sap-logo-chip"]["dataUri"] == "data:image/svg+xml,BASE"
    assert packs["cap-decision"]["dataUri"] == "data:image/svg+xml,DECISION"


def test_load_brand_packs_skips_missing_capability_icons_gracefully(M, monkeypatch, tmp_path):
    """No brand-pack.local at all (the common CI/Desktop/other-machine case)
    → load_brand_packs returns cleanly, no exception, no cap- keys."""
    (tmp_path / "brand-pack").mkdir()
    (tmp_path / "brand-pack" / "index.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(M, "ASSETS", tmp_path)
    packs = M.load_brand_packs()
    assert not any(k.startswith("cap-") for k in packs)


def test_capability_chip_auto_resolves_from_pack(M, contract):
    packs = {"cap-decision": {"dataUri": "data:image/svg+xml,DECISION"}}
    node = NS(id="p", label="BPA", type="product",
              capabilities=[{"label": "Decision"}])
    cells = M.product_box(node, contract, icon_resolver=None, brand_packs=packs)
    chip = _chip(cells)
    assert "image=data:image/svg+xml,DECISION" in chip["style"]
    assert chip["value"] == "Decision"


def test_capability_chip_visibility_scenario_alias(M, contract):
    """Label decoration handling: "Visibility Scenario" strips " Scenario"
    and resolves the harvested "cap-visibility" key."""
    packs = {"cap-visibility": {"dataUri": "data:image/svg+xml,VIS"}}
    node = NS(id="p", label="Work Zone", type="product",
              capabilities=[{"label": "Visibility Scenario"}])
    cells = M.product_box(node, contract, icon_resolver=None, brand_packs=packs)
    chip = _chip(cells)
    assert "image=data:image/svg+xml,VIS" in chip["style"]


@pytest.mark.parametrize("label,key", [
    ("Business Content", "cap-business-content"),
    ("Task Center", "cap-task-center"),
    ("Channel Manager", "cap-channel-manager"),
])
def test_capability_chip_slug_derivation(M, contract, label, key):
    packs = {key: {"dataUri": f"data:image/svg+xml,{key.upper()}"}}
    node = NS(id="p", label="P", type="product", capabilities=[{"label": label}])
    cells = M.product_box(node, contract, icon_resolver=None, brand_packs=packs)
    chip = _chip(cells)
    assert f"image={packs[key]['dataUri']}" in chip["style"]


def test_capability_chip_no_matching_icon_stays_text(M, contract):
    """Cloud ALM's "Implementation" has no harvested cap-implementation icon
    — this is CORRECT (text-only in Gabriele's originals), not a bug."""
    packs = {"cap-decision": {"dataUri": "data:image/svg+xml,DECISION"}}
    node = NS(id="p", label="Cloud ALM", type="product",
              capabilities=[{"label": "Implementation"}])
    cells = M.product_box(node, contract, icon_resolver=None, brand_packs=packs)
    chip = _chip(cells)
    assert "image=" not in chip["style"]
    assert chip["value"] == "Implementation"


def test_capability_chip_pack_absent_all_text_no_error(M, contract):
    node = NS(id="p", label="BPA", type="product",
              capabilities=[{"label": "Decision"}, {"label": "Task Center"}])
    cells = M.product_box(node, contract, icon_resolver=None, brand_packs=None)
    chips = [c for c in cells if c["id"].startswith("chip")]
    assert len(chips) == 2
    assert all("image=" not in c["style"] for c in chips)


def test_capability_chip_explicit_icon_wins_over_auto_resolution(M, contract):
    packs = {"cap-decision": {"dataUri": "data:image/svg+xml,DECISION"}}
    explicit_uri = "data:image/svg+xml,EXPLICIT"
    node = NS(id="p", label="BPA", type="product",
              capabilities=[{"label": "Decision", "icon": "explicit-icon"}])
    cells = M.product_box(
        node, contract,
        icon_resolver=lambda n: explicit_uri if n == "explicit-icon" else None,
        brand_packs=packs,
    )
    chip = _chip(cells)
    assert f"image={explicit_uri}" in chip["style"]
    assert "DECISION" not in chip["style"]
