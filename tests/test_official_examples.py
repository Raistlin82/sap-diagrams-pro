# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""Compatibility baseline for SAP's official editable diagram examples.

The upstream examples are not treated as perfect generated output: some use
legacy root ids, palette variants, and at least one known orphan edge. These
tests only pin robust parser and shape-count invariants so local regressions in
draw.io handling show up without making CI depend on upstream cleanup.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from conftest import load_script


UPSTREAM_ROOT = Path("/tmp/SAP-btp-solution-diagrams")
EXAMPLES_DIR = UPSTREAM_ROOT / "assets" / "editable-diagram-examples"

OFFICIAL_EXAMPLES = (
    "SAP_Build_Process_Automation_L2.drawio",
    "SAP_Build_Work_Zone_L2.drawio",
    "SAP_Cloud_Identity_Services_Authentication_L2.drawio",
    "SAP_Cloud_Identity_Services_Authentication_preset_L2.drawio",
    "SAP_Cloud_Identity_Services_Authorization_L1.drawio",
    "SAP_Cloud_Identity_Services_Identity_Lifecycle_L1.drawio",
    "SAP_Private_Link_Service_L2.drawio",
    "SAP_Start_L2.drawio",
    "SAP_Task_Center_L0.drawio",
    "SAP_Task_Center_L1.drawio",
    "SAP_Task_Center_L2.drawio",
)

KNOWN_PAGE_SIZES = {
    (1169, 827),
    (2336, 1654),
}


@pytest.fixture(scope="module")
def official_examples_dir() -> Path:
    if not EXAMPLES_DIR.exists():
        pytest.skip(
            f"official SAP BTP Solution Diagrams clone not found at {UPSTREAM_ROOT}"
        )
    return EXAMPLES_DIR


@pytest.fixture(scope="module")
def drawio_io():
    return load_script("_drawio_io")


@pytest.fixture(scope="module")
def validate_drawio():
    return load_script("validate-drawio")


@pytest.fixture(scope="module")
def check_composition():
    return load_script("check-composition")


def _model_for_page(page_root):
    if page_root.tag == "mxGraphModel":
        return page_root
    return page_root.find("mxGraphModel")


def _int_attr(element, name: str) -> int | None:
    raw = element.get(name)
    if raw is None:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def _style(cell) -> str:
    return (cell.get("style") or "").lower()


def _inventory(cells: list) -> dict[str, int]:
    return {
        "cells": len(cells),
        "vertices": sum(c.get("vertex") == "1" for c in cells),
        "edges": sum(c.get("edge") == "1" for c in cells),
        "images": sum(
            "shape=image" in _style(c) or "image=" in _style(c) for c in cells
        ),
        "texts": sum(
            bool((c.get("value") or "").strip()) or _style(c).startswith("text;")
            for c in cells
        ),
    }


def _assert_root_cells_present(cells: list) -> None:
    cells_by_id = {c.get("id"): c for c in cells if c.get("id")}
    graph_roots = [
        cid
        for cid, cell in cells_by_id.items()
        if cell.get("parent") is None
        and cell.get("vertex") != "1"
        and cell.get("edge") != "1"
    ]
    assert graph_roots, "no draw.io graph root cell found"
    assert any(
        child.get("parent") in graph_roots
        and child.get("vertex") != "1"
        and child.get("edge") != "1"
        for child in cells
    ), "no draw.io layer cell parented to a graph root found"


def _assert_reasonable_inventory(counts: dict[str, int]) -> None:
    assert 30 <= counts["cells"] <= 300
    assert 20 <= counts["vertices"] <= 250
    assert 5 <= counts["edges"] <= 100
    assert 1 <= counts["images"] <= 100
    assert 10 <= counts["texts"] <= 180
    assert counts["vertices"] > counts["edges"]


def _assert_local_parsers_do_not_crash(
    path: Path, validate_drawio, check_composition
) -> None:
    issues = validate_drawio.validate(path)
    findings = check_composition.check(path)

    assert isinstance(issues, list)
    assert isinstance(findings, list)
    assert validate_drawio.render_text(issues, path)
    assert check_composition.render_text(findings, path)

    # Known upstream issues include palette variants, an orphan edge, and some
    # composition findings. Parser-level failures are the regression signal here.
    assert not any(i.rule in {"PARSE", "ROOT", "STRUCTURE", "EMPTY"} for i in issues)
    assert not any(f.rule in {"PARSE", "EMPTY"} for f in findings)


def test_expected_official_example_list_has_eleven_files(official_examples_dir: Path) -> None:
    assert len(OFFICIAL_EXAMPLES) == 11
    available = {p.name for p in official_examples_dir.glob("*.drawio")}
    assert set(OFFICIAL_EXAMPLES) <= available


@pytest.mark.parametrize("filename", OFFICIAL_EXAMPLES)
def test_official_example_compatibility_baseline(
    filename: str,
    official_examples_dir: Path,
    drawio_io,
    validate_drawio,
    check_composition,
) -> None:
    path = official_examples_dir / filename
    pages = drawio_io.decode_diagram_pages(path)

    assert pages, f"{filename} decoded to zero pages"
    assert len(pages) <= 5

    totals = {"cells": 0, "vertices": 0, "edges": 0, "images": 0, "texts": 0}
    for page_name, page_root in pages:
        model = _model_for_page(page_root)
        assert model is not None, (
            f"{filename}:{page_name or '<unnamed>'} has no mxGraphModel"
        )
        assert model.find("root") is not None, (
            f"{filename}:{page_name or '<unnamed>'} has no mxGraphModel/root"
        )

        width = _int_attr(model, "pageWidth")
        height = _int_attr(model, "pageHeight")
        assert (width, height) in KNOWN_PAGE_SIZES

        cells = list(page_root.iter("mxCell"))
        _assert_root_cells_present(cells)
        counts = _inventory(cells)
        _assert_reasonable_inventory(counts)
        for key, value in counts.items():
            totals[key] += value

    _assert_reasonable_inventory(totals)
    _assert_local_parsers_do_not_crash(path, validate_drawio, check_composition)
