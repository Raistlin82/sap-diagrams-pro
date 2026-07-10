# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
from conftest import load_script

E = load_script("_drawio_edit")

MX = ('<mxfile><diagram><mxGraphModel><root>'
      '<mxCell id="0"/><mxCell id="1" parent="0"/>'
      '<mxCell id="g" value="Group" parent="1" vertex="1" style="rounded=1;">'
      '<mxGeometry x="10" y="20" width="100" height="80" as="geometry"/></mxCell>'
      '<mxCell id="n" value="Node" parent="g" vertex="1"><mxGeometry x="0" y="0" width="40" height="40" as="geometry"/></mxCell>'
      '<mxCell id="e" edge="1" source="n" target="g" parent="1"><mxGeometry as="geometry"/></mxCell>'
      '</root></mxGraphModel></diagram></mxfile>')


def test_load_find_children_geo(tmp_path):
    f = tmp_path/"d.drawio"; f.write_text(MX)
    doc = E.load(f)
    assert E.find_cell(doc, "g").get("value") == "Group"
    assert E.find_cell_by_label(doc, "Node").get("id") == "n"
    assert [c.get("id") for c in E.children(doc, "g")] == ["n"]
    assert E.geometry(E.find_cell(doc, "g")) == (10.0, 20.0, 100.0, 80.0)


def test_save_writes_bak(tmp_path):
    f = tmp_path/"d.drawio"; f.write_text(MX)
    doc = E.load(f); E.save(doc, f)
    assert (tmp_path/"d.drawio.bak").read_text() == MX


def test_grid_snap():
    assert E.snap(23) == 20 and E.snap(26) == 30   # 10px grid
