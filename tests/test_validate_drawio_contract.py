# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
from conftest import load_script


validate_drawio = load_script("validate-drawio")


def _write_drawio(tmp_path, name: str, cells: str):
    p = tmp_path / name
    p.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="test-fixture" version="24.7.8">
  <diagram id="p1" name="P1">
    <mxGraphModel dx="800" dy="600" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="900" pageHeight="600" math="0" shadow="0">
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="title-11111111" value="Fixture - SAP BTP Solution Diagram" style="text;html=1;align=left;verticalAlign=middle;fontColor=#0070F2;fontSize=16;fontStyle=1;" vertex="1" parent="1">
          <mxGeometry x="20" y="20" width="360" height="30" as="geometry" />
        </mxCell>
        {cells}
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
""",
        encoding="utf-8",
    )
    return p


def _issues(tmp_path, name: str, cells: str):
    return validate_drawio.validate(_write_drawio(tmp_path, name, cells))


def test_network_separator_grey_is_allowed_as_structural_stroke(tmp_path):
    issues = _issues(
        tmp_path,
        "network-separator.drawio",
        """
        <mxCell id="netsep-11111111" value="" style="endArrow=none;html=1;strokeColor=#5B738B;bendable=1;rounded=1;endFill=0;endSize=3;strokeWidth=3;jumpStyle=gap;" edge="1" parent="1" connectable="0">
          <mxGeometry relative="1" as="geometry">
            <mxPoint x="440" y="100" as="sourcePoint" />
            <mxPoint x="440" y="320" as="targetPoint" />
            <Array as="points">
              <mxPoint x="440" y="100" />
              <mxPoint x="440" y="320" />
            </Array>
          </mxGeometry>
        </mxCell>
        """,
    )
    assert not [i for i in issues if i.rule == "PALETTE_BORDER"]


def test_network_separator_grey_is_not_a_general_box_stroke(tmp_path):
    issues = _issues(
        tmp_path,
        "grey-box-stroke.drawio",
        """
        <mxCell id="n-aaaaaaaa" value="Bad grey stroke" style="rounded=1;whiteSpace=wrap;html=1;strokeColor=#5B738B;fillColor=#FFFFFF;fontColor=#1D2D3E;" vertex="1" parent="1">
          <mxGeometry x="100" y="100" width="120" height="60" as="geometry" />
        </mxCell>
        """,
    )
    assert [i for i in issues if i.rule == "PALETTE_BORDER" and i.cell_id == "n-aaaaaaaa"]


def test_decorative_badges_and_pills_do_not_emit_box_overlap(tmp_path):
    issues = _issues(
        tmp_path,
        "decorative-overlaps.drawio",
        """
        <mxCell id="n-aaaaaaaa" value="Node" style="rounded=1;whiteSpace=wrap;html=1;strokeColor=#0070F2;fillColor=#FFFFFF;fontColor=#1D2D3E;" vertex="1" parent="1">
          <mxGeometry x="100" y="100" width="100" height="60" as="geometry" />
        </mxCell>
        <mxCell id="st-11111111" value="1" style="ellipse;whiteSpace=wrap;html=1;aspect=fixed;gradientColor=#223548;strokeColor=none;gradientDirection=east;fillColor=#5B738B;rounded=0;pointerEvents=0;fontColor=#FFFFFF;" vertex="1" parent="n-aaaaaaaa" connectable="0">
          <mxGeometry x="-14" y="-14" width="28" height="28" as="geometry" />
        </mxCell>
        <mxCell id="counter-local" value="2" style="ellipse;whiteSpace=wrap;html=1;aspect=fixed;gradientColor=#223548;strokeColor=none;gradientDirection=east;fillColor=#5B738B;rounded=0;pointerEvents=0;fontColor=#FFFFFF;" vertex="1" parent="n-aaaaaaaa" connectable="0">
          <mxGeometry x="86" y="-14" width="28" height="28" as="geometry" />
        </mxCell>
        <mxCell id="if-22222222" value="Interface" style="rounded=1;whiteSpace=wrap;html=1;arcSize=50;strokeColor=#0070f3;fillColor=default;strokeWidth=1.5;fontColor=#0070f3;fontStyle=1;fontSize=9;align=center;verticalAlign=middle;" vertex="1" parent="n-aaaaaaaa" connectable="0">
          <mxGeometry x="22" y="-8" width="56" height="16" as="geometry" />
        </mxCell>
        <mxCell id="p-1234abcd" value="OIDC" style="rounded=1;whiteSpace=wrap;html=1;arcSize=50;strokeColor=#188918;fillColor=#F5FAE5;fontColor=#188918;fontStyle=1;strokeWidth=1.5;fontSize=10;align=center;verticalAlign=middle;" vertex="1" parent="1" connectable="0">
          <mxGeometry x="70" y="120" width="56" height="22" as="geometry" />
        </mxCell>
        """,
    )
    assert not [i for i in issues if i.rule == "BOX_OVERLAP"]


def test_main_node_overlap_still_emits_box_overlap(tmp_path):
    issues = _issues(
        tmp_path,
        "main-node-overlap.drawio",
        """
        <mxCell id="n-aaaaaaaa" value="A" style="rounded=1;whiteSpace=wrap;html=1;strokeColor=#0070F2;fillColor=#FFFFFF;fontColor=#1D2D3E;" vertex="1" parent="1">
          <mxGeometry x="100" y="100" width="90" height="60" as="geometry" />
        </mxCell>
        <mxCell id="n-bbbbbbbb" value="B" style="rounded=1;whiteSpace=wrap;html=1;strokeColor=#0070F2;fillColor=#FFFFFF;fontColor=#1D2D3E;" vertex="1" parent="1">
          <mxGeometry x="150" y="130" width="90" height="60" as="geometry" />
        </mxCell>
        """,
    )
    overlaps = [i for i in issues if i.rule == "BOX_OVERLAP"]
    assert overlaps and overlaps[0].cell_id == "n-aaaaaaaa|n-bbbbbbbb"
