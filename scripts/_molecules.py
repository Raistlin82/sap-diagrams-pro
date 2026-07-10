#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""_molecules.py — style-contract-driven molecule emission for sap-diagrams-pro.

Every visual "molecule" (product box, subaccount frame, cloud-tier box, custom
app card, capability chip, protocol pill, step circle, image badge, …) is
assembled here from the *style contract* (``assets/style-contract.json``) — this
module contains **NO style literals of its own** (the
``test_no_style_literals_in_engine_sources`` guard greps this file for hardcoded
styles). Base molecule styles come verbatim from the contract; this module only
picks the right contract entry and computes geometry. Protocol-pill semantic
recolouring may also consult ``assets/canonical-pills.json`` so guideline-level
Trust/AuthN/AuthZ intent can override ambiguous harvested examples.

Public API (each vertex molecule returns a list of cell dicts, or a single dict,
in PARENT-RELATIVE coordinates — the caller in ``generate-drawio.py`` offsets the
anchor by the group/node position the layout engine computed and serialises the
dicts to ``mxCell`` XML)::

    load_contract() -> dict
    load_brand_packs() -> dict
    product_box(node, contract, icon_resolver) -> list[dict]
    db_cell(node, contract) -> dict
    chip_cell(node, contract) -> dict
    custom_app_box(group, contract) -> list[dict]
    subaccount_frame(group, contract) -> list[dict]
    governance_strip(group, contract) -> list[dict]
    tier_box(group, contract) -> list[dict]
    persona(node, contract, icon_resolver) -> list[dict]
    pill(edge, contract) -> dict
    step_circle(node, contract) -> dict
    network_separator(x, y0, y1, contract) -> list[dict]
    branding_block(metadata, contract, brand_packs, icon_resolver, warnings) -> list[dict]
    badge(kind, name, contract, brand_packs, icon_resolver, warnings) -> dict

