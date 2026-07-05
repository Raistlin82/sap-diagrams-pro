# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path

from conftest import load_script

ROOT = Path(__file__).resolve().parent.parent


def test_zone_icon_size():
    zl = load_script("_zone_layout")
    assert zl.icon_size("L1") == 48
    assert zl.icon_size("L2") == 32


def test_generator_imports():
    gen = load_script("generate-drawio")
    assert hasattr(gen, "emit")


def test_v1_demo_renders_end_to_end():
    """Regression net for Task 5: before `emit` starts reading the new IR v2
    fields (product/chip/db molecules, flowFamily edge styles, branding/
    badges), prove the existing v1 rendering path still produces
    well-formed .drawio XML end-to-end — not just that it *parses*
    (test_ir_v2.py only exercises parse_json, never emit). If a future
    change to the shared molecule/style code breaks v1 rendering, this is
    the test that should catch it.
    """
    gen = load_script("generate-drawio")
    payload = json.loads((ROOT / "demo" / "nova" / "nova-L1.json").read_text(encoding="utf-8"))
    diagram = gen.parse_json(payload)

    # shape_index=None → emit() loads the real ShapeIndex itself, exactly as
    # the CLI's main() does. layout="auto" is the deterministic zone-
    # composition engine (no graphviz dependency).
    xml = gen.emit(diagram, layout="auto")

    assert xml
    assert "mxGraphModel" in xml
