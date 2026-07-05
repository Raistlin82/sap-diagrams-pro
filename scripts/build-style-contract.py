#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""build-style-contract.py — extract the canonical molecule style contract.

The contract (assets/style-contract.json) is the single source of truth for
every diagram molecule's draw.io style + reference geometry, so the downstream
engine (molecules / skeleton / router — Tasks 5/6/8) never hardcodes a style.
Styles are read from SAP's official ``btp-solution-diagrams`` shape libraries
(``--official-repo``) and the Lutech exemplar ``.drawio`` files (``--exemplar``),
then normalized:

  * fill/stroke/font colors → uppercase SAP Horizon hex (or ``none``); a small,
    documented snap map fixes SAP's own near-Horizon typos
    (#0070F3→#0070F2, #ECF8FF→#EBF8FF, #CB00DC→#CC00DC, pill text #266F3A→#188918)
    and expands ``default`` (fontColor→#1D2D3E, fillColor→#FFFFFF); anything that
    still falls outside the palette raises and FAILS the run.
  * ``image=<data|url>`` payloads → ``image=@{asset-key}`` placeholders resolved
    from the brand pack at emit time. NO base64 is ever written to the contract.
  * ``points=[…]`` connection hints and edge routing anchors (exit*/entry*) are
    dropped so edge/vertex styles are reusable families, not per-instance cells.

Geometry numbers come from the source cells' mxGeometry; derived paddings
(padX/padTop/titleRow/gap) are measured from the exemplar child offsets and the
derivation is recorded in each molecule's ``notes``.

Selection is deterministic and documented per key in EXTRACTORS below. If any
required molecule cannot be resolved the run FAILS loudly listing the key(s) —
contract completeness is non-negotiable, no silent placeholder styles.

┌───────────────────── extraction mapping (KEY → source) ─────────────────────┐
│ title-block        official essentials.xml   "Diagram title" (text cell)     │
│ btp-area           official essentials.xml   "BTP base layer" outer area     │
│ backend-box        official essentials.xml   "On-Premise" (202x70) box       │
│ persona            official essentials.xml   "User and client" avatar image  │
│ legend             official essentials.xml   "Legend" container              │
│ pill-interface     official annotations_…    "Interface SAP" chip            │
│ step-circle        official numbers.xml      "default number" ellipse (30)   │
│ sap-btp-chip       official sap_brand_names   "SAP BTP (Text Only)" text      │
│ service-icon       official …-all-size-M      first service image cell (32)  │
│ badge-runtime      official …-all-size-M      service image skeleton→@runtime │
│ edge-default       official connectors.xml    "direct one-directional"       │
│ edge-firewall      official connectors.xml    "default firewall" (thick grey)│
│ subaccount-frame   exemplar SSAM      tightest white frame of "Subaccount:   │
│                                       Extension …" (blue border)             │
│ governance-strip   exemplar SSAM      outer BTP box of "Subaccount:          │
│                                       Governance" (EBF8FF)                    │
│ product-box        exemplar SSAM      box of "SAP Build Process Automation"  │
│ capability-chip    exemplar SSAM      white rounded child panel of the SBPA  │
│                                       box enclosing 'Decision' + icon grid   │
│ tier-box-sap       exemplar SSAM      box of "Public Cloud" (blue border)    │
│ tier-box-nonsap    exemplar SSAM      box of "Any-Premise", recolored to the │
│                                       official non-SAP grey #475E75          │
│ chip               exemplar SNAM      "PAS" client chip (blue, white fill)   │
│ db                 exemplar SNAM      cylinder fallback (SNAM DBs are chips)  │
│ badge-hyperscaler  exemplar BRANDART  Azure/AWS logo image → @{hyperscaler}  │
│ watermark          exemplar BRANDART  Lutech opacity image → @{watermark}    │
│ custom-app-box     exemplar BRANDART  box of "Procurement Application"       │
│ pill-protocol      exemplar SSAM      green "SAML2/OIDC" pill (arcSize 50)   │
│ network-separator  official example   SAP_Task_Center_L1 NETWORK bar + label │
│ edge-identity      exemplar SSAM      green #188918 edge                     │
│ edge-provisioning  exemplar SSAM      purple #470BED (SCIM) edge             │
│ edge-master-data   exemplar SSAM      magenta #CC00DC edge (#CB00DC→#CC00DC) │
│ edge-transport     exemplar SSAM      dashed grey edge (dashed=1)            │
└─────────────────────────────────────────────────────────────────────────────┘

Usage:
    python3 build-style-contract.py \\
        --official-repo ~/tools/btp-solution-diagrams \\
        --exemplar SSAM.drawio --exemplar brandart.drawio --exemplar SNAM.drawio \\
        --date 2026-07-05 \\
        --out assets/style-contract.json \\
        --schema assets/style-contract.schema.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from _drawio_io import decode_diagram_pages, parse_entry_cells, parse_mxlibrary

SCRIPT_VERSION = "1.0.0"
LIB_SUBPATH = Path("assets/shape-libraries-and-editable-presets/draw.io")
EXAMPLES_SUBPATH = Path("assets/editable-diagram-examples")
SERVICE_LIB = "20-02-99-sap-btp-service-icons-all/20-02-99-02-sap-btp-service-icons-all-size-M.xml"
GOLD_FILE = "SAP_Task_Center_L1.drawio"

# SAP Horizon palette — kept in lockstep with tests/test_style_contract.py.
HORIZON = {
    "#0070F2", "#EBF8FF", "#475E75", "#F5F6F7", "#1D2D3E", "#556B82", "#188918",
    "#F5FAE5", "#C35500", "#FFF8D6", "#D20A0A", "#FFEAF4", "#07838F", "#DAFDF5",
    "#5D36FF", "#F1ECFF", "#CC00DC", "#FFF0FA", "#470BED", "#5B738B", "#FFFFFF",
}
# Documented snaps for SAP's own near-Horizon deviations (uppercased first).
SNAP = {
    "#0070F3": "#0070F2",   # SAP primary blue: 1-digit library typo
    "#ECF8FF": "#EBF8FF",   # BTP area tint: exemplar typo of the Horizon tint
    "#CB00DC": "#CC00DC",   # master-data magenta: some SSAM cells use CB
    "#266F3A": "#188918",   # green pill label text → Horizon success green
    "#0A74F3": "#0070F2",   # alt SAP blue seen in some rich-text values
    "#0070F3;": "#0070F2",  # (guard; not expected after tokenizing)
}
# 'default' color expands to its concrete Horizon value, per attribute.
DEFAULT_COLOR = {"fontColor": "#1D2D3E", "fillColor": "#FFFFFF", "strokeColor": "#475E75"}

# Style tokens dropped everywhere (connection hints) / on edges (routing anchors).
DROP_ALWAYS = {"points"}
DROP_EDGE = {
    "exitX", "exitY", "exitDx", "exitDy", "exitPerimeter",
    "entryX", "entryY", "entryDx", "entryDy", "entryPerimeter",
}


class ContractError(Exception):
    """A molecule could not be resolved / normalized — fails the run."""


# ── number / color / style normalization ─────────────────────────────────
def num(x) -> float | int:
    r = round(float(x), 2)
    return int(r) if r == int(r) else r


def _snap(hexcol: str) -> str:
    up = hexcol.upper()
    return SNAP.get(up, up)


def normalize_color(attr: str, value: str, molecule: str) -> str:
    v = value.strip()
    if v == "none":
        return "none"
    if v == "default":
        return DEFAULT_COLOR.get(attr, "#1D2D3E")
    if v.startswith("#"):
        snapped = _snap(v)
        if snapped not in HORIZON:
            raise ContractError(
                f"{molecule}: {attr}={value} normalizes to {snapped}, off the Horizon palette"
            )
        return snapped
    # Named CSS/theme tokens (e.g. 'red') are never part of the Horizon design
    # language — reject them so the palette promise holds for every guarded attr.
    raise ContractError(
        f"{molecule}: {attr}={value!r} is not a Horizon hex color, 'none', or 'default'"
    )


def _drop_tokens(style: str, keys: set[str]) -> str:
    kept = [p for p in style.split(";") if p.split("=", 1)[0] not in keys]
    return ";".join(kept)


def _replace_image(style: str, placeholder: str) -> str:
    # Normalize the standard ';base64,' marker away first so the payload holds
    # no ';' and the whole image=… value is a single token, then swap it for
    # the placeholder. Handles data: and http(s): image sources alike.
    s = style.replace(";base64,", ",")
    return re.sub(r"image=[^;]*", f"image=@{{{placeholder}}}", s)


def normalize_style(
    raw: str, molecule: str, *, image_placeholder: str | None = None, is_edge: bool = False
) -> str:
    s = raw.strip()
    if image_placeholder is not None:
        s = _replace_image(s, image_placeholder)
    drop = set(DROP_ALWAYS)
    if is_edge:
        drop |= DROP_EDGE
    s = _drop_tokens(s, drop)
    # Uppercase every hex color so gradientColor/others are consistent too.
    s = re.sub(r"#[0-9a-fA-F]{6}", lambda m: m.group(0).upper(), s)
    # Snap + validate the palette-guarded colors and expand default.
    s = re.sub(
        r"(fillColor|strokeColor|fontColor)=([^;]+)",
        lambda m: f"{m.group(1)}={normalize_color(m.group(1), m.group(2), molecule)}",
        s,
    )
    # Collapse any doubled ';' left by token drops.
    s = re.sub(r";;+", ";", s).lstrip(";")
    return s


def color_eq(raw_style_color: str | None, want: str) -> bool:
    if not raw_style_color:
        return False
    return _snap(raw_style_color) == _snap(want)


def style_attr(style: str, key: str) -> str | None:
    m = re.search(rf"(?:^|;){re.escape(key)}=([^;]*)", style or "")
    return m.group(1) if m else None


# ── official library access ───────────────────────────────────────────────
def _cell_geom(cell: ET.Element) -> dict:
    g = cell.find("mxGeometry")
    if g is None:
        return {}
    return {k: float(g.get(k)) for k in ("x", "y", "width", "height") if g.get(k) is not None}


class Official:
    def __init__(self, repo: Path):
        self.repo = Path(repo)
        self.libdir = self.repo / LIB_SUBPATH
        self._cache: dict[str, list[dict] | None] = {}

    def entries(self, filename: str) -> list[dict]:
        if filename not in self._cache:
            self._cache[filename] = parse_mxlibrary(self.libdir / filename)
        entries = self._cache[filename]
        if entries is None:
            raise ContractError(f"cannot read official library {filename!r}")
        return entries

    def entry_cells(self, filename: str, title: str) -> tuple[dict, list[ET.Element]]:
        """Return (entry, [mxCell,…]) for the first entry titled ``title``."""
        for e in self.entries(filename):
            if (e.get("title") or "") == title:
                root = parse_entry_cells(e.get("xml") or "")
                if root is None:
                    raise ContractError(f"{filename}:{title!r} xml did not parse")
                return e, list(root.iter("mxCell"))
        raise ContractError(f"{filename}: entry {title!r} not found")

    def first_entry_cells(self, filename: str) -> tuple[dict, list[ET.Element]]:
        entries = self.entries(filename)
        if not entries:
            raise ContractError(f"{filename}: empty library")
        e = entries[0]
        root = parse_entry_cells(e.get("xml") or "")
        if root is None:
            raise ContractError(f"{filename}: first entry xml did not parse")
        return e, list(root.iter("mxCell"))


def pick(cells: list[ET.Element], predicate) -> ET.Element:
    for c in cells:
        if predicate(c):
            return c
    raise ContractError("no cell matched predicate")


def is_text(c: ET.Element) -> bool:
    return (c.get("style") or "").startswith("text;")


def is_image(c: ET.Element) -> bool:
    return "shape=image" in (c.get("style") or "")


def is_vertex_box(c: ET.Element) -> bool:
    st = c.get("style") or ""
    return c.get("vertex") == "1" and st not in ("", "group") and not is_text(c) and not is_image(c)


# ── exemplar corpus access (flat cells with absolute coordinates) ──────────
_HTML = re.compile(r"<[^>]+>")


class Cell:
    __slots__ = ("id", "value", "style", "x", "y", "w", "h", "parent", "edge", "elem", "image_val")

    def __init__(self, cid, value, style, x, y, w, h, parent, edge, elem, image_val):
        self.id, self.value, self.style = cid, value, style
        self.x, self.y, self.w, self.h = x, y, w, h
        self.parent, self.edge, self.elem, self.image_val = parent, edge, elem, image_val

    @property
    def vclean(self) -> str:
        return _HTML.sub("", self.value or "").strip()

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def area(self) -> float:
        return self.w * self.h


class Corpus:
    def __init__(self, path: Path, role: str):
        self.path = Path(path)
        self.role = role
        self.cells: list[Cell] = []
        self._load()

    def _load(self):
        for _name, page in decode_diagram_pages(self.path):
            parents = {ch: pa for pa in page.iter() for ch in pa}
            raw: dict[str, ET.Element] = {}
            geoms: dict[str, dict] = {}
            values: dict[str, str] = {}
            order: list[str] = []
            for c in page.iter("mxCell"):
                cid = c.get("id")
                if cid is None:
                    continue
                raw[cid] = c
                geoms[cid] = _cell_geom(c)
                val = c.get("value") or ""
                w = parents.get(c)
                if not val and w is not None and w.tag in ("object", "UserObject"):
                    val = w.get("label") or ""
                values[cid] = val
                order.append(cid)

            def absxy(cid: str) -> tuple[float, float]:
                x = geoms[cid].get("x", 0.0)
                y = geoms[cid].get("y", 0.0)
                pid = raw[cid].get("parent")
                seen: set[str] = set()
                while pid in raw and pid not in seen:
                    seen.add(pid)
                    g = geoms[pid]
                    if "x" in g:
                        x += g["x"]
                        y += g["y"]
                    pid = raw[pid].get("parent")
                return x, y

            for cid in order:
                g = geoms[cid]
                ax, ay = absxy(cid)
                style = raw[cid].get("style") or ""
                image_val = None
                if "image=" in style:
                    m = re.search(r"image=([^;]*)", style.replace(";base64,", ","))
                    image_val = m.group(1) if m else None
                self.cells.append(Cell(
                    cid, values[cid], style, ax, ay,
                    g.get("width", 0.0), g.get("height", 0.0),
                    raw[cid].get("parent"), raw[cid].get("edge"), raw[cid], image_val))

    def labels(self, value_regex: str) -> list[Cell]:
        pat = re.compile(value_regex, re.I)
        return [c for c in self.cells if pat.search(c.vclean)]

    def by_value(self, value_regex: str) -> list[Cell]:
        return self.labels(value_regex)

    def rects_containing(self, cx: float, cy: float, exclude: Cell) -> list[Cell]:
        out = []
        for c in self.cells:
            if c is exclude or c.edge == "1" or c.w <= 0 or c.h <= 0:
                continue
            if is_text(c.elem) or c.style == "group":
                continue
            if c.x <= cx <= c.x + c.w and c.y <= cy <= c.y + c.h and c.area > 0:
                out.append(c)
        out.sort(key=lambda c: c.area)
        return out

    def container_of(self, value_regex: str, *, pick_="smallest", fill=None) -> tuple[Cell, Cell]:
        labs = self.labels(value_regex)
        if not labs:
            raise ContractError(f"{self.role}: no label matching {value_regex!r}")
        for lab in labs:
            cands = self.rects_containing(lab.cx, lab.cy, exclude=lab)
            if fill is not None:
                cands = [c for c in cands if color_eq(style_attr(c.style, "fillColor"), fill)]
            if cands:
                chosen = cands[0] if pick_ == "smallest" else cands[-1]
                return lab, chosen
        raise ContractError(f"{self.role}: no container rect for {value_regex!r}")

    def edge_by_stroke(self, stroke: str, *, dashed=None, extra=None) -> Cell:
        for c in self.cells:
            if c.edge != "1":
                continue
            if not color_eq(style_attr(c.style, "strokeColor"), stroke):
                continue
            if dashed is not None and (("dashed=1" in c.style) != dashed):
                continue
            if extra is not None and extra not in c.style:
                continue
            return c
        raise ContractError(f"{self.role}: no edge strokeColor≈{stroke} dashed={dashed} extra={extra}")


def edge_span(cell: Cell) -> tuple[float, float]:
    g = cell.elem.find("mxGeometry")
    if g is None:
        return 0.0, 0.0
    pts = {p.get("as"): (float(p.get("x", 0)), float(p.get("y", 0))) for p in g.findall("mxPoint")}
    if "sourcePoint" in pts and "targetPoint" in pts:
        (sx, sy), (tx, ty) = pts["sourcePoint"], pts["targetPoint"]
        return abs(sx - tx), abs(sy - ty)
    return 0.0, 0.0


# ── build context ─────────────────────────────────────────────────────────
class Ctx:
    def __init__(self, official: Official, ex: dict[str, Corpus], gold: Corpus):
        self.official = official
        self.ex = ex          # role -> Corpus  (ssam / brandart / snam)
        self.gold = gold

    def need(self, role: str) -> Corpus:
        c = self.ex.get(role)
        if c is None:
            raise ContractError(f"required exemplar {role!r} not provided")
        return c


def _spec(style, geometry, source, notes, frm, **kw):
    return {"raw": style, "geometry": geometry, "source": source, "notes": notes,
            "from": frm, **kw}


# ── EXTRACTORS (deterministic, documented per key) ────────────────────────
def x_title_block(c: Ctx):
    _e, cells = c.official.entry_cells("essentials.xml", "Diagram title")
    cell = pick(cells, is_text)
    g = _cell_geom(cell)
    return _spec(cell.get("style"), {"w": num(g["width"]), "h": num(g["height"])},
                 "official", "essentials.xml 'Diagram title' text cell; fontColor #1d2d3e→#1D2D3E.",
                 "essentials.xml:Diagram title")


def x_btp_area(c: Ctx):
    _e, cells = c.official.entry_cells("essentials.xml", "BTP base layer")
    area = pick(cells, lambda x: is_vertex_box(x) and "fillColor=#EBF8FF" in (x.get("style") or ""))
    logo = next((x for x in cells if is_image(x)), None)
    g = _cell_geom(area)
    geom = {"w": num(g["width"]), "h": num(g["height"])}
    note = "essentials.xml 'BTP base layer' outer blue area (arcSize=32)."
    if logo is not None:
        lg = _cell_geom(logo)
        geom.update({"logoX": num(lg.get("x", 0)), "logoY": num(lg.get("y", 0)),
                     "logoW": num(lg.get("width", 0)), "logoH": num(lg.get("height", 0))})
        note += (" The SAP-logo chip is a separate child image cell at "
                 f"({num(lg.get('x',0))},{num(lg.get('y',0))}) "
                 f"{num(lg.get('width',0))}x{num(lg.get('height',0))} (see brand pack @{{sap-logo-chip}}).")
    return _spec(area.get("style"), geom, "official", note, "essentials.xml:BTP base layer")


def x_backend_box(c: Ctx):
    e, cells = c.official.entry_cells("essentials.xml", "On-Premise")
    box = pick(cells, is_vertex_box)
    icon = next((x for x in cells if is_image(x)), None)
    txt = next((x for x in cells if is_text(x)), None)
    geom = {"w": num(e["w"]), "h": num(e["h"])}
    note = "essentials.xml 'On-Premise' rounded box (202x70 group; inner rect 197x70)."
    if icon is not None and txt is not None:
        ig, tg = _cell_geom(icon), _cell_geom(txt)
        geom.update({"padX": num(ig.get("x", 0)), "iconW": num(ig.get("width", 0)),
                     "iconH": num(ig.get("height", 0)), "textX": num(tg.get("x", 0))})
        note += (f" Icon inset padX={num(ig.get('x',0))} ({num(ig.get('width',0))}x"
                 f"{num(ig.get('height',0))}); text starts at x={num(tg.get('x',0))}.")
    return _spec(box.get("style"), geom, "official", note, "essentials.xml:On-Premise")


def x_persona(c: Ctx):
    e, cells = c.official.entry_cells("essentials.xml", "User and client")
    img = pick(cells, is_image)
    g = _cell_geom(img)
    note = ("essentials.xml 'User and client' is a composite (client device + user "
            f"avatar + labels, group {num(e['w'])}x{num(e['h'])}); persona captures the "
            "user-figure image cell. image→@{persona} placeholder (brand pack / icon atlas).")
    return _spec(img.get("style"),
                 {"w": num(g["width"]), "h": num(g["height"]),
                  "groupW": num(e["w"]), "groupH": num(e["h"])},
                 "official", note, "essentials.xml:User and client",
                 image_placeholder="persona")


def x_legend(c: Ctx):
    e, cells = c.official.entry_cells("essentials.xml", "Legend")
    box = pick(cells, lambda x: is_vertex_box(x) and "fillColor=#F5F6F7" in (x.get("style") or ""))
    g = _cell_geom(box)
    return _spec(box.get("style"), {"w": num(g["width"]), "h": num(g["height"])},
                 "official", "essentials.xml 'Legend' container panel (fill #F5F6F7, no border).",
                 "essentials.xml:Legend")


def x_pill_interface(c: Ctx):
    _e, cells = c.official.entry_cells("annotations_and_interfaces.xml", "Interface SAP")
    cell = pick(cells, is_vertex_box)
    g = _cell_geom(cell)
    return _spec(cell.get("style"), {"w": num(g["width"]), "h": num(g["height"])},
                 "official",
                 "annotations_and_interfaces.xml 'Interface SAP' pill (arcSize=50); "
                 "strokeColor #0070f3→#0070F2, fillColor default→#FFFFFF.",
                 "annotations_and_interfaces.xml:Interface SAP")


def x_step_circle(c: Ctx):
    _e, cells = c.official.entry_cells("numbers.xml", "default number")
    cell = pick(cells, lambda x: "ellipse" in (x.get("style") or ""))
    g = _cell_geom(cell)
    return _spec(cell.get("style"), {"w": num(g["width"]), "h": num(g["height"])},
                 "official",
                 "numbers.xml 'default number' ellipse (30x30); fillColor #5b738b→#5B738B, "
                 "fontColor default→#1D2D3E. gradientColor endpoint #223548 is the authentic "
                 "SAP number gradient (not in the flat Horizon set; not palette-guarded).",
                 "numbers.xml:default number")


def x_sap_btp_chip(c: Ctx):
    _e, cells = c.official.entry_cells("sap_brand_names.xml", "SAP BTP (Text Only)")
    cell = pick(cells, is_text)
    g = _cell_geom(cell)
    return _spec(cell.get("style"), {"w": num(g["width"]), "h": num(g["height"])},
                 "official",
                 "sap_brand_names.xml 'SAP BTP (Text Only)' text-only chip (no image); "
                 "fontColor default→#1D2D3E. Extra key owned by this contract per the manifest.",
                 "sap_brand_names.xml:SAP BTP (Text Only)")


def _service_image_cell(c: Ctx) -> tuple[ET.Element, dict]:
    e, cells = c.official.first_entry_cells(SERVICE_LIB)
    img = pick(cells, is_image)
    return img, {"w": num(e["w"]), "h": num(e["h"])}


def x_service_icon(c: Ctx):
    img, geom = _service_image_cell(c)
    return _spec(img.get("style"), geom, "official",
                 "First service icon cell of the official all-services (size M) library; "
                 "square 32x32. image→@{service}. Per-level render size (L1=48, L2=32) is "
                 "owned by _skeleton_layout, not this reference geometry.",
                 f"{Path(SERVICE_LIB).name}:service-icon", image_placeholder="service")


def x_badge_runtime(c: Ctx):
    img, geom = _service_image_cell(c)
    return _spec(img.get("style"), geom, "official",
                 "A CF-specific cell exists in the all-size-M library "
                 "('10017-sap-btp_cloud-foundry-runtime_sd' — entries are titled only by "
                 "tech-id, not friendly name); its style skeleton is byte-identical to the "
                 "generic service-icon skeleton, which we reuse with @{runtime}. The concrete "
                 "CF/Kyma logo is supplied at emit time (brand pack cf-badge).",
                 f"{Path(SERVICE_LIB).name}:runtime-badge", image_placeholder="runtime")


def x_edge_default(c: Ctx):
    _e, cells = c.official.entry_cells("connectors.xml", "direct one-directional")
    edge = pick(cells, lambda x: x.get("edge") == "1")
    return _spec(edge.get("style"), {"strokeWidth": 1.5}, "official",
                 "connectors.xml 'direct one-directional' (#475e75→#475E75 blockThin).",
                 "connectors.xml:direct one-directional", is_edge=True)


def x_edge_firewall(c: Ctx):
    _e, cells = c.official.entry_cells("connectors.xml", "default firewall")
    edge = pick(cells, lambda x: x.get("edge") == "1")
    return _spec(edge.get("style"), {"strokeWidth": 3}, "official",
                 "connectors.xml 'default firewall' (thick grey #475E75, strokeWidth=3).",
                 "connectors.xml:default firewall", is_edge=True)


def x_subaccount_frame(c: Ctx):
    corpus = c.need("ssam")
    lab, box = corpus.container_of(r"Subaccount: Extension", pick_="smallest", fill="#FFFFFF")
    return _spec(box.style, {"w": num(box.w), "h": num(box.h),
                             "padX": num(lab.x - box.x), "padTop": num(lab.y - box.y)},
                 "exemplar",
                 "SSAM tightest white rounded frame (blue border) enclosing the "
                 "'Subaccount: Extension …' label; padX/padTop measured from the label offset.",
                 f"SSAM:{box.id} (Subaccount: Extension)")


def x_governance_strip(c: Ctx):
    corpus = c.need("ssam")
    lab, box = corpus.container_of(r"Subaccount: Governance", pick_="largest", fill="#EBF8FF")
    return _spec(box.style, {"w": num(box.w), "h": num(box.h),
                             "padX": num(lab.x - box.x), "padTop": num(lab.y - box.y)},
                 "exemplar",
                 "SSAM outer BTP box (fill #EBF8FF) enclosing 'Subaccount: Governance'.",
                 f"SSAM:{box.id} (Subaccount: Governance)")


def x_product_box(c: Ctx):
    corpus = c.need("ssam")
    lab, box = corpus.container_of(r"SAP Build Process Automation", pick_="smallest")
    # title row = top offset of the inner white content panel inside the box
    inner = [x for x in corpus.cells
             if x is not box and x.w > 0 and x.x >= box.x - 2 and x.y >= box.y - 2
             and x.x + x.w <= box.x + box.w + 2 and x.y + x.h <= box.y + box.h + 2
             and color_eq(style_attr(x.style, "fillColor"), "#FFFFFF")]
    inner.sort(key=lambda x: x.y)
    geom = {"w": num(box.w), "h": num(box.h), "padX": num(lab.x - box.x)}
    note = ("SSAM container of 'SAP Build Process Automation'; fill #ECF8FF→#EBF8FF. "
            "padX = title icon+gap inset before the title text.")
    if inner:
        geom["titleRow"] = num(inner[0].y - box.y)
        note += f" titleRow = inner white content panel top offset ({geom['titleRow']})."
    return _spec(box.style, geom, "exemplar", note,
                 f"SSAM:{box.id} (SAP Build Process Automation)")


def x_capability_chip(c: Ctx):
    corpus = c.need("ssam")
    # Mapping: "child chip of that container, e.g. value 'Decision' (white rounded
    # chip w/ small icon)". In SSAM the SBPA product box renders ALL capabilities
    # inside one white rounded child panel (blue border) holding a grid of 32px
    # icons with labels ('Decision', …) — there is no per-capability rect. Select
    # that panel: the white rounded rect that encloses the 'Decision' label, and
    # derive the per-capability icon-grid geometry from its enclosed image cells.
    labs = corpus.labels(r"^Decision$")
    if not labs:
        raise ContractError("SSAM: no 'Decision' capability label found")
    lab = labs[0]
    cands = [r for r in corpus.rects_containing(lab.cx, lab.cy, exclude=lab)
             if "rounded=1" in r.style
             and color_eq(style_attr(r.style, "fillColor"), "#FFFFFF")
             and color_eq(style_attr(r.style, "strokeColor"), "#0070F2")]
    if not cands:
        raise ContractError("SSAM: no white rounded chip encloses the 'Decision' label")
    chip = cands[0]  # tightest white rounded enclosure
    icons = sorted((k for k in corpus.cells
                    if is_image(k.elem) and 24 <= k.w <= 40
                    and chip.x <= k.cx <= chip.x + chip.w
                    and chip.y <= k.cy <= chip.y + chip.h),
                   key=lambda k: (k.y, k.x))
    geom = {"w": num(chip.w), "h": num(chip.h)}
    note = ("SSAM white rounded capability chip: the SBPA product box's child panel "
            "enclosing the 'Decision' label. SSAM draws capabilities as ONE white "
            "chip with an icon grid (no per-capability rects).")
    if icons:
        # Hand-drawn icons wobble by a pixel or two: cluster rows/columns with an
        # 8px tolerance, then take the top row's leftmost icon as the reference.
        def cluster(vals: list[float], tol: float = 8.0) -> list[float]:
            out: list[float] = []
            for v in sorted(vals):
                if not out or v - out[-1] > tol:
                    out.append(v)
            return out

        top_y = min(k.y for k in icons)
        first = min((k for k in icons if k.y - top_y <= 8), key=lambda k: k.x)
        geom.update({"iconW": num(first.w), "iconH": num(first.h),
                     "padX": num(first.x - chip.x), "padTop": num(first.y - chip.y),
                     "labelDy": num(lab.y - first.y)})
        cols = cluster([k.x for k in icons])
        rows = cluster([k.y for k in icons])
        if len(cols) > 1:
            geom["gapX"] = num(cols[1] - cols[0])
        if len(rows) > 1:
            geom["gapY"] = num(rows[1] - rows[0])
        note += (" Icon grid measured from enclosed image cells (rows/cols clustered "
                 "at 8px tolerance): padX/padTop = top-left icon offset, gapX/gapY = "
                 "column/row pitch, labelDy = label top relative to its icon.")
        col_pitches = [num(b - a) for a, b in zip(cols, cols[1:])]
        if len(set(col_pitches)) > 1:
            note += (f" Exemplar icon columns are uneven (pitches "
                     f"{', '.join(str(p) for p in col_pitches)}); gapX records the "
                     "first (canonical) pitch.")
    return _spec(chip.style, geom, "exemplar", note,
                 f"SSAM:{chip.id} (capability panel w/ 'Decision')")


def x_tier_box_sap(c: Ctx):
    corpus = c.need("ssam")
    _lab, box = corpus.container_of(r"Public Cloud", pick_="smallest", fill="#FFFFFF")
    return _spec(box.style, {"w": num(box.w), "h": num(box.h)}, "exemplar",
                 "SSAM tier box of 'Public Cloud' (SAP blue border, white fill).",
                 f"SSAM:{box.id} (Public Cloud)")


def x_tier_box_nonsap(c: Ctx):
    corpus = c.need("ssam")
    _lab, box = corpus.container_of(r"Any-Premise", pick_="smallest")
    # SSAM draws Any-Premise with a blue border like the SAP tiers; recolor its
    # stroke to SAP's official non-SAP grey (#475E75, per essentials '3rd party
    # layer') so the non-SAP tier is visually distinct from tier-box-sap.
    recolored = re.sub(r"strokeColor=[^;]+", "strokeColor=#475E75", box.style)
    return _spec(recolored, {"w": num(box.w), "h": num(box.h)}, "exemplar",
                 "SSAM box of 'Any-Premise'. The exemplar drew a blue (#0070F2) border; "
                 "recolored to the official SAP non-SAP grey #475E75 (essentials '3rd party "
                 "layer') so non-SAP tiers read distinctly from SAP tiers. Fill kept #FFFFFF.",
                 f"SSAM:{box.id} (Any-Premise, stroke recolored to non-SAP grey)")


def x_chip(c: Ctx):
    corpus = c.need("snam")
    cand = [x for x in corpus.by_value(r"^PAS$")
            if "rounded=1" in x.style and 0 < x.w <= 200]
    if not cand:
        raise ContractError("SNAM: no 'PAS' client chip found")
    chip = cand[0]
    return _spec(chip.style, {"w": num(chip.w), "h": num(chip.h)}, "exemplar",
                 "SNAM client chip 'PAS' (small white chip, SAP blue border).",
                 f"SNAM:{chip.id} (PAS)")


def x_db(c: Ctx):
    corpus = c.need("snam")
    # SNAM has no cylinder/datastore shape — databases are rendered as blue label
    # chips (e.g. 'SAP HANA DB'). The mapping sanctions a canonical drawio cylinder
    # fallback with Horizon colors; adopt SNAM's blue/white DB palette.
    ref = next((x for x in corpus.by_value(r"HANA DB|Oracle DB") if "rounded=1" in x.style), None)
    frm = f"SNAM (fallback cylinder; DBs are chips, e.g. {ref.id!r} '{ref.vclean}')" if ref else \
          "SNAM (fallback cylinder; no cylinder cell in corpus)"
    style = ("shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;"
             "size=15;strokeColor=#0070F2;fillColor=#FFFFFF;strokeWidth=1.5;")
    return _spec(style, {"w": 60, "h": 80}, "exemplar",
                 "Canonical draw.io cylinder fallback (sanctioned by the mapping): SNAM has no "
                 "cylinder/datastore shape, it renders databases as blue label chips. Colors "
                 "adopt SNAM's DB palette (SAP blue border, white fill); geometry is a standard "
                 "datastore default.", frm)


def x_badge_hyperscaler(c: Ctx):
    corpus = c.need("brandart")
    azure = [x for x in corpus.cells if x.image_val and "Microsoft_Azure" in x.image_val]
    aws = [x for x in corpus.cells if x.image_val and "Amazon_Web_Services" in x.image_val]
    chosen = (azure or aws)
    if not chosen:
        raise ContractError("BRANDART: no AWS/Azure hyperscaler logo image found")
    badge = chosen[0]
    alt = "AWS logo also present (55x37)" if azure and aws else ""
    return _spec(badge.style, {"w": num(badge.w), "h": num(badge.h)}, "exemplar",
                 "BRANDART hyperscaler logo badge (Azure preferred). SSAM embeds hyperscaler "
                 "logos as unlabeled base64 images (not selectable); Brandart carries an "
                 f"identifiable logo image. image→@{{hyperscaler}}. {alt}".strip(),
                 f"BRANDART:{badge.id} (hyperscaler logo)", image_placeholder="hyperscaler")


def x_watermark(c: Ctx):
    corpus = c.need("brandart")
    cands = []
    for x in corpus.cells:
        if not is_image(x.elem):
            continue
        m = re.search(r"opacity=(\d+)", x.style)
        if m and int(m.group(1)) < 100:
            cands.append((x.area, int(m.group(1)), x))
    if not cands:
        raise ContractError("BRANDART: no semi-transparent watermark image found")
    cands.sort(key=lambda t: -t[0])
    _area, op, wm = cands[0]
    return _spec(wm.style, {"w": num(wm.w), "h": num(wm.h), "opacity": op}, "exemplar",
                 f"BRANDART Lutech watermark: largest semi-transparent (opacity={op}) image cell. "
                 "Opacity preserved; image→@{watermark} (brand pack, local-only).",
                 f"BRANDART:{wm.id} (Lutech watermark)", image_placeholder="watermark")


def x_custom_app_box(c: Ctx):
    corpus = c.need("brandart")
    lab, box = corpus.container_of(r"Procurement Application", pick_="smallest")
    return _spec(box.style, {"w": num(box.w), "h": num(box.h), "padX": num(lab.x - box.x)},
                 "exemplar",
                 "BRANDART container of 'Procurement Application' (custom app card); "
                 "fill #ECF8FF→#EBF8FF, SAP blue border.",
                 f"BRANDART:{box.id} (Procurement Application)")


def x_pill_protocol(c: Ctx):
    corpus = c.need("ssam")
    cand = [x for x in corpus.by_value(r"SAML2?/OIDC|SAML2|OIDC")
            if "rounded=1" in x.style and "arcSize=50" in x.style
            and color_eq(style_attr(x.style, "strokeColor"), "#188918")]
    if not cand:
        raise ContractError("SSAM: no green SAML2/OIDC protocol pill found")
    pill = min(cand, key=lambda x: (x.area, x.id))
    return _spec(pill.style, {"w": num(pill.w), "h": num(pill.h)}, "exemplar",
                 f"SSAM green protocol pill '{pill.vclean}' (arcSize=50); fillColor "
                 "#f5fae5→#F5FAE5, fontColor #266f3a→#188918. Cross-checked vs official "
                 "'Authenticate' pill (annotations_and_interfaces.xml): same arcSize-50 "
                 "green pill pattern.",
                 f"SSAM:{pill.id} ({pill.vclean})")


def _network_label(c: Ctx) -> Cell:
    labels = c.gold.labels(r"^NETWORK$")
    if not labels:
        raise ContractError("GOLD: no NETWORK label in SAP_Task_Center_L1")
    return labels[0]


def x_network_separator(c: Ctx):
    label = _network_label(c)
    line = c.gold.edge_by_stroke("#5B738B", extra="jumpStyle=gap")
    _dx, dy = edge_span(line)
    return _spec(line.style,
                 {"h": num(dy), "strokeWidth": 3},
                 "official",
                 "SAP_Task_Center_L1 NETWORK zone separator: the vertical grey bar "
                 "(#5B738B, strokeWidth=3, jumpStyle=gap). Its caption is the separate "
                 "molecule 'network-separator-label'. Line height (h) is the exemplar "
                 "span; layout drives the real length.",
                 f"GOLD:{line.id} NETWORK bar (+label {label.id})", is_edge=True)


def x_network_separator_label(c: Ctx):
    label = _network_label(c)
    return _spec(label.style, {"w": num(label.w), "h": num(label.h)},
                 "official",
                 "SAP_Task_Center_L1 NETWORK zone caption: the text cell placed beside the "
                 "network-separator bar (see that molecule for the line style).",
                 f"GOLD:{label.id} NETWORK label")


def x_edge_identity(c: Ctx):
    edge = c.need("ssam").edge_by_stroke("#188918")
    return _spec(edge.style, {"strokeWidth": 1.5}, "exemplar",
                 "SSAM identity edge (green #188918); routing anchors + fontColor default→#1D2D3E "
                 "normalized. Semantic family: identity/authentication flows.",
                 f"SSAM:{edge.id} (identity)", is_edge=True)


def x_edge_provisioning(c: Ctx):
    edge = c.need("ssam").edge_by_stroke("#470BED")
    return _spec(edge.style, {"strokeWidth": 1.5}, "exemplar",
                 "SSAM provisioning edge (purple #470BED, SCIM). Semantic family: user/identity "
                 "provisioning flows.",
                 f"SSAM:{edge.id} (provisioning/SCIM)", is_edge=True)


def x_edge_master_data(c: Ctx):
    corpus = c.need("ssam")
    try:
        edge = corpus.edge_by_stroke("#CC00DC")
    except ContractError:
        edge = corpus.edge_by_stroke("#CB00DC")  # snapped to #CC00DC on normalize
    return _spec(edge.style, {"strokeWidth": 1.5}, "exemplar",
                 "SSAM master-data edge (magenta). Some SSAM cells use #CB00DC; normalized to the "
                 "Horizon #CC00DC. Semantic family: master-data replication flows.",
                 f"SSAM:{edge.id} (master-data)", is_edge=True)


def x_edge_transport(c: Ctx):
    edge = c.need("ssam").edge_by_stroke("#475E75", dashed=True)
    return _spec(edge.style, {"strokeWidth": 1.5}, "exemplar",
                 "SSAM dashed transport edge (grey #475E75, dashed=1). Semantic family: "
                 "transport / change-movement flows.",
                 f"SSAM:{edge.id} (transport, dashed)", is_edge=True)


EXTRACTORS = [
    ("title-block", x_title_block),
    ("btp-area", x_btp_area),
    ("subaccount-frame", x_subaccount_frame),
    ("governance-strip", x_governance_strip),
    ("product-box", x_product_box),
    ("capability-chip", x_capability_chip),
    ("custom-app-box", x_custom_app_box),
    ("tier-box-sap", x_tier_box_sap),
    ("tier-box-nonsap", x_tier_box_nonsap),
    ("backend-box", x_backend_box),
    ("persona", x_persona),
    ("service-icon", x_service_icon),
    ("chip", x_chip),
    ("db", x_db),
    ("legend", x_legend),
    ("network-separator", x_network_separator),
    ("network-separator-label", x_network_separator_label),
    ("badge-hyperscaler", x_badge_hyperscaler),
    ("badge-runtime", x_badge_runtime),
    ("watermark", x_watermark),
    ("pill-protocol", x_pill_protocol),
    ("pill-interface", x_pill_interface),
    ("step-circle", x_step_circle),
    ("sap-btp-chip", x_sap_btp_chip),
    ("edge-default", x_edge_default),
    ("edge-identity", x_edge_identity),
    ("edge-provisioning", x_edge_provisioning),
    ("edge-master-data", x_edge_master_data),
    ("edge-transport", x_edge_transport),
    ("edge-firewall", x_edge_firewall),
]


# ── assembly / validation ─────────────────────────────────────────────────
def _finalize(key: str, spec: dict) -> dict:
    style = normalize_style(
        spec["raw"], key,
        image_placeholder=spec.get("image_placeholder"),
        is_edge=spec.get("is_edge", False),
    )
    return {"style": style, "geometry": spec["geometry"], "source": spec["source"],
            "from": spec["from"], "notes": spec["notes"]}


def build(ctx: Ctx, date: str, corpus_files: list[str]) -> dict:
    molecules: dict[str, dict] = {}
    errors: list[str] = []
    for key, fn in EXTRACTORS:
        try:
            molecules[key] = _finalize(key, fn(ctx))
        except ContractError as exc:
            errors.append(f"  {key}: {exc}")
    if errors:
        raise ContractError(
            "contract extraction FAILED for %d molecule(s):\n%s"
            % (len(errors), "\n".join(errors))
        )
    # Machine-readable placeholder vocabulary: every @{…} key used in styles.
    placeholders = sorted({
        m.group(1)
        for mol in molecules.values()
        for m in re.finditer(r"@\{([\w-]+)\}", mol["style"])
    })
    return {
        "meta": {
            "generatedAt": date,
            "scriptVersion": SCRIPT_VERSION,
            "corpus": sorted(corpus_files),
            "palette": sorted(HORIZON),
            "placeholders": placeholders,
            "notes": "Colors normalized to SAP Horizon; image payloads are @{…} placeholders "
                     "resolved from the brand pack at emit time (no base64 in this file).",
        },
        "molecules": molecules,
    }


def validate(contract: dict, schema_path: Path) -> None:
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    try:
        import jsonschema  # type: ignore
    except ImportError:
        jsonschema = None
    if jsonschema is not None:
        try:
            jsonschema.validate(contract, schema)
        except jsonschema.ValidationError as exc:
            # Surface schema violations through the normal failure path (main()
            # catches ContractError) instead of a raw traceback.
            raise ContractError(f"schema: {exc.message}") from exc
        return
    # Pure-python fallback (no dependency): check the load-bearing constraints.
    if set(contract) < {"meta", "molecules"}:
        raise ContractError("schema: top-level must have meta + molecules")
    for k in schema["properties"]["meta"]["required"]:
        if k not in contract["meta"]:
            raise ContractError(f"schema: meta.{k} missing")
    required = set(schema["properties"]["molecules"]["required"])
    mols = contract["molecules"]
    missing = required - set(mols)
    if missing:
        raise ContractError(f"schema: molecules missing {sorted(missing)}")
    for name, m in mols.items():
        for field in ("style", "geometry", "source", "notes"):
            if field not in m:
                raise ContractError(f"schema: {name} missing {field}")
        if not isinstance(m["style"], str):
            raise ContractError(f"schema: {name}.style not a string")
        if m["source"] not in ("official", "exemplar"):
            raise ContractError(f"schema: {name}.source invalid ({m['source']})")
        if not isinstance(m["geometry"], dict) or not all(
            isinstance(v, (int, float)) for v in m["geometry"].values()
        ):
            raise ContractError(f"schema: {name}.geometry must be an object of numbers")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Extract the molecule style contract.")
    ap.add_argument("--official-repo", type=Path, required=True,
                    help="Checkout of SAP/btp-solution-diagrams.")
    ap.add_argument("--exemplar", type=Path, action="append", default=[],
                    help="Lutech exemplar .drawio (repeatable): SSAM / Brandart / SNAM.")
    ap.add_argument("--date", required=True,
                    help="Generation date (YYYY-MM-DD) — reproducible, not datetime.now.")
    ap.add_argument("--out", type=Path, default=Path("assets/style-contract.json"))
    ap.add_argument("--schema", type=Path, default=Path("assets/style-contract.schema.json"))
    args = ap.parse_args(argv)

    official = Official(args.official_repo)
    gold_path = args.official_repo / EXAMPLES_SUBPATH / GOLD_FILE
    if not gold_path.exists():
        print(f"ERROR: gold example not found: {gold_path}", file=sys.stderr)
        return 2
    gold = Corpus(gold_path, "gold")

    ex: dict[str, Corpus] = {}
    corpus_files = ["essentials.xml", "area_shapes.xml", "annotations_and_interfaces.xml",
                    "connectors.xml", "numbers.xml", "sap_brand_names.xml", "text_elements.xml",
                    Path(SERVICE_LIB).name, GOLD_FILE]
    for p in args.exemplar:
        n = p.name.lower()
        role = "ssam" if "ssam" in n else "brandart" if "brandart" in n else "snam" if "snam" in n else None
        if role is None:
            print(f"WARNING: could not classify exemplar {p.name} (expected SSAM/brandart/SNAM)",
                  file=sys.stderr)
            continue
        if not p.exists():
            print(f"ERROR: exemplar not found (needs hydration?): {p}", file=sys.stderr)
            return 2
        ex[role] = Corpus(p, role)
        corpus_files.append(p.name)

    ctx = Ctx(official, ex, gold)
    try:
        contract = build(ctx, args.date, corpus_files)
        validate(contract, args.schema)
    except ContractError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(contract, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"OK: wrote {len(contract['molecules'])} molecules -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
