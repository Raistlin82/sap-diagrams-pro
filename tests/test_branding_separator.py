# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""tests/test_branding_separator.py — Task 7 emit-level wiring.

Covers the pieces that only appear when the full diagram is emitted through
``generate-drawio.emit()``:

  * the NETWORK separator edge cell (contract ``network-separator`` style +
    ``edge="1"``) placed in the center→right gutter, and its opt-out;
  * branding placement — the customer logo at the TOP-LEFT (before the title,
    which shifts right of it), and the partner watermark as a faint, centred,
    contract-opacity background mark that is emitted BEHIND everything and is
    sized to a modest fraction of the canvas (FIX-C).
"""
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from conftest import load_script

ROOT = Path(__file__).resolve().parent.parent
V2 = ROOT / "tests" / "fixtures" / "ir-v2-sample.json"
NOVA = ROOT / "demo" / "nova" / "nova-L1.json"

# A minimal 1×1 PNG data-URI so a brand asset "resolves" to an image in tests
# without shipping a .local pack (mirrors the comma-form the emitter expects).
_IMG = "data:image/png,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


@pytest.fixture(scope="module")
def gen():
    return load_script("generate-drawio")


def _emit_root(gen, path, mutate=None, brand_packs=None):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if mutate:
        payload = mutate(payload)
    diagram = gen.parse_json(payload)
    xml = gen.emit(diagram, layout="auto")
    return ET.fromstring(xml)


def _cells(root):
    return list(root.iter("mxCell"))


def _sep_cells(gen, root):
    sep_style = load_script("_molecules").load_contract()["molecules"]["network-separator"]["style"]
    return [c for c in _cells(root)
            if c.get("edge") == "1" and c.get("style", "").startswith(sep_style)]


# ─────────────────────────────────────────────────────────────────────────────
# NETWORK separator emission.
# ─────────────────────────────────────────────────────────────────────────────
def test_emit_network_separator_edge_and_label(gen):
    root = _emit_root(gen, V2)
    seps = _sep_cells(gen, root)
    assert len(seps) == 1, "exactly one NETWORK separator bar expected"
    line = seps[0]
    geom = line.find("mxGeometry")
    src = geom.find("./mxPoint[@as='sourcePoint']")
    tgt = geom.find("./mxPoint[@as='targetPoint']")
    assert src is not None and tgt is not None, "separator needs explicit source/target points"
    # vertical bar: same x, top above bottom
    assert src.get("x") == tgt.get("x")
    assert float(src.get("y")) < float(tgt.get("y"))
    # the "NETWORK" caption cell is present
    labels = [c for c in _cells(root) if (c.get("value") or "") == "NETWORK"]
    assert labels, "NETWORK caption cell expected"


def test_emit_network_separator_absent_on_opt_out(gen):
    def _off(p):
        p.setdefault("metadata", {})["networkSeparator"] = False
        return p
    root = _emit_root(gen, V2, mutate=_off)
    assert _sep_cells(gen, root) == []
    assert not [c for c in _cells(root) if (c.get("value") or "") == "NETWORK"]


def test_emit_network_separator_present_for_nova(gen):
    root = _emit_root(gen, NOVA)
    assert len(_sep_cells(gen, root)) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Branding placement — customer logo top-left, title shifts right of it.
# ─────────────────────────────────────────────────────────────────────────────
def _geom_x(cell):
    return float(cell.find("mxGeometry").get("x"))


def _title_cell(gen, root, title):
    tid = gen._stable_id("title", title)
    return next(c for c in _cells(root) if c.get("id") == tid)


def test_customer_logo_top_left_and_title_shifts_right(gen):
    root = _emit_root(gen, V2)
    logo = next((c for c in _cells(root) if c.get("id") == gen._stable_id("brand", "customer-logo")), None)
    assert logo is not None, "customer logo cell expected (text-badge fallback is fine)"
    lg = logo.find("mxGeometry")
    lx, ly = float(lg.get("x")), float(lg.get("y"))
    # top-left corner (not the old top-right placement)
    assert lx < 200 and ly < 80, f"customer logo must sit top-left, got ({lx},{ly})"
    # the diagram title shifts to the RIGHT of the logo
    title = _title_cell(gen, root, json.loads(V2.read_text())["metadata"]["title"])
    assert _geom_x(title) > lx + float(lg.get("width")) - 1


def test_title_not_shifted_without_branding(gen):
    # nova has no metadata.branding ⇒ the title keeps its default left margin.
    root = _emit_root(gen, NOVA)
    title = _title_cell(gen, root, json.loads(NOVA.read_text())["metadata"]["title"])
    assert _geom_x(title) == 32


# ─────────────────────────────────────────────────────────────────────────────
# FIX-C — the partner watermark is a faint, centred, behind-everything mark
# sized to a modest fraction of the canvas.
# ─────────────────────────────────────────────────────────────────────────────
def test_watermark_faint_centered_and_behind(gen, monkeypatch):
    # Make the partner watermark asset "resolve" to an image so the emitter
    # actually places it (a text fallback is intentionally never drawn).
    modmol = gen._molecules_module()
    monkeypatch.setattr(modmol, "load_brand_packs",
                        lambda: {"lutech": {"dataUri": _IMG}, "acme": {"dataUri": _IMG}})

    root = _emit_root(gen, V2)
    model = root.find(".//mxGraphModel")
    canvas_w = int(model.get("pageWidth"))
    cells = _cells(root)
    wm = next((c for c in cells if c.get("id") == gen._stable_id("brand", "watermark")), None)
    assert wm is not None, "resolved image watermark must be emitted"

    style = wm.get("style", "")
    assert "shape=image" in style
    # faint: opacity comes from the contract watermark molecule (≈10–15)
    import re
    m = re.search(r"opacity=(\d+)", style)
    assert m and int(m.group(1)) <= 20, f"watermark must be faint, opacity={m and m.group(1)}"

    g = wm.find("mxGeometry")
    w = float(g.get("width"))
    assert w <= 0.4 * canvas_w + 1, "watermark must not span more than ~40% of the canvas width"
    # roughly centred horizontally
    cx = float(g.get("x")) + w / 2
    assert abs(cx - canvas_w / 2) <= 2

    # behind everything: emitted before the diagram title (document order == z-order)
    order = [c.get("id") for c in cells]
    title_id = gen._stable_id("title", json.loads(V2.read_text())["metadata"]["title"])
    assert order.index(wm.get("id")) < order.index(title_id)