Cell dict schema: ``{id, value, style, x, y, w, h, parent, ...}``. ``parent`` is
``None`` for the molecule's *anchor* (cells[0]) — the caller supplies its real
parent + offset — or a local id from the same list for a true child (whose
coords are relative to that child's parent, so the caller does NOT offset it).

Placeholder resolution (spec Layer-3): contract styles carry ``image=@{key}``
placeholders. ``resolve_style_placeholders`` swaps ``@{key}``/``@key`` for a real
dataUri drawn from the brand packs (``assets/brand-pack[.local]/index.json``) or,
for service icons, an ``icon_resolver`` callable. When an asset is ABSENT the
cell degrades to a neutral **text-badge fallback** (a bordered chip whose value
is the human-readable name, e.g. "AWS") and a preflight WARNING is recorded —
never a hard failure (CI / Claude Desktop ship without the ``.local`` pack).
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Callable

ASSETS = Path(__file__).resolve().parent.parent / "assets"

# ─────────────────────────────────────────────────────────────────────────────
# Loading
# ─────────────────────────────────────────────────────────────────────────────
_CONTRACT_CACHE: dict | None = None
_CANONICAL_PILLS_CACHE: dict | None = None


def load_contract() -> dict:
    """Load (and memoise) the style contract."""
    global _CONTRACT_CACHE
    if _CONTRACT_CACHE is None:
        _CONTRACT_CACHE = json.loads(
            (ASSETS / "style-contract.json").read_text(encoding="utf-8")
        )
    return _CONTRACT_CACHE


def _load_canonical_pills() -> dict:
    """Load the harvested SAP pill catalog, if present.

    The catalog is evidence from examples, not the source of semantic truth:
    semantic overrides below may intentionally supersede an entry when the
    guideline has a clearer meaning for the current edge context.
    """
    global _CANONICAL_PILLS_CACHE
    if _CANONICAL_PILLS_CACHE is None:
        try:
            data = json.loads(
                (ASSETS / "canonical-pills.json").read_text(encoding="utf-8")
            )
            _CANONICAL_PILLS_CACHE = data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            _CANONICAL_PILLS_CACHE = {}
    return _CANONICAL_PILLS_CACHE


def load_brand_packs() -> dict:
    """Merge the public + local brand-pack indexes into one ``key -> entry`` map.

    The public pack (committed) is loaded first; the ``.local`` pack (gitignored,
    often absent in CI / on Desktop) is layered on top so a private high-fidelity
    asset can override a public placeholder. The (also gitignored, also often
    absent) ``brand-pack.local/capability-icons.json`` — Gabriele's harvested
    ``cap-<slug>`` capability-chip icons — is merged in last. Missing / malformed
    files are ignored — resolution then simply falls back to the text-badge /
    text-chip path, no error.
    """
    packs: dict[str, Any] = {}
    for rel in (
        "brand-pack/index.json",
        "brand-pack.local/index.json",
        "brand-pack.local/capability-icons.json",
    ):
        p = ASSETS / rel
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                packs.update(data)
        except (json.JSONDecodeError, OSError):
            continue
    return packs


# ─────────────────────────────────────────────────────────────────────────────
# Contract accessors (keep every style string sourced from the contract)
# ─────────────────────────────────────────────────────────────────────────────
def _mol(contract: dict, name: str) -> dict:
    try:
        return contract["molecules"][name]
    except KeyError as exc:
        raise KeyError(f"molecule {name!r} missing from style-contract.json") from exc


def _style(contract: dict, name: str) -> str:
    return _mol(contract, name)["style"]


def _geo(contract: dict, name: str) -> dict:
    return _mol(contract, name).get("geometry", {})


def _f(geo: dict, key: str, default: float) -> float:
    v = geo.get(key, default)
    return float(v)


# ─────────────────────────────────────────────────────────────────────────────
# Human-readable names + brand-asset key resolution
# ─────────────────────────────────────────────────────────────────────────────
_DISPLAY: dict[str, str] = {
    "aws": "AWS",
    "azure": "Azure",
    "gcp": "GCP",
    "google": "Google Cloud",
    "alibaba": "Alibaba Cloud",
    "ibm": "IBM Cloud",
    "cloud-foundry": "Cloud Foundry",
    "cloudfoundry": "Cloud Foundry",
    "kyma": "Kyma",
    "abap": "ABAP",
    "neo": "Neo",
    "rise": "RISE with SAP",
    "acme": "ACME",
    "lutech": "Lutech",
    "sap": "SAP",
    "sap-btp-chip": "SAP BTP",
    "sap-logo-chip": "SAP",
}


def display_name(key: str) -> str:
    """Map a brand-asset key (``aws``, ``cloud-foundry``, ``azure-badge``) to a
    human-readable label for the text-badge fallback (``AWS`` / ``Cloud
    Foundry`` / ``Azure``)."""
    if not key:
        return ""
    if key in _DISPLAY:
        return _DISPLAY[key]
    base = re.sub(r"-(badge|logo|chip)$", "", key)
    if base in _DISPLAY:
        return _DISPLAY[base]
    return base.replace("-", " ").replace("_", " ").title()


# A few brand keys don't follow the ``<name>-badge`` convention.
_KEY_ALIASES: dict[str, list[str]] = {
    "sap-btp-chip": ["sap-logo-chip"],
    "cloud-foundry": ["cf-badge"],
    "cloudfoundry": ["cf-badge"],
}


def _key_candidates(key: str) -> list[str]:
    """Candidate brand-pack keys to try for a logical asset name, most-specific
    first: the exact key, ``<key>-badge``, ``<key>-logo``, then any aliases."""
    cands = [key, f"{key}-badge", f"{key}-logo"]
    for alias in _KEY_ALIASES.get(key, []):
        if alias not in cands:
            cands.append(alias)
    # de-dup, preserve order
    seen: set[str] = set()
    out: list[str] = []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _resolve_asset(
    key: str,
    brand_packs: dict | None,
    icon_resolver: Callable[[str], str | None] | None = None,
    brand_only: bool = False,
) -> str | None:
    """Resolve a logical asset name to a draw.io dataUri, or ``None`` if absent.

    Order (spec Layer-3): brand packs (public then local, already merged) →
    the icon resolver (for ``@{service}`` glyphs, reusing the emitter's own
    ShapeIndex path). dataUris are used verbatim (comma-form, no ``;base64``)
    so the downstream sha1 / atlas lookup stays stable.

    FIX-4: ``brand_only`` skips the icon-resolver leg. Hyperscaler / runtime
    badges are BRAND assets — an unrelated fuzzy glyph match (e.g. the icon
    resolver matching ``cloud-foundry`` to some small SAP icon) must NOT stand
    in for a missing brand-pack asset; without the real asset the badge has to
    degrade to the deterministic text-chip fallback (+ WARNING), exactly like
    ``aws`` / ``azure`` already do (they simply have no fuzzy match)."""
    packs = brand_packs or {}
    for cand in _key_candidates(key):
        entry = packs.get(cand)
        if isinstance(entry, dict) and entry.get("dataUri"):
            return entry["dataUri"]
    if icon_resolver is not None and not brand_only:
        uri = icon_resolver(key)
        if uri:
            return uri
    return None


_PLACEHOLDER_RE = re.compile(r"image=@\{?([\w.\-]+)\}?")


def resolve_style_placeholders(
    style: str,
    brand_packs: dict | None,
    icon_resolver: Callable[[str], str | None] | None = None,
    brand_only: bool = False,
) -> tuple[str, list[str]]:
    """Replace ``image=@{key}`` / ``image=@key`` tokens with resolved dataUris.

    Returns ``(new_style, unresolved_keys)``. Idempotent: an already-resolved
    ``image=data:…`` has no ``@`` and is left untouched. When ``unresolved_keys``
    is non-empty the caller applies the text-badge fallback. ``brand_only``
    (FIX-4) forbids the fuzzy icon-resolver leg for brand-asset badges.
    """
    unresolved: list[str] = []

    def repl(m: re.Match) -> str:
        key = m.group(1)
        uri = _resolve_asset(key, brand_packs, icon_resolver, brand_only)
        if uri is None:
            unresolved.append(key)
            return m.group(0)
        return f"image={uri}"

    return _PLACEHOLDER_RE.sub(repl, style), unresolved


def _fallback_chip_style(contract: dict) -> str:
    """Neutral bordered chip used for the text-badge fallback."""
    return _style(contract, "chip")


def resolve_cell(
    cell: dict,
    brand_packs: dict | None,
    contract: dict,
    icon_resolver: Callable[[str], str | None] | None = None,
    warnings: list[str] | None = None,
) -> dict:
    """Return a copy of ``cell`` with any image placeholders resolved.

    Behaviour on an *unresolved* placeholder depends on ``cell['placeholder_mode']``:
      * ``"strip"`` — drop the ``image=@…`` token, keep the cell's own style +
        value (used by the SAP BTP text chip, whose fallback *is* its text form).
      * anything else (``"badge"`` / default) — swap to the neutral text-badge:
        a bordered chip whose value is ``fallback_name`` (or the humanised key).
    """
    out = dict(cell)
    style = cell.get("style", "")
    if "@" not in style:
        return out
    resolved, unresolved = resolve_style_placeholders(
        style, brand_packs, icon_resolver, brand_only=bool(cell.get("brand_only"))
    )
    if not unresolved:
        out["style"] = resolved
        return out
    if cell.get("placeholder_mode") == "strip":
        out["style"] = re.sub(r"image=@\{?[\w.\-]+\}?;?", "", resolved)
        return out
    key = unresolved[0]
    if warnings is not None:
        warnings.append(
            f"brand asset {key!r} not found (no .local pack?); "
            f"using text-badge fallback {display_name(key)!r}"
        )
    out["style"] = _fallback_chip_style(contract)
    out["value"] = cell.get("fallback_name") or display_name(key)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Badge slots (image badge with text-chip fallback)
# ─────────────────────────────────────────────────────────────────────────────
_BADGE_MOLECULE = {
    "hyperscaler": "badge-hyperscaler",
    "runtime": "badge-runtime",
    "watermark": "watermark",
    "persona": "persona",
    "service": "service-icon",
}


def _badge_slot_size(kind: str, contract: dict) -> tuple[float, float]:
    """(w, h) of a single badge slot of ``kind`` — the ONE source of truth for
    badge geometry, shared by ``_badge_slot`` (what gets drawn) and
    ``_badge_row_size`` (what a frame reserves), so the two can never drift.

    Runtime badges render as the ``chip`` text chip (see ``_badge_slot``), NOT
    the 32px ``badge-runtime`` image slot, so their reserved width must be the
    chip's — otherwise a badge-driven frame (e.g. a min-width cloud tier) reserves
    too little and the chip overflows the frame border."""
    if kind == "runtime":
        cg = _geo(contract, "chip")
        return _f(cg, "w", 130.0), _f(cg, "h", 28.18)
    g = _geo(contract, _BADGE_MOLECULE.get(kind, "badge-hyperscaler"))
    return _f(g, "w", 82.5), _f(g, "h", 55.0)


def _badge_slot(kind: str, name: str, contract: dict) -> dict:
    """Build an UNRESOLVED image-badge cell for ``name`` using the ``kind``
    contract style, with its category placeholder rewritten to ``@{name}`` and a
    ``fallback_name`` for the text-badge path. Resolution is the caller's job
    (``resolve_cell`` / ``badge``)."""
    # Runtime badges (Cloud Foundry, Kyma) are wide wordmark logos that squish
    # into an illegible blob at the 32px runtime-badge size. Render them as a
    # neutral text chip (SAP-blue bordered pill with the friendly name) instead —
    # the deliberate "chip testuale" form. No image resolution: deterministic and
    # readable everywhere (zone title bands + custom-app runtime row).
    if kind == "runtime":
        bw, bh = _badge_slot_size(kind, contract)
        return {
            "id": f"badge-{kind}-{name}",
            "value": display_name(name),
            "style": _fallback_chip_style(contract),
            "x": 0.0,
            "y": 0.0,
            "w": bw,
            "h": bh,
            "parent": None,
            "connectable": False,
            "placeholder_mode": "strip",
            "fallback_name": display_name(name),
        }
    molname = _BADGE_MOLECULE.get(kind, "badge-hyperscaler")
    base = _style(contract, molname)
    style = re.sub(r"image=@\{[^}]*\}", f"image=@{{{name}}}", base)
    g = _geo(contract, molname)
    return {
        "id": f"badge-{kind}-{name}",
        "value": "",
        "style": style,
        "x": 0.0,
        "y": 0.0,
        "w": _f(g, "w", 82.5),
        "h": _f(g, "h", 55.0),
        "parent": None,
        "connectable": False,
        "placeholder_mode": "badge",
        "fallback_name": display_name(name),
        # FIX-4: hyperscaler/runtime badges are brand assets — resolve them from
        # brand packs only, never a fuzzy icon-glyph match, so a missing asset
        # degrades to the text-chip fallback (+ WARNING) deterministically.
        "brand_only": kind in ("hyperscaler", "runtime"),
    }


def badge(
    kind: str,
    name: str,
    contract: dict,
    brand_packs: dict,
    icon_resolver: Callable[[str], str | None] | None = None,
    warnings: list[str] | None = None,
) -> dict:
    """Resolved image badge for ``name`` (kind ``hyperscaler`` | ``runtime`` |
    ``watermark`` | …). Returns the image cell when the asset resolves, else the
    neutral text-badge fallback (a bordered chip whose value is the human name,
    e.g. ``badge("hyperscaler","aws",…)`` with an empty pack → value ``"AWS"``).

    ``icon_resolver``/``warnings`` are threaded straight through to
    ``resolve_cell`` so every badge — including the customer-branding assets
    emitted via ``branding_block`` — gets the SAME shape-index resolution leg
    and the SAME de-duplicated preflight WARNING on an unresolved asset as the
    group-badge path (``_place_molecule`` in generate-drawio.py). Both default
    to ``None`` so existing 4-positional-arg callers are unaffected."""
    return resolve_cell(
        _badge_slot(kind, name, contract), brand_packs, contract, icon_resolver, warnings
    )


def _append_badge_slots(
    cells: list[dict],
    group: Any,
    contract: dict,
    parent: str,
    x0: float,
    y0: float,
    gap: float = 8.0,
) -> None:
    """Append hyperscaler + runtime badge slots (from ``group.badges``) in a row,
    parented to ``parent``. Slots stay UNRESOLVED; the emitter resolves them
    (it owns the brand packs)."""
    badges = getattr(group, "badges", None) or {}
    x = x0
    for kind, coll in (("hyperscaler", "hyperscalers"), ("runtime", "runtimes")):
        for name in (badges.get(coll) or []):
            slot = _badge_slot(kind, str(name), contract)  # id already set by _badge_slot
            slot["x"] = x
            slot["y"] = y0
            slot["parent"] = parent
            cells.append(slot)
            x += slot["w"] + gap


# ─────────────────────────────────────────────────────────────────────────────
# Node molecules
# ─────────────────────────────────────────────────────────────────────────────
def _capability_grid_geometry(contract: dict) -> tuple[float, float, float]:
    """Per-chip ``(w, h, gap)`` for the capability-chip grid inside a product
    box — derived from the contract's ``capability-chip`` geometry (review
    fix: this used to be the bare module literal ``132.0, 56.0, 12.0``,
    disconnected from the contract and disagreeing with it).

    The contract's ``capability-chip`` entry (``build-style-contract.py::
    x_capability_chip``) is measured from a SINGLE SSAM exemplar panel that
    wraps an ENTIRE capability icon-grid for one product — ``w``/``h`` are
    the whole panel's size, ``gapX``/``gapY`` the icon-to-icon pitch,
    ``iconW``/``iconH`` the measured icon footprint, ``padX``/``padTop`` the
    inset of the first icon from the panel edge. The SSAM exemplar draws ALL
    of a product's capabilities inside that ONE bordered white panel — it has
    no per-capability rect — whereas this engine renders one bordered
    ``capability-chip``-styled cell PER capability (see ``product_box``
    below). The panel geometry therefore can't be used verbatim as a
    per-chip size; we derive one instead, from the SAME numbers:

      * cell size — the panel's content box (panel size minus the
        padX/padTop inset on both axes) divided across the reference grid
        shape actually measured in that exemplar (2 columns x 2 rows — the
        SBPA exemplar panel holds 4 capabilities);
      * gap — the icon pitch minus the icon footprint (``gapX - iconW`` /
        ``gapY - iconH``), i.e. the exemplar's own breathing room between
        grid items, taking the smaller of the two axes.

    Every input number is sourced from the contract via ``_geo()``; only the
    divide-by-reference-grid step is this module's own (documented)
    derivation, per the "geometry from the contract" principle in the module
    docstring above (no bare, contract-disconnected literals)."""
    g = _geo(contract, "capability-chip")
    panel_w = _f(g, "w", 315.0)
    panel_h = _f(g, "h", 135.0)
    pad_x = _f(g, "padX", 42.5)
    pad_top = _f(g, "padTop", 18.2)
    gap_x = _f(g, "gapX", 87.36)
    gap_y = _f(g, "gapY", 53.28)
    icon_w = _f(g, "iconW", 32.0)
    icon_h = _f(g, "iconH", 32.0)
    cols_ref, rows_ref = 2.0, 2.0  # SSAM SBPA exemplar panel: 4 caps, 2x2 grid
    cell_w = max(icon_w, (panel_w - 2 * pad_x) / cols_ref)
    cell_h = max(icon_h, (panel_h - 2 * pad_top) / rows_ref)
    gap = max(4.0, min(gap_x - icon_w, gap_y - icon_h))
    return cell_w, cell_h, gap


_CAPABILITY_LABEL_SUFFIX_STRIP = (" scenario",)


def _capability_icon_key(label: str | None) -> str | None:
    """Derive a ``cap-<slug>`` brand-pack key from a capability chip label.

    Slug = the label lowercased, spaces → "-", punctuation stripped. A known
    label decoration is stripped first: "Visibility Scenario" → "visibility"
    → ``cap-visibility`` (Gabriele's harvested icon is keyed on the bare
    capability name, not the "Scenario" variant BPA sometimes uses). Returns
    ``None`` for an empty/missing label; the caller treats a key that isn't in
    the loaded brand pack as "no icon" too (correct for e.g. Cloud ALM's
    "Implementation"/"Operations"/"Transformation" capabilities, which are
    text-only in Gabriele's originals — there is no ``cap-implementation``
    icon to find).
    """
    if not label:
        return None
    l = label.strip().lower()
    for suffix in _CAPABILITY_LABEL_SUFFIX_STRIP:
        if l.endswith(suffix):
            l = l[: -len(suffix)].strip()
            break
    l = re.sub(r"[^\w\s-]", "", l)
    slug = re.sub(r"\s+", "-", l).strip("-")
    return f"cap-{slug}" if slug else None


def _resolve_capability_icon(
    cap: Any,
    icon_resolver: Callable[[str], str | None] | None,
    brand_packs: dict | None,
) -> str | None:
    """The same "explicit icon wins, else auto-resolve from the label" lookup
    ``product_box`` applies per capability — pulled out so it can be run in a
    pre-pass (to decide the grid's row height, see ``product_box``) with the
    identical result the main per-chip loop will get, instead of two logic
    paths that could silently drift apart."""
    if not isinstance(cap, dict):
        return None
    icon = cap.get("icon")
    uri = icon_resolver(icon) if (icon and icon_resolver) else None
    if not uri and not icon:
        auto_key = _capability_icon_key(cap.get("label"))
        if auto_key:
            uri = _resolve_asset(auto_key, brand_packs)
    return uri


def product_box(
    node: Any,
    contract: dict,
    icon_resolver: Callable[[str], str | None] | None = None,
    brand_packs: dict | None = None,
) -> list[dict]:
    """Product node → white-panelled BTP-blue box + title row + a grid of
    capability chips. Every chip sits inside the box with ``product-box.padX``
    margins on all four sides; the box grows to fit the grid + title.

    A capability with no explicit ``icon`` is auto-resolved from
    ``brand_packs`` via ``_capability_icon_key`` (a harvested ``cap-<slug>``
    entry, e.g. ``cap-decision``); when the pack has no matching entry (absent
    pack, or a capability with no harvested icon like Cloud ALM's
    "Implementation") the chip stays text-only — never an error. An explicit
    ``icon`` on the capability always wins over auto-resolution."""
    box_style = _style(contract, "product-box")
    g = _geo(contract, "product-box")
    pad_x = _f(g, "padX", 60.8)
    title_row = _f(g, "titleRow", 48.08)
    base_w = _f(g, "w", 343.0)
    base_h = _f(g, "h", 199.85)
    cap_w, cap_h, cap_gap = _capability_grid_geometry(contract)

    caps = list(getattr(node, "capabilities", None) or [])
    # Resolve every capability's icon (if any) UP FRONT, once — both to decide
    # the grid's row height below (an icon-bearing row needs to be taller
    # than a text-only one; the row height is one shared number for the whole
    # grid, fixed before the per-chip loop lays out x/y, so it can't be
    # decided chip-by-chip) and to avoid the per-chip loop re-running the same
    # resolver/auto-key lookup a second time.
    cap_uris = [_resolve_capability_icon(cap, icon_resolver, brand_packs) for cap in caps]
    chip_geo = _geo(contract, "capability-chip")
    icon_w = _f(chip_geo, "iconW", 32.0)
    icon_h = _f(chip_geo, "iconH", 32.0)
    if any(cap_uris):
        # An icon-bearing chip stacks its icon ABOVE the label (see the style
        # comment below) instead of centering text alone, so it needs the
        # icon's own height plus room for one line of label text beneath it —
        # the text-only cap_h (sized for a single centered text line, no
        # icon) is too short and the label collides with the icon's bottom
        # edge. "Room for one label line" mirrors _pure_render.py's own
        # label-band-height convention for an icon caption
        # (``label_band_rect``: ``max(16.0, fontSize * 1.8)``, fontSize
        # defaulting to mxgraph's own 12 since the contract style sets none),
        # plus this chip's own spacingBottom=4 text inset.
        label_zone_h = max(16.0, 12.0 * 1.8) + 4.0
        cap_h = icon_h + label_zone_h
    n = len(caps)
    cols = 1 if n <= 1 else 2
    rows = math.ceil(n / cols) if n else 0
    grid_w = cols * cap_w + (cols - 1) * cap_gap if cols else 0.0
    grid_h = rows * cap_h + (rows - 1) * cap_gap if rows else 0.0

    top = max(title_row, pad_x)  # clear the title row AND honour the padX margin
    box_w = max(base_w, grid_w + 2 * pad_x)
    box_h = (top + grid_h + pad_x) if rows else max(base_h, top + pad_x)

    cells: list[dict] = [
        {
            "id": "box",
            "value": "",
            "style": box_style,
            "x": 0.0,
            "y": 0.0,
            "w": box_w,
            "h": box_h,
            "parent": None,
        }
    ]

    # Title row (product name), text style from the contract.
    cells.append(
        {
            "id": "title",
            "value": getattr(node, "label", "") or "",
            "style": _style(contract, "title-block"),
            "x": pad_x,
            "y": max(6.0, (top - 30.0) / 2.0),
            "w": box_w - 2 * pad_x,
            "h": 30.0,
            "parent": "box",
            "connectable": False,
        }
    )

    # Capability chips, centred horizontally inside the padX margins.
    grid_x = max(pad_x, (box_w - grid_w) / 2.0) if grid_w else pad_x
    chip_style = _style(contract, "capability-chip")
    for i, cap in enumerate(caps):
        col, row = i % cols, i // cols
        cx = grid_x + col * (cap_w + cap_gap)
        cy = top + row * (cap_h + cap_gap)
        style = chip_style
        uri = cap_uris[i]
        if uri:
            # The contract's base capability-chip style bakes in
            # imageWidth=64;imageHeight=64 (measured off the SSAM panel as a
            # WHOLE-panel number — see _capability_grid_geometry's
            # docstring), which is nearly 1.5x this per-capability chip's own
            # height and, at that size, the icon swallows the chip and sits
            # on top of the label instead of above it. The actual per-ICON
            # footprint the contract measured (``iconW``/``iconH``, 32x32 —
            # Gabriele's exemplar glyph size) is what belongs here;
            # overriding imageWidth/imageHeight AFTER the verbatim chip_style
            # prefix (styles are parsed key=value, last write wins) shrinks
            # just the icon-bearing case to the real icon size without
            # touching the shared base style string (still a required
            # verbatim prefix — see test_molecules.py) or any text-only chip
            # (which never gets an `image=` key at all).
            #
            # Icon top-centered (imageVerticalAlign=top) at its real 32x32
            # footprint, label bottom-centered (verticalAlign=bottom) below
            # it -- both inside the SAME bordered chip rect (now tall enough,
            # see cap_h above), stacked instead of overlapping (was: 64x64
            # icon nearly filling the whole chip, drawn UNDER a bottom-pinned
            # label with no room for both).
            style = (
                chip_style
                + "shape=label;imageAlign=center;imageVerticalAlign=top;"
                + "verticalAlign=bottom;spacingBottom=4;"
                + f"imageWidth={icon_w:g};imageHeight={icon_h:g};image={uri};"
            )
        cells.append(
            {
                "id": f"chip{i}",
                "value": (cap.get("label", "") if isinstance(cap, dict) else str(cap)),
                "style": style,
                "x": cx,
                "y": cy,
                "w": cap_w,
                "h": cap_h,
                "parent": "box",
                "connectable": False,
            }
        )
    return cells


def db_cell(node: Any, contract: dict) -> dict:
    """Database node → the contract cylinder (SAP-blue border, white fill)."""
    g = _geo(contract, "db")
    return {
        "id": "db",
        "value": getattr(node, "label", "") or "",
        "style": _style(contract, "db"),
        "x": 0.0,
        "y": 0.0,
        "w": _f(g, "w", 60.0),
        "h": _f(g, "h", 80.0),
        "parent": None,
    }


def chip_cell(node: Any, contract: dict) -> dict:
    """Chip node (small client/label chip) → the contract chip style."""
    g = _geo(contract, "chip")
    return {
        "id": "chip",
        "value": getattr(node, "label", "") or "",
        "style": _style(contract, "chip"),
        "x": 0.0,
        "y": 0.0,
        "w": _f(g, "w", 130.0),
        "h": _f(g, "h", 28.18),
        "parent": None,
    }


def persona(
    node: Any,
    contract: dict,
    icon_resolver: Callable[[str], str | None] | None = None,
) -> list[dict]:
    """Persona (user figure) → the contract persona image cell with its
    ``@{persona}`` placeholder resolved via ``icon_resolver`` (the node's
    ``genericIcon`` or a plain "user")."""
    g = _geo(contract, "persona")
    style = _style(contract, "persona")
    who = getattr(node, "genericIcon", None) or "user"
    uri = icon_resolver(who) if icon_resolver else None
    if uri:
        style = re.sub(r"@\{persona\}", uri, style)
    return [
        {
            "id": "persona",
            "value": getattr(node, "label", "") or "",
            "style": style,
            "x": 0.0,
            "y": 0.0,
            "w": _f(g, "w", 28.0),
            "h": _f(g, "h", 28.0),
            "parent": None,
            "connectable": False,
        }
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Footprint & reflow (Task 6)
#
# The skeleton layout (``_skeleton_layout.py``) must reserve the RIGHT amount of
# space for every molecule BEFORE placement: a product box is far bigger than a
# bare icon, and a frame (subaccount / governance / cloud-tier / custom-app) must
# be sized to contain its packed children plus its own decorations (title chip,
# label, badge row). These helpers own that geometry — they are the single
# source of truth shared by ``footprint`` (what the layout reserves) and the
# frame builders below (where they draw their decorations), so the two never
# drift. All numbers are plain geometry sourced from the contract via ``_geo``;
# no style literals live here (the ``test_no_style_literals`` guard greps this
# file). Every builder now accepts the FINAL ``size`` the layout computed, so a
# bottom-anchored decoration (a tier-box badge row) reflows to the real frame
# edge instead of floating at the contract's reference height.
# ─────────────────────────────────────────────────────────────────────────────
BADGE_GAP = 8.0  # horizontal gap between adjacent badge slots (see _append_badge_slots)
# Vertical clearance a bottom-anchored badge row keeps from its frame's OWN
# bottom border (cloud-tier). MUST match frame_insets' cloud-tier pad_bot
# reserve (``brow_h + TIER_BADGE_BOTTOM_MARGIN``) — tier_box draws the row at
# ``box_h - brow_h - TIER_BADGE_BOTTOM_MARGIN`` so the reserve and the draw
# offset share one number and can't drift apart again (they previously did:
# the reserve was right, but the draw offset was a stale ``box_h - 42.0``
# that let a 55px-tall hyperscaler badge overflow the frame bottom by ~13px).
TIER_BADGE_BOTTOM_MARGIN = 14.0

# Frame-title geometry (FIX-A). Frames draw their label as their OWN top-band
# cell (top-left, beside any chip) instead of the frame `value` — draw.io
# middle-centres a frame value over the packed children, which floated titles
# dead-centre on tall frames. ``_title_w`` estimates the caption width (12px
# Helvetica ≈ 6.6px/char) so the frame-min reserves room for chip + title on one
# header line. ``CHAR_W`` is the SINGLE source of truth for that advance:
# _skeleton_layout re-exports it (its ``_text_w`` uses the same value), so the
# frame-min estimate here and the layout's label-width estimate there can't drift
# apart. It lives in THIS module because _skeleton_layout depends on _molecules
# (never the reverse), so the shared constant belongs in the lower module.
CHAR_W = 6.6            # ≈Helvetica advance at 12px, for label-width estimates
TITLE_H = 24.0          # height of a standalone top-left frame-title cell
HEADER_GAP = 8.0        # gap between the SAP BTP chip and the title beside it


def _title_w(label: str, cap: float = 240.0) -> float:
    return min(cap, max(40.0, len(label or "") * CHAR_W + 12.0))


def subaccount_shows_chip(group_type: str | None, parent_type: str | None) -> bool:
    """Whether a BTP frame stamps the "SAP BTP" chip.

    The chip marks the OUTERMOST BTP container: a top-level subaccount, or one
    whose parent is not itself a BTP container. A subaccount nested inside a
    ``btp-layer``/``subaccount`` suppresses it (otherwise every nested tier
    repeats an identical "SAP BTP" chip — the staircase the review flagged) and
    shows only its own name.

    FIX-3: a ``governance`` frame is Gabriele's subaccount-style BTP governance
    band, which carries the chip like a subaccount (it is always a top-level
    container, so it always shows it). Single source of truth shared by the
    layout engine (frame-min sizing) and the emitter (which builder arg to
    pass)."""
    if group_type == "governance":
        return parent_type not in ("btp-layer", "subaccount")
    if group_type != "subaccount":
        return False
    return parent_type not in ("btp-layer", "subaccount")


def _frame_title_cell(group: Any, contract: dict, x: float, y: float,
                      box_w: float, h: float = TITLE_H) -> dict:
    """A frame's label as its OWN top-left cell (FIX-A), styled from the contract
    ``title-block`` (``align=left`` already). Fills the frame width to the right
    of ``x`` so the label reads on one header line. The frame's ``value`` is
    left empty by every builder so draw.io can't middle-centre a title over the
    packed children.

    The contract ``title-block`` is ``verticalAlign=middle``; we append
    ``verticalAlign=top`` (a positioning override, not a colour literal — the
    no-style-literals guard only greps for hardcoded fill/stroke colour hexes)
    so the label hugs the TOP of its reserved band instead of floating mid-cell,
    which is what the T6-review fix intended."""
    return {
        "id": "frame-title",
        "value": getattr(group, "label", "") or "",
        "style": _style(contract, "title-block") + "verticalAlign=top;",
        "x": x,
        "y": y,
        "w": max(40.0, box_w - x - 8.0),
        "h": h,
        "parent": "frame",
        "connectable": False,
    }


def _has_badges(group: Any) -> bool:
    b = getattr(group, "badges", None) or {}
    return bool(b.get("hyperscalers") or b.get("runtimes"))


def _badge_row_size(group: Any, contract: dict) -> tuple[float, float]:
    """(w, h) of the hyperscaler+runtime badge row a frame draws from
    ``group.badges`` — 0×0 when the group carries none. Mirrors the row
    ``_append_badge_slots`` lays out (hyperscaler slots then runtime slots,
    ``BADGE_GAP`` between them)."""
    badges = getattr(group, "badges", None) or {}
    w = 0.0
    h = 0.0
    for kind, coll in (("hyperscaler", "hyperscalers"), ("runtime", "runtimes")):
        bw, bh = _badge_slot_size(kind, contract)  # shared with _badge_slot: no drift
        for _ in (badges.get(coll) or []):
            w += bw + BADGE_GAP
            h = max(h, bh)
    if w:
        w -= BADGE_GAP
    return w, h


def frame_insets(group: Any, contract: dict) -> tuple[float, float, float]:
    """``(pad_x, pad_top, pad_bot)`` content insets for a frame molecule.

    ``pad_top`` reserves the space the frame's *own* decorations occupy at the
    top (SAP-BTP chip / label / a top-anchored badge row); ``pad_bot`` reserves a
    bottom-anchored badge row (cloud-tier). The skeleton layout places the
    frame's packed children at ``(pad_x, pad_top)`` and sizes the frame to
    ``content + these insets`` — so the insets here MUST match where the builders
    below draw those decorations."""
    gtype = getattr(group, "type", None)
    brow_h = _badge_row_size(group, contract)[1]
    if gtype == "subaccount":
        g = _geo(contract, "subaccount-frame")
        chip_h = _f(_geo(contract, "sap-btp-chip"), "h", 30.0)
        pad_x = _f(g, "padX", 11.57) + 8.0
        chip_bottom = _f(g, "padTop", 6.0) + chip_h
        # badge slots sit at padTop+36 (see subaccount_frame); clear them too.
        deco_bottom = max(chip_bottom, (_f(g, "padTop", 6.0) + 36.0 + brow_h) if brow_h else chip_bottom)
        return pad_x, deco_bottom + 10.0, pad_x
    if gtype == "governance":
        g = _geo(contract, "governance-strip")
        # FIX-3: the SAP BTP chip (y=8, h≈30) sits in the header, so the base
        # top inset must clear it before the badge row / content start.
        chip_h = _f(_geo(contract, "sap-btp-chip"), "h", 30.0)
        base_top = max(_f(g, "padTop", 35.0), 8.0 + chip_h)
        pad_top = base_top + (brow_h + 8.0 if brow_h else 8.0)
        return 24.0, pad_top, 16.0
    if gtype == "custom-app":
        # title band at the top; the runtime badge row is drawn just BELOW it
        # (custom_app_box, FIX-5). Reserve title + that row so packed children
        # start beneath both.
        return 16.0, 40.0 + (brow_h if brow_h else 0.0), 16.0
    if gtype == "cloud-tier":
        # label at the top; the badge row reflows to the BOTTOM (pad_bot holds it).
        return 10.0, 24.0, (brow_h + TIER_BADGE_BOTTOM_MARGIN if brow_h else 12.0)
    return 16.0, 32.0, 14.0


def _frame_min(group: Any, contract: dict, show_chip: bool = True) -> tuple[float, float]:
    """Smallest a frame may be regardless of child content — enough for its own
    decorations. For cloud-tier / custom-app the contract card size is the
    canonical minimum; the big container frames (subaccount / governance) use a
    decoration-driven minimum instead of their (huge) exemplar size.

    The frame's own TOP-BAND title (FIX-A) now counts toward the minimum width:
    a subaccount reserves ``chip + gap + title`` on one header line when it
    stamps the chip (``show_chip``), else just the title; governance / cloud-tier
    widen to hold their title too. ``show_chip`` must match what the emitter
    passes ``subaccount_frame`` (both derive it from ``subaccount_shows_chip``)."""
    gtype = getattr(group, "type", None)
    pad_x, pad_top, pad_bot = frame_insets(group, contract)
    brow_w, _ = _badge_row_size(group, contract)
    title_w = _title_w(getattr(group, "label", "") or "")
    if gtype == "cloud-tier":
        kind = (getattr(group, "kind", None) or "public").lower()
        mol = "tier-box-nonsap" if kind == "any-premise" else "tier-box-sap"
        g = _geo(contract, mol)
        min_w = max(_f(g, "w", 201.0), brow_w + 2 * pad_x, title_w + 2 * pad_x)
        return min_w, _f(g, "h", 92.85)
    if gtype == "custom-app":
        g = _geo(contract, "custom-app-box")
        return max(_f(g, "w", 343.0), title_w + 2 * pad_x), _f(g, "h", 185.93)
    if gtype == "subaccount":
        chip_w = _f(_geo(contract, "sap-btp-chip"), "w", 90.0)
        header_w = (chip_w + HEADER_GAP + title_w) if show_chip else title_w
        return max(header_w, brow_w) + 2 * pad_x, pad_top + pad_bot + 30.0
    if gtype == "governance":
        # FIX-3: reserve chip + gap + title on the header line when the chip shows.
        chip_w = _f(_geo(contract, "sap-btp-chip"), "w", 90.0)
        header_w = (chip_w + HEADER_GAP + title_w) if show_chip else title_w
        return max(120.0, brow_w, header_w) + 2 * pad_x, pad_top + pad_bot + 30.0
    return 2 * pad_x, pad_top + pad_bot


def footprint(obj: Any, contract: dict, children_bbox: tuple[float, float] = (0.0, 0.0),
              show_chip: bool = True) -> tuple[float, float]:
    """Minimum ``(w, h)`` a molecule occupies in layout space.

    Leaf node molecules (``product`` / ``db`` / ``chip``) have an intrinsic size
    (the capability grid drives a product box; ``db`` / ``chip`` come straight
    from the contract). Frame molecules take ``max(_frame_min, children_bbox +
    insets)`` — i.e. never smaller than their own decorations, always big enough
    to contain the packed children the layout passes in ``children_bbox``. This
    is the value ``_skeleton_layout`` reserves before placement (and passes back
    to the builders as their final ``size``). ``show_chip`` reaches the
    subaccount frame-min so a chip-suppressed nested subaccount doesn't reserve
    the (absent) chip's width."""
    t = getattr(obj, "type", None)
    cw, ch = children_bbox
    if t == "product":
        c = product_box(obj, contract)[0]
        return c["w"], c["h"]
    if t == "db":
        g = _geo(contract, "db")
        return _f(g, "w", 60.0), _f(g, "h", 80.0)
    if t == "chip":
        g = _geo(contract, "chip")
        return _f(g, "w", 130.0), _f(g, "h", 28.18)
    if t in ("subaccount", "governance", "custom-app", "cloud-tier"):
        pad_x, pad_top, pad_bot = frame_insets(obj, contract)
        min_w, min_h = _frame_min(obj, contract, show_chip)
        return max(min_w, cw + 2 * pad_x), max(min_h, pad_top + ch + pad_bot)
    return cw, ch


# ─────────────────────────────────────────────────────────────────────────────
# Group / frame molecules
# ─────────────────────────────────────────────────────────────────────────────
def subaccount_frame(group: Any, contract: dict, size: tuple[float, float] | None = None,
                     show_chip: bool = True) -> list[dict]:
    """Subaccount → white rounded frame (SAP-blue border) + a top-left header
    (an optional "SAP BTP" chip and the subaccount's OWN name) + any
    hyperscaler/runtime badge slots from ``group.badges``.

    FIX-A: the name is its own top-band cell (``frame-title``, top-left, beside
    the chip on one header line) and the frame ``value`` is empty, so draw.io
    never middle-centres the title over the packed children.
    FIX-B: the "SAP BTP" chip is emitted only when ``show_chip`` — i.e. on the
    OUTERMOST BTP container (see ``subaccount_shows_chip``); a nested subaccount
    passes ``show_chip=False`` and shows only its own name at the top-left."""
    g = _geo(contract, "subaccount-frame")
    pad_x = _f(g, "padX", 11.57)
    pad_top = _f(g, "padTop", 6.0)
    box_w, box_h = size if size else (_f(g, "w", 1001.0), _f(g, "h", 567.0))
    frame = {
        "id": "frame",
        "value": "",
        "style": _style(contract, "subaccount-frame"),
        "x": 0.0,
        "y": 0.0,
        "w": box_w,
        "h": box_h,
        "parent": None,
    }
    cells: list[dict] = [frame]

    cg = _geo(contract, "sap-btp-chip")
    chip_w, chip_h = _f(cg, "w", 90.0), _f(cg, "h", 30.0)
    x0 = pad_x + 8.0
    title_x = x0
    if show_chip:
        cells.append(
            {
                "id": "btpchip",
                "value": "SAP BTP",
                # contract text style + the SAP-BTP logo placeholder (resolved to
                # the brand-pack image when present, else stripped → the text chip).
                "style": _style(contract, "sap-btp-chip") + "image=@sap-btp-chip;",
                "x": x0,
                "y": pad_top,
                "w": chip_w,
                "h": chip_h,
                "parent": "frame",
                "connectable": False,
                "placeholder_mode": "strip",
            }
        )
        title_x = x0 + chip_w + HEADER_GAP
    # The subaccount's own name — its own top-band cell (beside the chip),
    # never the middle-centred frame value.
    cells.append(_frame_title_cell(group, contract, title_x, pad_top, box_w, chip_h))
    _append_badge_slots(cells, group, contract, "frame", x0, pad_top + 36.0)
    return cells


def governance_strip(group: Any, contract: dict, size: tuple[float, float] | None = None,
                     show_chip: bool = True) -> list[dict]:
    """Governance band → the contract BTP strip enclosing its members.

    FIX-3: like a subaccount, the governance frame carries a top-left "SAP BTP"
    chip beside its own name (Gabriele's governance box IS a subaccount-style
    BTP frame). ``show_chip`` mirrors ``subaccount_shows_chip`` so the layout's
    reserved header width and the drawn header agree."""
    g = _geo(contract, "governance-strip")
    box_w, box_h = size if size else (_f(g, "w", 946.0), _f(g, "h", 236.0))
    frame = {
        "id": "frame",
        "value": "",
        "style": _style(contract, "governance-strip"),
        "x": 0.0,
        "y": 0.0,
        "w": box_w,
        "h": box_h,
        "parent": None,
    }
    cells = [frame]
    cg = _geo(contract, "sap-btp-chip")
    chip_w, chip_h = _f(cg, "w", 90.0), _f(cg, "h", 30.0)
    x0 = 24.0
    title_x = x0
    if show_chip:
        cells.append(
            {
                "id": "btpchip",
                "value": "SAP BTP",
                "style": _style(contract, "sap-btp-chip") + "image=@sap-btp-chip;",
                "x": x0,
                "y": 8.0,
                "w": chip_w,
                "h": chip_h,
                "parent": "frame",
                "connectable": False,
                "placeholder_mode": "strip",
            }
        )
        title_x = x0 + chip_w + HEADER_GAP
    # FIX-A: title as its own top-left cell (beside the chip), never the
    # middle-centred frame value.
    cells.append(_frame_title_cell(group, contract, title_x, 8.0, box_w))
    _append_badge_slots(
        cells, group, contract, "frame",
        _f(g, "padX", 64.0), _f(g, "padTop", 35.0),
    )
    return cells


def tier_box(group: Any, contract: dict, size: tuple[float, float] | None = None) -> list[dict]:
    """Cloud-tier → SAP-blue box for ``public``/``private``, non-SAP grey box for
    ``any-premise`` (matches SSAM/Brandart), plus any brand chips (badge slots).

    The badge row is BOTTOM-anchored to ``box_h - badge_row_h -
    TIER_BADGE_BOTTOM_MARGIN``, where ``badge_row_h`` is the tallest badge
    actually present (55 for a hyperscaler, 32 for a runtime-only row) — the
    SAME reserve ``frame_insets`` adds as this frame's ``pad_bot``. So the
    row's bottom edge always sits ``TIER_BADGE_BOTTOM_MARGIN`` px inside the
    frame's own bottom border, never past it. (A prior version anchored at a
    fixed ``box_h - 42``, which put a 55px-tall hyperscaler badge's bottom
    edge ~13px BELOW the frame border whenever the frame was at/near its
    contract reference height — the reserve was already correct; only this
    draw offset had drifted from it.) When the layout grows the box taller
    than the contract reference height it passes the final ``size`` here so
    the row reflows to the true bottom edge instead of floating."""
    kind = (getattr(group, "kind", None) or "public").lower()
    molname = "tier-box-nonsap" if kind == "any-premise" else "tier-box-sap"
    g = _geo(contract, molname)
    # The layout always passes the footprint-derived ``size`` (which reserves the
    # badge row + title via _frame_min → _badge_row_size → _badge_slot_size); the
    # bare contract card size is only the size=None convenience default.
    box_w, box_h = size if size else (_f(g, "w", 201.0), _f(g, "h", 92.85))
    frame = {
        "id": "frame",
        "value": "",
        "style": _style(contract, molname),
        "x": 0.0,
        "y": 0.0,
        "w": box_w,
        "h": box_h,
        "parent": None,
    }
    # FIX-A: label top-left (matches the gold standard tier header), not the
    # middle-centred frame value. Kept within the tier's 24px top inset so it
    # clears any content placed at pad_top (e.g. the PCE chip).
    cells = [frame, _frame_title_cell(group, contract, 10.0, 4.0, box_w, 16.0)]
    badge_row_h = _badge_row_size(group, contract)[1]
    _append_badge_slots(cells, group, contract, "frame", 10.0,
                         box_h - badge_row_h - TIER_BADGE_BOTTOM_MARGIN)
    return cells


def custom_app_box(group: Any, contract: dict, size: tuple[float, float] | None = None) -> list[dict]:
    """Custom application → the contract card frame + a runtime badge slot (for
    any ``group.badges.runtimes``)."""
    g = _geo(contract, "custom-app-box")
    box_w, box_h = size if size else (_f(g, "w", 343.0), _f(g, "h", 185.93))
    frame = {
        "id": "frame",
        "value": "",
        "style": _style(contract, "custom-app-box"),
        "x": 0.0,
        "y": 0.0,
        "w": box_w,
        "h": box_h,
        "parent": None,
    }
    # FIX-A: label top-left, never the middle-centred frame value.
    title_y = 10.0
    cells = [frame, _frame_title_cell(group, contract, 16.0, title_y, box_w)]
    badges = getattr(group, "badges", None) or {}
    x = _f(g, "padX", 80.08)
    # FIX-5 (review): the runtime badge row sits BELOW the title band, not at the
    # old y=8. The title cell spans the full frame width, so a long custom-app
    # label would render over a top-anchored badge row. ``frame_insets``'
    # custom-app ``pad_top`` already reserves ``title + this row``, so packed
    # children still start beneath both.
    badge_y = title_y + TITLE_H + 4.0
    for name in (badges.get("runtimes") or []):
        slot = _badge_slot("runtime", str(name), contract)  # id already set by _badge_slot
        slot["x"] = x
        slot["y"] = badge_y
        slot["parent"] = "frame"
        cells.append(slot)
        x += slot["w"] + 8.0
    return cells


# ─────────────────────────────────────────────────────────────────────────────
# Edge-adjacent + decorative molecules
# ─────────────────────────────────────────────────────────────────────────────
_FLOW_FAMILY_MOLECULE = {
    "identity": "edge-identity",
    "provisioning": "edge-provisioning",
    "master-data": "edge-master-data",
    "transport": "edge-transport",
    "firewall": "edge-firewall",
    "default": "edge-default",
}

_KIND_PILL_FAMILY = {
    "trust": "pink",
    "authenticate": "green",
    "authorize": "purple",
}
_SUPPORTED_PILL_FAMILIES = {"green", "pink", "purple"}
_TRUST_PILL_KEYS = {"trust", "oidctrust", "mutualtrust"}
_AUTHN_PILL_KEYS = {
    "authenticate",
    "authentication",
    "saml",
    "saml2",
    "oidc",
    "saml2oidc",
    "jwt",
}
_AUTHZ_PILL_KEYS = {
    "authorize",
    "authorization",
    "authz",
    "policy",
    "role",
    "rolecollection",
    "rolecollections",
    "businessrole",
    "rolereplica",
    "scim",
}
_PILL_FAMILY_REPRESENTATIVES = {
    "green": ("SAML2/OIDC", "OIDC", "Authenticate"),
    "pink": ("OIDC Trust", "ORD", "Harmonized API"),
    "purple": ("Role", "Policy", "Group"),
}


def _pill_key(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _canonical_pill_entry(label: str | None) -> dict | None:
    if not label:
        return None
    catalog = _load_canonical_pills()
    entry = catalog.get(label)
    if isinstance(entry, dict):
        return entry
    norm = re.sub(r"\s+", "", label).lower()
    for name, candidate in catalog.items():
        if re.sub(r"\s+", "", name).lower() == norm and isinstance(candidate, dict):
            return candidate
    return None


def _semantic_pill_family(edge: Any, text: str) -> str | None:
    kind = str(getattr(edge, "kind", "") or "").lower()
    if kind in _KIND_PILL_FAMILY:
        return _KIND_PILL_FAMILY[kind]
    if kind == "annotation":
        pill_color = str(getattr(edge, "pillColor", "") or "").lower()
        if pill_color in _SUPPORTED_PILL_FAMILIES:
            return pill_color

    key = _pill_key(text)
    if key in _TRUST_PILL_KEYS:
        return "pink"
    if key in _AUTHN_PILL_KEYS:
        return "green"
    if key in _AUTHZ_PILL_KEYS:
        return "purple"

    canonical = _canonical_pill_entry(text)
    family = str(canonical.get("family", "") if canonical else "").lower()
    return family if family in _SUPPORTED_PILL_FAMILIES else None


def _replace_style_values(style: str, replacements: dict[str, str]) -> str:
    out: list[str] = []
    applied: set[str] = set()
    for token in filter(None, style.split(";")):
        key, sep, _value = token.partition("=")
        if sep and key in replacements:
            out.append(f"{key}={replacements[key]}")
            applied.add(key)
        else:
            out.append(token)
    for key, value in replacements.items():
        if key not in applied:
            out.append(f"{key}={value}")
    return ";".join(out) + ";"


def _canonical_pill_colors(label: str, family: str) -> tuple[str, str] | None:
    entry = _canonical_pill_entry(label)
    if (
        entry
        and str(entry.get("family", "")).lower() == family
        and entry.get("stroke")
        and entry.get("fill")
    ):
        return str(entry["stroke"]), str(entry["fill"])

    for representative in _PILL_FAMILY_REPRESENTATIVES.get(family, ()):
        entry = _canonical_pill_entry(representative)
        if entry and entry.get("stroke") and entry.get("fill"):
            return str(entry["stroke"]), str(entry["fill"])
    return None


def _semantic_pill_style(edge: Any, contract: dict, text: str) -> str:
    base = _style(contract, "pill-protocol")
    family = _semantic_pill_family(edge, text)
    if not family:
        return base
    colors = _canonical_pill_colors(text, family)
    if not colors:
        return base
    stroke, fill = colors
    return _replace_style_values(
        base,
        {
            "strokeColor": stroke,
            "fillColor": fill,
            "fontColor": stroke,
        },
    )


def flow_family_style(flow_family: str, contract: dict) -> str:
    """Contract edge style for a flow family (1:1 with the six edge-* molecules)."""
    molname = _FLOW_FAMILY_MOLECULE.get(flow_family, "edge-default")
    return _style(contract, molname)


def pill(edge: Any, contract: dict) -> dict:
    """Protocol pill vertex for an edge (e.g. "SCIM", "SAML2/OIDC"). Emitted at
    (0,0); the channel router (Task 8e) positions it along the edge later."""
    g = _geo(contract, "pill-protocol")
    text = getattr(edge, "pill", None) or getattr(edge, "label", "") or ""
    return {
        "id": f"pill-{getattr(edge, 'id', 'e')}",
        "value": text,
        "style": _semantic_pill_style(edge, contract, text),
        "x": 0.0,
        "y": 0.0,
        "w": _f(g, "w", 35.43),
        "h": _f(g, "h", 16.0),
        "parent": None,
        "connectable": False,
    }


def step_circle(node: Any, contract: dict) -> dict:
    """Numbered step circle for a node (numbers.xml default number ellipse)."""
    g = _geo(contract, "step-circle")
    step = getattr(node, "step", None)
    return {
        "id": f"step-{getattr(node, 'id', 'n')}",
        "value": "" if step is None else str(step),
        "style": _style(contract, "step-circle"),
        "x": 0.0,
        "y": 0.0,
        "w": _f(g, "w", 30.0),
        "h": _f(g, "h", 30.0),
        "parent": None,
        "connectable": False,
    }


def network_separator(x: float, y0: float, y1: float, contract: dict) -> list[dict]:
    """Vertical NETWORK zone separator: the grey jump-gap bar + its caption.

    The bar is an edge-style line between (x,y0) and (x,y1) (carries
    ``edge: True`` + explicit ``points``); the label is a text cell beside it."""
    line = {
        "id": "sep-line",
        "value": "",
        "style": _style(contract, "network-separator"),
        "x": float(x),
        "y": float(y0),
        "w": 0.0,
        "h": float(y1) - float(y0),
        "parent": None,
        "edge": True,
        "points": [(float(x), float(y0)), (float(x), float(y1))],
    }
    lg = _geo(contract, "network-separator-label")
    label_w = _f(lg, "w", 80.0)
    label_h = _f(lg, "h", 30.0)
    label = {
        "id": "sep-label",
        "value": "NETWORK",
        "style": _style(contract, "network-separator-label"),
        # Caption near the BOTTOM of the bar (gold standard SAP_Task_Center_L1),
        # CENTERED on the bar so it reads inside the gutter (FIX-3). The old
        # ``x - label_w + 2`` hard-left-aligned the 80px label at x-78, but the
        # gutter half-width is only ~ZONE_HGAP/2 (~48px), so ~30px overhung the
        # center column; centring at ``x - label_w/2`` keeps it within the gutter.
        "x": float(x) - label_w / 2.0,
        "y": float(y1) - label_h - 6.0,
        "w": label_w,
        "h": label_h,
        "parent": None,
    }
    return [line, label]


def branding_block(
    metadata: dict,
    contract: dict,
    brand_packs: dict,
    icon_resolver: Callable[[str], str | None] | None = None,
    warnings: list[str] | None = None,
) -> list[dict]:
    """Customer branding: an optional partner watermark, a customer-logo badge and
    a title cell. Each is an independent top-level cell (``parent`` is ``None``);
    the emitter places them. Unresolved logos degrade to text-badges — and, when
    ``warnings`` is supplied, append the same de-duplicated preflight WARNING
    every other badge slot gets (previously these two skipped that leg
    entirely: ``badge()`` was called with no ``icon_resolver``/``warnings``,
    so a missing customer logo or partner watermark degraded silently)."""
    cells: list[dict] = []
    branding = (metadata or {}).get("branding") or {}

    watermark = branding.get("partnerWatermark")
    if watermark:
        wc = badge("watermark", str(watermark), contract, brand_packs, icon_resolver, warnings)
        wg = _geo(contract, "watermark")
        wc["id"] = "watermark"
        wc["w"] = _f(wg, "w", 842.64)
        wc["h"] = _f(wg, "h", 143.94)
        wc["parent"] = None
        cells.append(wc)

    logo = branding.get("customerLogo")
    if logo:
        # A customer logo is an image badge that degrades to a text chip with
        # the customer name (e.g. "ACME") when the (usually .local) asset is
        # absent — badge() already applies that fallback (and now warns too).
        lc = badge("hyperscaler", str(logo), contract, brand_packs, icon_resolver, warnings)
        lc["id"] = "customer-logo"
        lc["parent"] = None
        cells.append(lc)

    title = (metadata or {}).get("title")
    if title:
        cells.append(
            {
                "id": "brand-title",
                "value": title,
                "style": _style(contract, "title-block"),
                "x": 0.0,
                "y": 0.0,
                "w": 260.0,
                "h": 30.0,
                "parent": None,
                "connectable": False,
            }
        )
    return cells
