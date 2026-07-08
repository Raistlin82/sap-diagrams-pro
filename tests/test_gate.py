# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Task 12 geometric gate (scripts/check-composition.py v2).

Two invariants this file pins:

  1. The hand-built NEGATIVE fixture (tests/fixtures/bad-nova-L1.drawio) —
     every defect in it is deliberate, see the file's own header comment —
     trips >=3 DISTINCT FAIL-severity rule codes, and the CLI exits 2.

  2. Both shipped "good" fixtures — demo/nova/nova-L1.json and
     tests/fixtures/ir-v2-sample.json, freshly regenerated through
     generate-drawio.py's default (routed, "auto") layout — produce ZERO
     FAIL findings, and the CLI exits 0. This is the exact invariant Task
     18's golden CI test gates on; a regression here means Task 18 breaks.

Both use ``check()`` directly (fast, in-process) for the "what fired"
assertions, plus one subprocess-based CLI test each for the exit-code
contract (``check()`` alone can't observe ``main()``'s argv/exit-code glue).
"""
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from conftest import load_script

ROOT = Path(__file__).resolve().parent.parent
NOVA_JSON = ROOT / "demo" / "nova" / "nova-L1.json"
V2_JSON = ROOT / "tests" / "fixtures" / "ir-v2-sample.json"
BAD_FIXTURE = ROOT / "tests" / "fixtures" / "bad-nova-L1.drawio"
GATE = ROOT / "scripts" / "check-composition.py"

cc = load_script("check-composition")


@pytest.fixture(scope="module")
def gen():
    return load_script("generate-drawio")


def _emit(gen, path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    diagram = gen.parse_json(payload)
    return gen.emit(diagram, shape_index=gen.ShapeIndex.load(), layout="auto")


@pytest.fixture(scope="module")
def nova_drawio(tmp_path_factory, gen):
    p = tmp_path_factory.mktemp("gate") / "nova-L1.drawio"
    p.write_text(_emit(gen, NOVA_JSON), encoding="utf-8")
    return p


@pytest.fixture(scope="module")
def v2_drawio(tmp_path_factory, gen):
    p = tmp_path_factory.mktemp("gate") / "ir-v2.drawio"
    p.write_text(_emit(gen, V2_JSON), encoding="utf-8")
    return p


def _run_cli(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), str(path)],
        capture_output=True, text=True,
    )


# ── the hand-built bad fixture must FAIL loudly, on >=3 distinct codes ──────
def test_bad_fixture_trips_at_least_three_distinct_fail_codes():
    findings = cc.check(BAD_FIXTURE)
    fail_codes = {f.rule for f in findings if f.severity == "FAIL"}
    assert len(fail_codes) >= 3, f"expected >=3 distinct FAIL codes, got {fail_codes}"
    # The 4 defects the fixture's header comment documents, each mapped to
    # its own rule — pin the exact codes, not just the count, so a future
    # refactor that silently drops one FAIL-level check is caught here.
    assert {"PIERCING", "TEXT_OVERLAP", "CAPTION_OUT", "PILL_COLLISION"} <= fail_codes


def test_bad_fixture_cli_exits_2():
    result = _run_cli(BAD_FIXTURE)
    assert result.returncode == 2, result.stdout + result.stderr


def test_bad_fixture_exits_2_without_strict_flag():
    """A FAIL blocks unconditionally now — ``--strict`` is a deprecated
    no-op (see check-composition.py's module docstring); the CI workflow
    still passes it, so this pins that it keeps working AND that omitting
    it no longer silently downgrades a FAIL to exit 0."""
    result = subprocess.run(
        [sys.executable, str(GATE), str(BAD_FIXTURE), "--strict"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2


# ── both shipped GOOD fixtures must produce ZERO FAIL ───────────────────────
def test_nova_l1_zero_fail(nova_drawio):
    findings = cc.check(nova_drawio)
    fails = [f for f in findings if f.severity == "FAIL"]
    assert fails == [], f"nova-L1 must have 0 FAIL, got: {fails}"


def test_nova_l1_cli_exits_0(nova_drawio):
    result = _run_cli(nova_drawio)
    assert result.returncode == 0, result.stdout + result.stderr


def test_ir_v2_zero_fail(v2_drawio):
    findings = cc.check(v2_drawio)
    fails = [f for f in findings if f.severity == "FAIL"]
    assert fails == [], f"ir-v2-sample must have 0 FAIL, got: {fails}"


def test_ir_v2_cli_exits_0(v2_drawio):
    result = _run_cli(v2_drawio)
    assert result.returncode == 0, result.stdout + result.stderr


# ── the router's channels ARE serialized (Task 12 prefers this over a
#    WARN-only degrade for CHANNEL_DISCIPLINE) ──────────────────────────────
def test_channels_metadata_is_serialized(nova_drawio):
    root = ET.parse(nova_drawio).getroot()
    cells = {c.get("id"): c for d in root.findall("diagram") for c in d.iter("mxCell")}
    assert "sapdp:channels" in cells
    channels = json.loads(cells["sapdp:channels"].get("value"))
    assert isinstance(channels, list) and len(channels) > 0
    for ch in channels:
        assert set(ch) == {"id", "axis", "rect"}
        assert ch["axis"] in ("v", "h")
        assert len(ch["rect"]) == 4


def test_channel_discipline_is_not_a_silent_no_op_on_nova(nova_drawio):
    """CHANNEL_DISCIPLINE must actually run (not degrade to the "metadata
    absent" INFO no-op) whenever the metadata cell is present."""
    findings = cc.check(nova_drawio)
    cd = [f for f in findings if f.rule == "CHANNEL_DISCIPLINE"]
    assert cd, "CHANNEL_DISCIPLINE produced no finding at all"
    assert "no sapdp:channels metadata" not in cd[0].message


# ── greedy layout (no router, no channels) must degrade gracefully ─────────
def test_channel_discipline_degrades_gracefully_without_metadata(gen, tmp_path):
    payload = json.loads(NOVA_JSON.read_text(encoding="utf-8"))
    diagram = gen.parse_json(payload)
    xml = gen.emit(diagram, shape_index=gen.ShapeIndex.load(), layout="greedy")
    root = ET.fromstring(xml)
    cells = [c for d in root.findall("diagram") for c in d.iter("mxCell")]
    assert not any(c.get("id") == "sapdp:channels" for c in cells)
    assert not any(c.get("id") == "sapdp:node_obstacles" for c in cells)
    findings = cc.check(_write_tmp(tmp_path, xml))
    cd = [f for f in findings if f.rule == "CHANNEL_DISCIPLINE"]
    assert len(cd) == 1 and cd[0].severity == "INFO"
    # FIX-1 (review round): no sapdp:node_obstacles metadata ⇒ PIERCING/
    # CAPTION_OUT fall back gracefully and say so, rather than silently
    # reconstructing rects with no way to tell they're an approximation.
    no = [f for f in findings if f.rule == "NODE_OBSTACLES"]
    assert len(no) == 1 and no[0].severity == "INFO"


def _write_tmp(tmp_path: Path, xml: str) -> Path:
    """Write ``xml`` to a fresh file under pytest's per-test ``tmp_path`` —
    FIX-4(c) (review round): the previous ``tempfile.mkstemp`` helper never
    cleaned up its file, leaking one ``*.drawio`` into the OS temp dir per
    test run; ``tmp_path`` is torn down by pytest automatically."""
    p = tmp_path / "greedy.drawio"
    p.write_text(xml, encoding="utf-8")
    return p


# ── existing v1 checks must not have regressed ──────────────────────────────
def test_v1_group_overlap_still_fail_severity():
    findings = cc.check(BAD_FIXTURE)
    # bad-nova-L1.drawio's two zones don't overlap each other — this just
    # pins that GROUP_OVERLAP's INFO/FAIL machinery still runs alongside v2.
    go = [f for f in findings if f.rule == "GROUP_OVERLAP"]
    assert go and go[0].severity == "INFO"


# ── FIX-1 (review round): the router's own node-obstacle rects are
#    serialized and the gate reads them verbatim — no reconstruction ────────
def test_node_obstacles_metadata_is_serialized(nova_drawio):
    """Same pattern as ``test_channels_metadata_is_serialized`` — pins the
    NEW ``sapdp:node_obstacles`` metadata cell generate-drawio.py's
    ``_emit_node_obstacles_metadata`` publishes."""
    root = ET.parse(nova_drawio).getroot()
    cells = {c.get("id"): c for d in root.findall("diagram") for c in d.iter("mxCell")}
    assert "sapdp:node_obstacles" in cells
    obstacles = json.loads(cells["sapdp:node_obstacles"].get("value"))
    assert isinstance(obstacles, dict) and len(obstacles) > 0
    node_ids = {cid for cid in cells if cid and cid.startswith("n-")}
    for cid, rect in obstacles.items():
        assert cid in node_ids, f"{cid} in sapdp:node_obstacles isn't a real node cell"
        assert len(rect) == 4


def test_node_obstacles_metadata_absent_on_check_of_stale_pre_metadata_file():
    """bad-nova-L1.drawio predates ``sapdp:node_obstacles`` (hand-built, no
    router ran) — the gate must say so via a NODE_OBSTACLES INFO finding and
    still run PIERCING/CAPTION_OUT off the fallback reconstruction (both
    still fire — see ``test_bad_fixture_trips_at_least_three_distinct_fail_codes``)."""
    findings = cc.check(BAD_FIXTURE)
    no = [f for f in findings if f.rule == "NODE_OBSTACLES"]
    assert len(no) == 1 and no[0].severity == "INFO"
    assert "sapdp:node_obstacles" in no[0].message


_OBSTACLE_FIXTURE_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="test-fixture" version="24.7.8">
  <diagram id="p1" name="P1">
    <mxGraphModel dx="800" dy="600" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="900" pageHeight="600" math="0" shadow="0">
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="n-cccccccc" value="Icon C" style="shape=image;html=1;verticalLabelPosition=bottom;verticalAlign=top;imageAspect=0;aspect=fixed;image=data:image/png,;" vertex="1" parent="1">
          <mxGeometry x="20" y="20" width="48" height="48" as="geometry" />
        </mxCell>
        <mxCell id="n-eeeeeeee" value="Src" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="0" y="95" width="10" height="10" as="geometry" />
        </mxCell>
        <mxCell id="n-dddddddd" value="Dst" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="200" y="95" width="10" height="10" as="geometry" />
        </mxCell>
        <mxCell id="e-xxxxxxxx" value="" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;exitX=1.0;exitY=0.5;entryX=0.0;entryY=0.5;" edge="1" parent="1" source="n-eeeeeeee" target="n-dddddddd">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        {obstacle_cell}
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
"""


def test_piercing_reads_router_exact_rect_not_a_reconstruction(tmp_path):
    """The crux of FIX-1: n-cccccccc's DRAWN icon rect is (20,20,48,48) —
    bottom at y=68. The old hardcoded 24px reconstruction would build an
    obstacle bottoming out at y=92; the router's REAL obstacle (published
    here as h=88, i.e. a 40px caption band) bottoms out at y=108. The edge
    e-xxxxxxxx runs straight across y=100 (x: 10→200) — a height a fallback
    24px reconstruction CANNOT reach, but the router's actual (metadata)
    rect does. This is exactly the drift scenario the FIX-1 metadata cell
    exists to close: only the real published rect catches it."""
    xml = _OBSTACLE_FIXTURE_TEMPLATE.format(
        obstacle_cell=(
            '<mxCell id="sapdp:node_obstacles" '
            'value="{&quot;n-cccccccc&quot;: [20, 20, 48, 88]}" '
            'style="text;html=0;" vertex="1" parent="1" visible="0">'
            '<mxGeometry x="0" y="0" width="1" height="1" as="geometry" />'
            "</mxCell>"
        )
    )
    p = tmp_path / "with-metadata.drawio"
    p.write_text(xml, encoding="utf-8")
    findings = cc.check(p)
    piercing_fail = [f for f in findings if f.rule == "PIERCING" and f.severity == "FAIL"]
    assert piercing_fail, "expected a PIERCING FAIL driven by the metadata rect"
    assert "n-cccccccc" in piercing_fail[0].message
    no = [f for f in findings if f.rule == "NODE_OBSTACLES"]
    assert no == [], "metadata is present — no fallback-degrade INFO expected"


def test_piercing_fallback_without_metadata_misses_the_same_case(tmp_path):
    """Same geometry as the previous test, MINUS the metadata cell — proves
    the fallback reconstruction (24px) genuinely is just an approximation:
    it does NOT reach y=100, so this specific piercing goes uncaught without
    router-published ground truth. Documents the exact gap FIX-1 closes."""
    xml = _OBSTACLE_FIXTURE_TEMPLATE.format(obstacle_cell="")
    p = tmp_path / "without-metadata.drawio"
    p.write_text(xml, encoding="utf-8")
    findings = cc.check(p)
    piercing_fail = [f for f in findings if f.rule == "PIERCING" and f.severity == "FAIL"]
    assert piercing_fail == [], (
        "the 24px fallback approximation should NOT catch a piercing that "
        "only exists in the router's real (40px-band) obstacle rect"
    )
    no = [f for f in findings if f.rule == "NODE_OBSTACLES"]
    assert len(no) == 1 and no[0].severity == "INFO"


# ── FIX-2 (review round): pin the TEXT_OVERLAP FAIL/WARN threshold and the
#    zone-header WARN cap, so a regression moving either can't slip by ─────
_TWO_RECTS_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="test-fixture" version="24.7.8">
  <diagram id="p1" name="P1">
    <mxGraphModel dx="800" dy="600" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="900" pageHeight="600" math="0" shadow="0">
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        {extra}
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
"""

_TEXT_CELL = (
    '<mxCell id="{id}" value="{value}" '
    'style="text;html=1;fontSize=11;fontColor=#1D2D3E;align=left;verticalAlign=middle;" '
    'vertex="1" parent="1"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" '
    'as="geometry" /></mxCell>'
)


def _two_text_rects(tmp_path, name, bx):
    """Two 100×10 text-bearing rects: A at x=0, B at x=``bx`` — the overlap
    fraction (of the smaller rect's 1000px² area) is ``(100 - bx) / 100``."""
    xml = _TWO_RECTS_TEMPLATE.format(extra="\n".join([
        _TEXT_CELL.format(id="leglbl-11110000", value="A", x=0, y=0, w=100, h=10),
        _TEXT_CELL.format(id="leglbl-22220000", value="B", x=bx, y=0, w=100, h=10),
    ]))
    p = tmp_path / name
    p.write_text(xml, encoding="utf-8")
    return p


def test_text_overlap_below_threshold_is_warn_not_fail(tmp_path):
    """10% overlap (< the 20% TEXT_OVERLAP_FAIL_FRAC threshold) ⇒ WARN."""
    findings = cc.check(_two_text_rects(tmp_path, "warn.drawio", bx=90))
    fails = [f for f in findings if f.rule == "TEXT_OVERLAP" and f.severity == "FAIL"]
    warns = [f for f in findings if f.rule == "TEXT_OVERLAP" and f.severity == "WARN"]
    assert fails == [], f"10% overlap must not FAIL, got: {fails}"
    assert warns, "10% overlap should still surface as a WARN graze"


def test_text_overlap_at_threshold_is_fail(tmp_path):
    """Exactly 20% overlap (frac >= TEXT_OVERLAP_FAIL_FRAC) ⇒ FAIL."""
    findings = cc.check(_two_text_rects(tmp_path, "fail.drawio", bx=80))
    fails = [f for f in findings if f.rule == "TEXT_OVERLAP" and f.severity == "FAIL"]
    assert fails, "20% overlap must trip the FAIL threshold"


def test_text_overlap_involving_zone_header_stays_warn(tmp_path):
    """A >=20% overlap with a zone's ``#header`` band is capped at WARN,
    never FAIL — a titled zone plus a text label planted on top of its
    title band, overlapping the label's entire area with the header."""
    xml = _TWO_RECTS_TEMPLATE.format(extra="\n".join([
        '<mxCell id="g-77778888" value="Zone H" '
        'style="rounded=1;whiteSpace=wrap;html=1;verticalAlign=top;align=left;'
        'fontStyle=1;fontSize=14;" vertex="1" parent="1">'
        '<mxGeometry x="0" y="0" width="300" height="300" as="geometry" /></mxCell>',
        _TEXT_CELL.format(id="leglbl-33330000", value="On Header", x=10, y=5, w=50, h=20),
    ]))
    p = tmp_path / "header-overlap.drawio"
    p.write_text(xml, encoding="utf-8")
    findings = cc.check(p)
    fails = [f for f in findings if f.rule == "TEXT_OVERLAP" and f.severity == "FAIL"]
    warns = [f for f in findings if f.rule == "TEXT_OVERLAP" and f.severity == "WARN"
              and "#header" in f.message]
    assert fails == [], f"a zone-header overlap must never FAIL, got: {fails}"
    assert warns, "the header overlap should still surface as a WARN"


# ── FIX-3 (review round): dedupe findings the Task 14 loop consumes ─────────
def test_piercing_dedupes_same_edge_obstacle_pair(tmp_path):
    """A path that bends INSIDE a single obstacle box crosses it across two
    consecutive segments — that's one physical piercing, not two. Before
    FIX-3(a), ``piercing_hits`` counted both segments; now it's deduped on
    (eid, oid)."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="test-fixture" version="24.7.8">
  <diagram id="p1" name="P1">
    <mxGraphModel dx="800" dy="600" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="900" pageHeight="600" math="0" shadow="0">
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="n-aaaa1111" value="Src" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="40" y="90" width="20" height="20" as="geometry" />
        </mxCell>
        <mxCell id="n-bbbb2222" value="Dst" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="140" y="290" width="20" height="20" as="geometry" />
        </mxCell>
        <mxCell id="n-cccc3333" value="Obstacle" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="100" y="50" width="100" height="100" as="geometry" />
        </mxCell>
        <mxCell id="e-dddd4444" value="" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;exitX=0.5;exitY=0.5;entryX=0.5;entryY=0.5;" edge="1" parent="1" source="n-aaaa1111" target="n-bbbb2222">
          <mxGeometry relative="1" as="geometry">
            <Array as="points">
              <mxPoint x="150" y="100" />
            </Array>
          </mxGeometry>
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
"""
    p = tmp_path / "dedupe-piercing.drawio"
    p.write_text(xml, encoding="utf-8")
    findings = cc.check(p)
    piercing_fail = [f for f in findings if f.rule == "PIERCING" and f.severity == "FAIL"]
    assert len(piercing_fail) == 1
    assert piercing_fail[0].message.startswith("1 edge/box intersection"), piercing_fail[0].message


def test_pill_pill_overlap_is_pill_collision_only_not_also_text_overlap():
    """FIX-3(b): the bad fixture's p-55555555/p-66666666 pill pair overlaps
    substantially — it must show up in PILL_COLLISION, and must NOT also be
    double-counted as a TEXT_OVERLAP finding for the same pair (the fixture
    carries a SEPARATE leglbl-*/leglbl-* pair specifically to keep
    TEXT_OVERLAP independently exercised — see the fixture's header comment)."""
    findings = cc.check(BAD_FIXTURE)
    pill_collision = [f for f in findings if f.rule == "PILL_COLLISION" and f.severity == "FAIL"]
    assert pill_collision and "p-55555555" in pill_collision[0].message
    text_overlap_fail = [f for f in findings if f.rule == "TEXT_OVERLAP" and f.severity == "FAIL"]
    assert text_overlap_fail, "expected the leglbl-*/leglbl-* pair to still trip TEXT_OVERLAP"
    for f in text_overlap_fail:
        assert "p-55555555" not in f.message and "p-66666666" not in f.message
