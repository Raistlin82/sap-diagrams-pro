"""Tests for scripts/score-diagram.py — the SAP-likeness / corpus scorer."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NOVA = REPO / "demo" / "nova" / "nova-L1.drawio"
TASK_CENTER = REPO / "demo" / "replicas" / "task-center-L1.drawio"


def _load_module():
    path = REPO / "scripts" / "score-diagram.py"
    spec = importlib.util.spec_from_file_location("score_diagram", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclass introspection needs the module registered
    spec.loader.exec_module(mod)
    return mod


sd = _load_module()


BROKEN_DRAWIO = """<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="app.diagrams.net">
  <diagram id="broken" name="broken">
    <mxGraphModel dx="800" dy="600" grid="0" gridSize="10" pageWidth="850" pageHeight="1100" background="#1D1D1D">
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="b1" value="Some Box" style="rounded=0;fillColor=#123456;strokeColor=#654321;fontFamily=Comic Sans MS;fontColor=#FFFFFF;" vertex="1" parent="1">
          <mxGeometry x="37" y="53" width="123" height="47" as="geometry" />
        </mxCell>
        <mxCell id="b2" value="Another" style="rounded=0;fillColor=#abcdef;strokeColor=#000000;fontFamily=Times New Roman;strokeWidth=7;" vertex="1" parent="1">
          <mxGeometry x="211" y="97" width="88" height="61" as="geometry" />
        </mxCell>
        <mxCell id="e1" style="edgeStyle=none;strokeColor=#FF00FF;strokeWidth=6;" edge="1" parent="1" source="b1" target="b2">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e2" style="edgeStyle=none;strokeColor=#00FF00;strokeWidth=6;" edge="1" parent="1" source="b2" target="b1">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
"""


# --- Fingerprint ---------------------------------------------------------------


def test_fingerprint_nova_plausible():
    fp = sd.fingerprint(NOVA)
    assert fp.canvas_w == 1500 and fp.canvas_h == 1242
    assert fp.zones > 0, "expected SAP composition zones"
    assert fp.icons > 0, "expected bundled service icons"
    assert fp.icons_inline > 0
    assert fp.pills > 0
    assert fp.edges > 0
    assert fp.vertices > 0
    # Engine output sits on the 10px grid at least as well as SAP's own refs.
    assert fp.grid_snap_rate > 0.15
    assert fp.has_absolute_arc is True
    # Helvetica/Arial only.
    assert fp.fonts <= sd.SAP_FONTS
    # Palette should be dominated by Horizon colours.
    assert len(fp.palette & sd.SAP_PALETTE) >= 5
    # has_label_bg is a stable boolean either way (nova uses edge label bgs).
    assert isinstance(fp.has_label_bg, bool)
    assert fp.has_label_bg is True


def test_fingerprint_pill_vocab_split():
    fp = sd.fingerprint(NOVA)
    # Labeled pills split into canonical vs novelty; unlabeled legend swatches
    # (legpill-* with value="") count toward pills but neither bucket.
    assert fp.canonical_pill_count + fp.novelty_pill_count <= fp.pills
    assert fp.canonical_pill_count + fp.novelty_pill_count >= 1
    # At least some pills use the canonical SAP vocabulary.
    assert fp.canonical_pill_count > 0


def test_fingerprint_broken(tmp_path):
    p = tmp_path / "broken.drawio"
    p.write_text(BROKEN_DRAWIO, encoding="utf-8")
    fp = sd.fingerprint(p)
    assert fp.zones == 0
    assert fp.icons == 0
    assert fp.page_background == "#1d1d1d"
    assert "comic sans ms" in fp.fonts


# --- sap_likeness --------------------------------------------------------------


def test_sap_likeness_demo_high():
    for path in (NOVA, TASK_CENTER):
        res = sd.sap_likeness(sd.fingerprint(path))
        assert res.score >= 70, f"{path.name} scored {res.score}"


def test_sap_likeness_broken_low(tmp_path):
    p = tmp_path / "broken.drawio"
    p.write_text(BROKEN_DRAWIO, encoding="utf-8")
    res = sd.sap_likeness(sd.fingerprint(p))
    assert res.score < 50, f"broken diagram scored too high: {res.score}"
    # The obvious smells should be flagged.
    joined = " ".join(res.issues).lower()
    assert "background" in joined
    assert "zone" in joined


def test_sap_likeness_validator_errors_penalise():
    fp = sd.fingerprint(NOVA)
    clean = sd.sap_likeness(fp, validator_errors=0).score
    dirty = sd.sap_likeness(fp, validator_errors=5).score
    assert dirty < clean


# --- compare -------------------------------------------------------------------


def test_compare_identity_is_100():
    fp = sd.fingerprint(NOVA)
    assert sd.compare(fp, fp).score == 100.0
    fp2 = sd.fingerprint(TASK_CENTER)
    assert sd.compare(fp2, fp2).score == 100.0


def test_compare_different_is_lower():
    ref = sd.fingerprint(NOVA)
    cand = sd.fingerprint(TASK_CENTER)
    score = sd.compare(ref, cand).score
    assert 0.0 <= score < 100.0


def test_compare_broken_vs_demo_low(tmp_path):
    p = tmp_path / "broken.drawio"
    p.write_text(BROKEN_DRAWIO, encoding="utf-8")
    ref = sd.fingerprint(NOVA)
    broken = sd.fingerprint(p)
    assert sd.compare(ref, broken).score < sd.compare(ref, sd.fingerprint(TASK_CENTER)).score


# --- score_corpus --------------------------------------------------------------


def test_score_corpus_missing_dir_degrades():
    res = sd.score_corpus(NOVA, REPO / "assets" / "does-not-exist")
    assert res.corpus_size == 0
    assert res.score == 0.0
    assert res.matches == []


def test_score_corpus_finds_best(tmp_path):
    # Build a tiny corpus from the two committed demos.
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.drawio").write_text(NOVA.read_text(encoding="utf-8"), encoding="utf-8")
    (corpus / "b.drawio").write_text(TASK_CENTER.read_text(encoding="utf-8"), encoding="utf-8")
    # A candidate identical to nova should match "a" at 100.
    res = sd.score_corpus(NOVA, corpus, top=2)
    assert res.corpus_size == 2
    assert res.score == 100.0
    assert res.best_match.endswith("a.drawio")
    assert len(res.matches) == 2


# --- CLI -----------------------------------------------------------------------


def test_cli_sap_like_json(capsys):
    rc = sd.main(["--sap-like", str(NOVA), "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"mode": "sap-like"' in out
    assert '"score"' in out


def test_cli_corpus_min_score_gate(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.drawio").write_text(NOVA.read_text(encoding="utf-8"), encoding="utf-8")
    # Impossible threshold → nonzero exit.
    rc = sd.main(["--corpus", str(corpus), str(TASK_CENTER), "--min-score", "101"])
    assert rc == 2
    # Trivial threshold → success.
    rc = sd.main(["--corpus", str(corpus), str(NOVA), "--min-score", "50"])
    assert rc == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
