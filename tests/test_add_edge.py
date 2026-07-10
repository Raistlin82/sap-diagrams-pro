# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""tests/test_add_edge.py — the hybrid scaffold-path surgical add-edge step.

add-edge.py wires a new styled, orthogonally-routed edge between two existing
vertices of a scaffolded .drawio. It:
  * styles the connector by flow family (the six edge-* contract molecules),
  * chooses exit/entry ports from the endpoints' relative positions and emits an
    orthogonalEdgeStyle line with ONE interior waypoint (clean L),
  * drops an optional protocol pill (rounded arcSize=50 chip) and an optional
    edge label as separate cells on the edge's longest segment, and
  * shifts the pill clear of the NETWORK separator keep-out bands when one is
    present, so a cross-network pill never parks on the seam.
It saves via the shared _drawio_edit.save helper (which writes a .bak first).
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from conftest import load_script

add_edge = load_script("add-edge")
edit = load_script("_drawio_edit")
cr = load_script("_channel_router")

# Separator geometry the sep fixture bakes in (x / y0 / y1 known to the test).
SEP_X, SEP_Y0, SEP_Y1 = 400.0, 100.0, 400.0


def _base_fixture(tmp_path: Path) -> Path:
    """Two vertices with geometry, no separator."""
    mx = (
        '<mxfile><diagram><mxGraphModel><root>'
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        '<mxCell id="n" value="Alpha" parent="1" vertex="1" style="rounded=1;">'
        '<mxGeometry x="40" y="40" width="120" height="60" as="geometry"/></mxCell>'
        '<mxCell id="g" value="Beta" parent="1" vertex="1" style="rounded=1;">'
        '<mxGeometry x="320" y="140" width="120" height="60" as="geometry"/></mxCell>'
        '</root></mxGraphModel></diagram></mxfile>'
    )
    f = tmp_path / "d.drawio"
    f.write_text(mx, encoding="utf-8")
    return f


def _sep_fixture(tmp_path: Path) -> Path:
    """n and g straddling a netsep-style separator bar centred on SEP_X, so an
    edge between them crosses the seam and its longest segment's midpoint lands
    on the keep-out band (forcing the pill shift)."""
    mx = (
        '<mxfile><diagram><mxGraphModel><root>'
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        '<mxCell id="n" value="Alpha" parent="1" vertex="1" style="rounded=1;">'
        '<mxGeometry x="40" y="200" width="120" height="60" as="geometry"/></mxCell>'
        '<mxCell id="g" value="Beta" parent="1" vertex="1" style="rounded=1;">'
        '<mxGeometry x="640" y="200" width="120" height="60" as="geometry"/></mxCell>'
        '<mxCell id="netsep-abc123" value="" '
        'style="endArrow=none;html=1;strokeColor=#5B738B;rounded=1;jumpStyle=gap;strokeWidth=3;" '
        'edge="1" parent="1" connectable="0">'
        f'<mxGeometry relative="1" as="geometry">'
        f'<mxPoint x="{SEP_X}" y="{SEP_Y0}" as="sourcePoint"/>'
        f'<mxPoint x="{SEP_X}" y="{SEP_Y1}" as="targetPoint"/>'
        f'<Array as="points"><mxPoint x="{SEP_X}" y="{SEP_Y0}"/>'
        f'<mxPoint x="{SEP_X}" y="{SEP_Y1}"/></Array>'
        '</mxGeometry></mxCell>'
        '</root></mxGraphModel></diagram></mxfile>'
    )
    f = tmp_path / "d.drawio"
    f.write_text(mx, encoding="utf-8")
    return f


def _cells(path: Path) -> list[ET.Element]:
    return list(ET.parse(path).getroot().iter("mxCell"))


def _edge_cell(path: Path) -> ET.Element:
    edges = [c for c in _cells(path) if c.get("edge") == "1"
             and c.get("source") and c.get("target")]
    assert edges, "no wired edge cell emitted"
    return edges[0]


def _cell_by_value(path: Path, value: str) -> ET.Element | None:
    for c in _cells(path):
        if c.get("value") == value:
            return c
    return None


def test_add_edge_styles_by_family(tmp_path):
    f = _base_fixture(tmp_path)
    rc = add_edge.main([
        str(f), "--source", "n", "--target", "g",
        "--flowFamily", "identity", "--label", "Login", "--pill", "SAML2/OIDC",
    ])
    assert rc == 0
    assert Path(str(f) + ".bak").exists()

    edge = _edge_cell(f)
    assert edge.get("source") == "n"
    assert edge.get("target") == "g"
    style = edge.get("style") or ""
    from _molecules import load_contract, _style
    # identity family colour, or the whole edge-identity molecule as prefix.
    assert "#188918" in style or style.startswith(_style(load_contract(), "edge-identity"))

    pill = _cell_by_value(f, "SAML2/OIDC")
    assert pill is not None, "pill cell not emitted"
    assert "arcSize=50" in (pill.get("style") or "")

    label = _cell_by_value(f, "Login")
    assert label is not None, "label cell not emitted"


def test_add_edge_orthogonal(tmp_path):
    f = _base_fixture(tmp_path)
    rc = add_edge.main([str(f), "--source", "n", "--target", "g"])
    assert rc == 0
    edge = _edge_cell(f)
    style = edge.get("style") or ""
    assert "orthogonalEdgeStyle" in style
    # one interior waypoint OR exit/entry anchors
    geo = edge.find("mxGeometry")
    arr = geo.find("Array") if geo is not None else None
    has_waypoint = arr is not None and len(arr.findall("mxPoint")) >= 1
    has_anchors = "exitX" in style and "entryX" in style
    assert has_waypoint or has_anchors


def test_add_edge_unknown_endpoint_errors(tmp_path):
    f = _base_fixture(tmp_path)
    original = f.read_bytes()
    try:
        rc = add_edge.main([str(f), "--source", "nope", "--target", "g"])
    except SystemExit as exc:
        rc = exc.code
    assert rc not in (0, None)
    assert not Path(str(f) + ".bak").exists()
    assert f.read_bytes() == original


def test_pill_clears_separator(tmp_path):
    f = _sep_fixture(tmp_path)
    net_sep = {"x": SEP_X, "y0": SEP_Y0, "y1": SEP_Y1}
    rc = add_edge.main([
        str(f), "--source", "n", "--target", "g", "--pill", "SAML2/OIDC",
    ])
    assert rc == 0

    pill = _cell_by_value(f, "SAML2/OIDC")
    assert pill is not None
    x, y, w, h = edit.geometry(pill)
    pill_rect = cr.Rect(x, y, w, h)

    bands = cr._sep_obstacle_rects(net_sep)
    assert bands, "test fixture must expose a separator"
    for band in bands:
        assert not pill_rect.intersects(band), (
            f"pill {pill_rect} parks on separator band {band}")
