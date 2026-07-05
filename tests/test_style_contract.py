# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
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
