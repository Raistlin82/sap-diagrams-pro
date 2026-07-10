# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""Tests for the bent-edge / label-background / absoluteArcSize checks and the
``--fix`` autofix added to scripts/validate-drawio.py."""
from conftest import load_script


validate_drawio = load_script("validate-drawio")


def _write_drawio(tmp_path, name: str, cells: str):
    p = tmp_path / name
    p.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="test-fixture" version="24.7.8">
  <diagram id="p1" name="P1">
    <mxGraphModel dx="800" dy="600" grid="1" gridSize="10" page="1" pageWidth="1400" pageHeight="800">
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="title-11111111" value="Fixture" style="text;html=1;fontColor=#0070F2;" vertex="1" parent="1">
          <mxGeometry x="20" y="20" width="360" height="30" as="geometry" />
        </mxCell>
        <mxCell id="n-aaaaaaaa" value="A" style="rounded=1;html=1;strokeColor=#0070F2;fillColor=#FFFFFF;" vertex="1" parent="1">
          <mxGeometry x="100" y="100" width="80" height="40" as="geometry" />
        </mxCell>
        <mxCell id="n-bbbbbbbb" value="B" style="rounded=1;html=1;strokeColor=#0070F2;fillColor=#FFFFFF;" vertex="1" parent="1">
          <mxGeometry x="600" y="400" width="80" height="40" as="geometry" />
        </mxCell>
        <mxCell id="n-cccccccc" value="C" style="rounded=1;html=1;strokeColor=#0070F2;fillColor=#FFFFFF;" vertex="1" parent="1">
          <mxGeometry x="100" y="400" width="80" height="40" as="geometry" />
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


# ── EDGE_BENT (CRITICAL) ─────────────────────────────────────────────────────


def test_bent_orthogonal_edge_is_flagged_critical(tmp_path):
    # n-aaaaaaaa center (140,120) vs n-bbbbbbbb center (640,420): both axes differ.
    issues = _issues(
        tmp_path,
        "bent.drawio",
        """
        <mxCell id="e-bent" style="edgeStyle=orthogonalEdgeStyle;html=1;endArrow=blockThin;" edge="1" parent="1" source="n-aaaaaaaa" target="n-bbbbbbbb">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        """,
    )
    bent = [i for i in issues if i.rule == "EDGE_BENT"]
    assert bent, "a center-misaligned orthogonal edge with no waypoints must be flagged"
    assert bent[0].severity == "CRITICAL"
    assert bent[0].cell_id == "e-bent"


def test_axis_aligned_orthogonal_edge_is_not_flagged(tmp_path):
    # n-aaaaaaaa center x=140, n-cccccccc center x=140 → share the vertical axis.
    issues = _issues(
        tmp_path,
        "aligned.drawio",
        """
        <mxCell id="e-aligned" style="edgeStyle=orthogonalEdgeStyle;html=1;endArrow=blockThin;" edge="1" parent="1" source="n-aaaaaaaa" target="n-cccccccc">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        """,
    )
    assert not [i for i in issues if i.rule == "EDGE_BENT"]


def test_waypointed_edge_is_not_flagged(tmp_path):
    issues = _issues(
        tmp_path,
        "waypointed.drawio",
        """
        <mxCell id="e-wp" style="edgeStyle=orthogonalEdgeStyle;html=1;endArrow=blockThin;" edge="1" parent="1" source="n-aaaaaaaa" target="n-bbbbbbbb">
          <mxGeometry relative="1" as="geometry">
            <Array as="points"><mxPoint x="140" y="420" /></Array>
          </mxGeometry>
        </mxCell>
        """,
    )
    assert not [i for i in issues if i.rule == "EDGE_BENT"]


def test_anchored_edge_is_not_flagged(tmp_path):
    issues = _issues(
        tmp_path,
        "anchored.drawio",
        """
        <mxCell id="e-anchor" style="edgeStyle=orthogonalEdgeStyle;exitX=1;exitY=0.5;entryX=0;entryY=0.5;html=1;endArrow=blockThin;" edge="1" parent="1" source="n-aaaaaaaa" target="n-bbbbbbbb">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        """,
    )
    assert not [i for i in issues if i.rule == "EDGE_BENT"]


# ── EDGE_LABEL_BG (WARNING) ──────────────────────────────────────────────────


def test_labelled_edge_without_label_bg_warns(tmp_path):
    issues = _issues(
        tmp_path,
        "labelled.drawio",
        """
        <mxCell id="e-labelled" value="audit events" style="edgeStyle=orthogonalEdgeStyle;html=1;endArrow=blockThin;" edge="1" parent="1" source="n-aaaaaaaa" target="n-cccccccc">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        """,
    )
    warn = [i for i in issues if i.rule == "EDGE_LABEL_BG"]
    assert warn and warn[0].severity == "WARNING" and warn[0].cell_id == "e-labelled"


def test_labelled_edge_with_label_bg_is_not_flagged(tmp_path):
    issues = _issues(
        tmp_path,
        "labelled-ok.drawio",
        """
        <mxCell id="e-ok" value="audit events" style="edgeStyle=orthogonalEdgeStyle;html=1;endArrow=blockThin;labelBackgroundColor=default;" edge="1" parent="1" source="n-aaaaaaaa" target="n-cccccccc">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        """,
    )
    assert not [i for i in issues if i.rule == "EDGE_LABEL_BG"]


def test_unlabelled_edge_does_not_warn_for_label_bg(tmp_path):
    issues = _issues(
        tmp_path,
        "unlabelled.drawio",
        """
        <mxCell id="e-plain" style="edgeStyle=orthogonalEdgeStyle;html=1;endArrow=blockThin;" edge="1" parent="1" source="n-aaaaaaaa" target="n-cccccccc">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        """,
    )
    assert not [i for i in issues if i.rule == "EDGE_LABEL_BG"]


# ── ARC_SIZE_ABS (WARNING) ───────────────────────────────────────────────────


def test_arcsize_without_absolute_warns(tmp_path):
    issues = _issues(
        tmp_path,
        "arc.drawio",
        """
        <mxCell id="zone-arc" value="Zone" style="rounded=1;arcSize=16;fillColor=#EBF8FF;strokeColor=#0070F2;" vertex="1" parent="1">
          <mxGeometry x="800" y="100" width="500" height="200" as="geometry" />
        </mxCell>
        """,
    )
    warn = [i for i in issues if i.rule == "ARC_SIZE_ABS"]
    assert warn and warn[0].severity == "WARNING" and warn[0].cell_id == "zone-arc"


def test_arcsize_with_absolute_is_not_flagged(tmp_path):
    issues = _issues(
        tmp_path,
        "arc-abs.drawio",
        """
        <mxCell id="zone-ok" value="Zone" style="rounded=1;arcSize=16;absoluteArcSize=1;fillColor=#EBF8FF;strokeColor=#0070F2;" vertex="1" parent="1">
          <mxGeometry x="800" y="100" width="500" height="200" as="geometry" />
        </mxCell>
        """,
    )
    assert not [i for i in issues if i.rule == "ARC_SIZE_ABS"]


def test_capsule_pill_arcsize50_is_exempt(tmp_path):
    issues = _issues(
        tmp_path,
        "pill.drawio",
        """
        <mxCell id="p-1234abcd" value="OIDC" style="rounded=1;arcSize=50;html=1;strokeColor=#188918;fillColor=#F5FAE5;" vertex="1" parent="1" connectable="0">
          <mxGeometry x="800" y="100" width="56" height="22" as="geometry" />
        </mxCell>
        """,
    )
    assert not [i for i in issues if i.rule == "ARC_SIZE_ABS"]


# ── --fix autofix ────────────────────────────────────────────────────────────


def test_fix_adds_label_bg_and_absolute_arcsize_but_spares_capsule(tmp_path):
    p = _write_drawio(
        tmp_path,
        "fixme.drawio",
        """
        <mxCell id="zone-arc" value="Zone" style="rounded=1;arcSize=16;fillColor=#EBF8FF;strokeColor=#0070F2;" vertex="1" parent="1">
          <mxGeometry x="800" y="100" width="500" height="200" as="geometry" />
        </mxCell>
        <mxCell id="p-1234abcd" value="OIDC" style="rounded=1;arcSize=50;html=1;strokeColor=#188918;fillColor=#F5FAE5;" vertex="1" parent="1" connectable="0">
          <mxGeometry x="800" y="360" width="56" height="22" as="geometry" />
        </mxCell>
        <mxCell id="e-labelled" value="audit events" style="edgeStyle=orthogonalEdgeStyle;html=1;endArrow=blockThin;" edge="1" parent="1" source="n-aaaaaaaa" target="n-cccccccc">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        """,
    )
    fixed, stats = validate_drawio.apply_fixes(p.read_text(encoding="utf-8"))
    assert stats == {"absoluteArcSize": 1, "labelBackgroundColor": 1}

    # Re-validating the fixed text produces no more label-bg / arc-size warnings.
    p.write_text(fixed, encoding="utf-8")
    issues = validate_drawio.validate(p)
    assert not [i for i in issues if i.rule in ("EDGE_LABEL_BG", "ARC_SIZE_ABS")]

    # The capsule pill (arcSize=50) must NOT have gained absoluteArcSize=1.
    assert 'id="p-1234abcd"' in fixed
    pill_line = next(ln for ln in fixed.splitlines() if 'id="p-1234abcd"' in ln)
    assert "absoluteArcSize" not in pill_line


def test_fix_does_not_move_bent_edge_geometry(tmp_path):
    p = _write_drawio(
        tmp_path,
        "bent-nofix.drawio",
        """
        <mxCell id="e-bent" style="edgeStyle=orthogonalEdgeStyle;html=1;endArrow=blockThin;" edge="1" parent="1" source="n-aaaaaaaa" target="n-bbbbbbbb">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        """,
    )
    fixed, stats = validate_drawio.apply_fixes(p.read_text(encoding="utf-8"))
    # No geometry rewrite: the bent edge stays a CRITICAL for the engine to fix.
    assert stats == {"absoluteArcSize": 0, "labelBackgroundColor": 0}
    p.write_text(fixed, encoding="utf-8")
    assert [i for i in validate_drawio.validate(p) if i.rule == "EDGE_BENT"]
