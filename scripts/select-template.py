#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""Rank the SAP reference templates in ``assets/template-index.json`` against a
free-text diagram request — the first step of the hybrid *scaffold* path.

The engine can either GENERATE a diagram procedurally from an IR
(``generate-drawio.py``) or SCAFFOLD one from the closest real SAP reference
``.drawio`` and make surgical edits. This selector answers the routing
question: *is there a real SAP template close enough to scaffold from?*

Scoring is deterministic and stdlib-only. It reads the prebuilt index (no
per-run XML parsing) and blends signals against every entry, from strongest
to weakest:

  * scenarioAliases hits   — curated canonical scenario markers (Task Center,
                             Joule, MCP, Private Link, …). Strongest signal.
  * family match           — the request's inferred family == the template's.
  * serviceTokens overlap  — request words found (word-boundary) inside the
                             template's canonical service names.
  * title / filename match — request words in the template title or filename.
  * labelTokens overlap    — request words in the template's full-text word bag
                             (weakest; capped so large templates can't dominate).
  * level match            — when the request names L0/L1/L2, reward the same
                             level and mildly penalise a different explicit one.

Query family/scenario detection reuses the SAME vocabularies the index was built
with (``build-template-index.py``), so a request and a template are classified
consistently. All keyword matching is word-boundary (``kw_hit``), so "storage"
never matches "rag", "aws" never matches "flaws", etc.

Confidence threshold
--------------------
A template is flagged ``recommended`` when the top score clears
``RECOMMEND_THRESHOLD`` (default 14.0). 14.0 is deliberately above any single
weak signal: one alias hit alone (10) is *not* enough, but an alias hit plus a
family match (10+6), or an alias hit plus real service/label overlap, clears it.
This keeps the scaffold path from firing on a vague lexical coincidence — when
nothing clears the bar the caller should fall back to the procedural engine.

Usage:
  select-template.py "<request>" [--top N] [--level L2] [--json]
  echo "<request>" | select-template.py
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
DEFAULT_INDEX = _REPO / "assets" / "template-index.json"

# Confidence bar the top candidate must clear to be flagged `recommended`
# (and therefore worth scaffolding from). See the module docstring.
RECOMMEND_THRESHOLD = 14.0

# Per-signal weights.
W_ALIAS = 10.0          # per curated scenario-alias hit (strongest)
W_FAMILY = 6.0          # request family == template family
W_SERVICE = 3.0         # per request word found in a service token
W_TITLE = 4.0           # per request word in title/filename
W_LABEL = 1.0           # per request word in the label word-bag
CAP_SERVICE = 24.0      # ceiling on service-token contribution
CAP_LABEL = 8.0         # ceiling on label-token contribution
W_LEVEL_MATCH = 5.0     # explicit level in request == template level
W_LEVEL_MISS = -3.0     # explicit level given, template is a *different* level


# --- reuse the index-builder vocabularies so query & template agree ----------
def _load_builder():
    """Import build-template-index.py (dashed filename) for its shared
    classification vocabularies, so a request is bucketed with the exact same
    rules that produced the index."""
    path = _HERE / "build-template-index.py"
    spec = importlib.util.spec_from_file_location("_build_template_index", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("_build_template_index", mod)
    spec.loader.exec_module(mod)
    return mod


def _load_edit():
    """Import the shared edit helpers (``_drawio_edit.py``) once, reusing an
    already-registered copy (e.g. loaded by a test's ``load_script``) so module
    identity stays single."""
    if "_drawio_edit" in sys.modules:
        return sys.modules["_drawio_edit"]
    path = _HERE / "_drawio_edit.py"
    spec = importlib.util.spec_from_file_location("_drawio_edit", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_drawio_edit"] = mod
    spec.loader.exec_module(mod)
    return mod


_B = _load_builder()
FAMILY_KEYWORDS = _B.FAMILY_KEYWORDS
SCENARIO_ALIASES = _B.SCENARIO_ALIASES
kw_hit = _B.kw_hit
infer_family = _B.infer_family
detect_scenarios = _B.detect_scenarios
clean_label = _B.clean_label
_EDIT = _load_edit()

# --- coverage / decision tuning ---------------------------------------------
# Minimum share of the requested components the winner must already contain for
# a scaffold-extend to be worthwhile (below this, generate from scratch).
COVERAGE_MIN = 0.4
# Most *heavy* (structural container) extras a scaffold-extend may strip; more
# than this means the template's skeleton is wrong for the request.
HEAVY_EXTRA_MAX = 1
TEMPLATES_DIR = _REPO / "assets" / "templates"

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9+/&.\-]*", re.IGNORECASE)
# Framing words that carry no discriminating signal for template selection.
_STOPWORDS = {
    "a", "an", "and", "app", "apps", "architecture", "arch", "as", "at", "be",
    "between", "btp", "build", "by", "calls", "call", "cloud", "create",
    "diagram", "diagrams", "draw", "for", "from", "generate", "in", "into",
    "is", "it", "l0", "l1", "l2", "l3", "landscape", "level", "make", "me",
    "my", "of", "on", "or", "sap", "show", "solution", "system", "systems",
    "that", "the", "then", "to", "uses", "use", "using", "via", "with",
}
_LEVEL_RE = re.compile(r"\bL([0-3])\b", re.IGNORECASE)


def query_tokens(text: str) -> set[str]:
    """Discriminating lowercase word set for overlap scoring."""
    out: set[str] = set()
    for m in _WORD_RE.finditer(text):
        w = m.group(0).lower()
        if len(w) >= 2 and w not in _STOPWORDS:
            out.add(w)
    return out


def explicit_level(text: str) -> str | None:
    m = _LEVEL_RE.search(text)
    return f"L{m.group(1)}" if m else None


def token_bag(values) -> set[str]:
    """Word-set of an iterable of strings (service tokens / title / filename)."""
    out: set[str] = set()
    for v in values or ():
        for m in _WORD_RE.finditer(str(v)):
            w = m.group(0).lower()
            if len(w) >= 2:
                out.add(w)
    return out


@dataclass
class Ranked:
    id: str
    file: str
    score: float
    level: str
    family: str
    title: str
    reasons: list[str] = field(default_factory=list)
    aliasHits: list[str] = field(default_factory=list)
    recommended: bool = False


def score_entry(entry: dict, q_tokens: set[str], q_aliases: set[str],
                q_family: str | None, req_level: str | None) -> Ranked:
    reasons: list[str] = []
    value = 0.0

    # 1. scenario-alias hits (strongest)
    tmpl_aliases = set(entry.get("scenarioAliases") or [])
    alias_hits = sorted(q_aliases & tmpl_aliases)
    if alias_hits:
        value += W_ALIAS * len(alias_hits)
        reasons.append("scenario match: " + ", ".join(alias_hits)
                       + f" (+{W_ALIAS * len(alias_hits):.0f})")

    # 2. family match
    tmpl_family = str(entry.get("family") or "")
    if q_family and q_family != "generic" and q_family == tmpl_family:
        value += W_FAMILY
        reasons.append(f"family match: {tmpl_family} (+{W_FAMILY:.0f})")

    # 3. serviceTokens overlap (word-boundary against service names)
    svc_words = token_bag(entry.get("serviceTokens"))
    svc_hits = sorted(q_tokens & svc_words)
    if svc_hits:
        boost = min(CAP_SERVICE, W_SERVICE * len(svc_hits))
        value += boost
        reasons.append("service overlap: " + ", ".join(svc_hits[:8])
                       + f" (+{boost:.0f})")

    # 4. title / filename overlap
    title = str(entry.get("title") or "")
    title_words = token_bag([title, entry.get("file", "")])
    title_hits = sorted(q_tokens & title_words)
    if title_hits:
        boost = W_TITLE * len(title_hits)
        value += boost
        reasons.append("title/file match: " + ", ".join(title_hits[:8])
                       + f" (+{boost:.0f})")

    # 5. labelTokens overlap (weakest, capped)
    label_words = set(entry.get("labelTokens") or [])
    label_hits = sorted(q_tokens & label_words)
    if label_hits:
        boost = min(CAP_LABEL, W_LABEL * len(label_hits))
        value += boost
        reasons.append("label overlap: " + ", ".join(label_hits[:8])
                       + f" (+{boost:.0f})")

    # 6. explicit level
    tmpl_level = str(entry.get("level") or "unknown")
    if req_level:
        if req_level == tmpl_level:
            value += W_LEVEL_MATCH
            reasons.append(f"level match: {req_level} (+{W_LEVEL_MATCH:.0f})")
        elif tmpl_level in ("L0", "L1", "L2", "L3"):
            value += W_LEVEL_MISS
            reasons.append(f"different level {tmpl_level} ({W_LEVEL_MISS:.0f})")

    if not reasons:
        reasons.append("weak lexical match; review manually")

    return Ranked(
        id=str(entry.get("id", "")),
        file=str(entry.get("file", "")),
        score=round(value, 1),
        level=tmpl_level,
        family=tmpl_family,
        title=title,
        reasons=reasons,
        aliasHits=alias_hits,
    )


def rank(index: dict, query: str, top: int, req_level: str | None = None) -> list[Ranked]:
    q_tokens = query_tokens(query)
    q_aliases = set(detect_scenarios(query))
    q_family = infer_family(query)
    lvl = req_level or explicit_level(query)

    ranked = [score_entry(e, q_tokens, q_aliases, q_family, lvl)
              for e in index.get("templates", [])]
    ranked.sort(key=lambda r: (-r.score, r.id))
    top_n = ranked[:max(1, top)]
    if top_n and top_n[0].score >= RECOMMEND_THRESHOLD:
        top_n[0].recommended = True
    return top_n


# --------------------------------------------------------------------------- #
# Component coverage + scaffold/extend/generate decision (the --components path)
# --------------------------------------------------------------------------- #
def enumerate_components(entry: dict) -> list[str]:
    """The template's *component* set: ``serviceTokens`` + ``scenarioAliases``,
    deduped case-insensitively, order preserved. ``labelTokens`` are deliberately
    excluded — they are a noisy word-bag (e.g. stray ``16px`` fragments) meant
    for lexical ranking, not for enumerating real components."""
    out: list[str] = []
    seen: set[str] = set()
    for key in ("serviceTokens", "scenarioAliases"):
        for v in entry.get(key) or ():
            s = str(v).strip()
            if s and s.lower() not in seen:
                seen.add(s.lower())
                out.append(s)
    return out


def _labeled_cell_id(doc, label: str) -> str | None:
    """Id of the cell whose (cleaned) label matches ``label``, or ``None``.

    Tries the shared exact/case-insensitive matcher first (clean scaffolds carry
    the label verbatim on ``mxCell/@value``); falls back to cleaning HTML-bearing
    ``mxCell``/``object``/``UserObject`` labels — as the real SAP corpus stores
    them — so structural (heavy) extras are still detected there."""
    cell = _EDIT.find_cell_by_label(doc, label)
    if cell is not None:
        return cell.get("id")
    low = label.lower()
    ci: str | None = None
    for c in _EDIT.iter_cells(doc):
        raw = c.get("value")
        if not raw:
            continue
        cl = clean_label(raw)
        if cl == label:
            return c.get("id")
        if ci is None and cl.lower() == low:
            ci = c.get("id")
    for el in _EDIT.root(doc).iter():
        if el.tag in ("object", "UserObject"):
            raw = el.get("label") or el.get("value") or ""
            cl = clean_label(raw)
            if cl == label:
                return el.get("id")
            if ci is None and cl.lower() == low:
                ci = el.get("id")
    return ci


def classify_extra(doc, label: str) -> str:
    """``"heavy"`` iff the extra is a container (some cell parents to it), i.e.
    removing it means unpicking nested content; else ``"light"`` (a leaf swap).
    Unlocatable labels default to ``"light"`` (the conservative, non-blocking
    outcome)."""
    if doc is None:
        return "light"
    cid = _labeled_cell_id(doc, label)
    if cid is None:
        return "light"
    return "heavy" if _EDIT.children(doc, cid) else "light"


def _clean_requested(requested) -> list[str]:
    return [s for s in (str(r).strip() for r in requested or ()) if s]


def coverage_report(entry: dict, requested, templates_dir=None) -> dict:
    """Compare ``requested`` components against the template's own components.

    PRESENT = requested found in the template; MISSING = requested not found;
    EXTRA = template components nobody requested (each tagged light/heavy by
    opening the candidate ``.drawio``). Matching is word-boundary (``kw_hit``)
    with BOTH sides lowercased, so canonical names like ``Integration Suite``
    match ``SAP Integration Suite`` and never partial-match across words."""
    req = _clean_requested(requested)
    comps = enumerate_components(entry)
    comps_low = [(c, c.lower()) for c in comps]
    req_low = [(r, r.lower()) for r in req]

    present, missing = [], []
    for r, rl in req_low:
        (present if any(kw_hit(cl, rl) for _c, cl in comps_low) else missing).append(r)

    extra_labels = [c for c, cl in comps_low
                    if not any(kw_hit(cl, rl) for _r, rl in req_low)]

    doc = None
    if extra_labels:
        base = Path(templates_dir) if templates_dir is not None else TEMPLATES_DIR
        path = base / str(entry.get("file", ""))
        if path.exists():
            doc = _EDIT.load(path)

    extra = []
    heavy_count = 0
    for label in extra_labels:
        weight = classify_extra(doc, label)
        if weight == "heavy":
            heavy_count += 1
        extra.append({"label": label, "weight": weight})

    coverage = round(len(present) / (len(req) or 1), 3)
    return {
        "present": present,
        "missing": missing,
        "extra": extra,
        "heavyCount": heavy_count,
        "coverage": coverage,
    }


# --------------------------------------------------------------------------- #
# Template-informed completeness: triage template extras into interview
# SUGGESTIONS (consensus across candidates OR a best-practice match).
# --------------------------------------------------------------------------- #
def _canon_key(label: str) -> str:
    """Grouping key: cleaned, lowercased, SAP-prefix-stripped."""
    return clean_label(label).lower().removeprefix("sap ").strip()


def suggest_extras(candidates, requested, best_practice=(), top_n=5,
                   min_consensus=2, cap=6, templates_dir=None):
    """Triage the top candidates' EXTRA components into completeness suggestions.

    ``candidates`` are index-entry dicts (NOT ``Ranked``); only the first
    ``top_n`` are used. ``coverage_report`` already excludes ``requested`` from
    each candidate's ``extra``. A canonical extra (grouped by ``_canon_key``) is
    suggested iff it appears in >= ``min_consensus`` distinct candidates OR it
    word-boundary-matches a ``best_practice`` name (both directions). Returns
    ``[{label, consensus, candidates, bestPractice, reason}]`` sorted by
    consensus desc then label, capped at ``cap``."""
    cands = list(candidates)[:top_n]
    n = len(cands)
    bp_low = [b.lower() for b in _clean_requested(best_practice)]
    req_keys = [k for k in (_canon_key(r) for r in _clean_requested(requested)) if k]

    buckets: dict[str, dict] = {}
    for i, entry in enumerate(cands):
        rep = coverage_report(entry, requested, templates_dir)
        for e in rep["extra"]:
            label = clean_label(e["label"])
            key = _canon_key(e["label"])
            if not key:
                continue
            b = buckets.setdefault(key, {"cands": set(), "spellings": Counter()})
            b["cands"].add(i)
            b["spellings"][label] += 1

    out = []
    for key, b in buckets.items():
        if any(kw_hit(key, rk) or kw_hit(rk, key) for rk in req_keys):
            continue
        consensus = len(b["cands"])
        bp = any(kw_hit(key, x) or kw_hit(x, key) for x in bp_low)
        if consensus < min_consensus and not bp:
            continue
        label = sorted(b["spellings"].items(),
                       key=lambda kv: (-kv[1], len(kv[0]), kv[0]))[0][0]
        reasons = []
        if consensus >= min_consensus:
            reasons.append(f"in {consensus}/{n} reference simili")
        if bp:
            reasons.append("best-practice")
        out.append({"label": label, "consensus": consensus, "candidates": n,
                    "bestPractice": bp, "reason": " · ".join(reasons)})

    out.sort(key=lambda s: (-s["consensus"], s["label"]))
    return out[:cap]


def decide(entry: dict, requested, recommended: bool, templates_dir=None) -> dict:
    """Route the winner to scaffold / scaffold-extend / generate (first match
    wins) and, for the scaffold paths, emit a bounded delta plan.

    0. ``generate`` (gutting guard) — if more template components would be
                             removed than kept (``len(extra) > len(present)``),
                             the template is the wrong base.
    1. ``scaffold``        — ★ recommended, nothing missing and nothing extra:
                             copy the template and relabel in place.
    2. ``scaffold-extend`` — ★ recommended, coverage ≥ COVERAGE_MIN, at least one
                             missing/extra, and the heavy guard holds: copy then
                             apply the delta (remove extras / relabel / add missing).
    3. ``generate``        — anything else: fall back to the procedural engine.

    Heavy guard (BOTH must hold): heavy ≤ HEAVY_EXTRA_MAX AND heavy ≤ zoneCount/3
    — a template with few zones can't absorb even one heavy structural removal."""
    rep = coverage_report(entry, requested, templates_dir)
    present, missing, extra = rep["present"], rep["missing"], rep["extra"]
    coverage, heavy = rep["coverage"], rep["heavyCount"]

    zone_count = float(entry.get("zoneCount") or 0)
    heavy_guard = heavy <= HEAVY_EXTRA_MAX and heavy <= zone_count / 3

    if len(extra) > len(present):
        # Gutting guard: stripping more than we keep means the template is the
        # wrong base (a hole-y layout; remove doesn't reflow/shrink frames).
        decision = "generate"
    elif recommended and not missing and not extra:
        decision = "scaffold"
    elif (recommended and coverage >= COVERAGE_MIN
          and (missing or extra) and heavy_guard):
        decision = "scaffold-extend"
    else:
        decision = "generate"

    # Delta plan: strip extras, relabel each present match to its canonical
    # requested name, add the missing components.
    comps = enumerate_components(entry)
    relabel = []
    for r in present:
        rl = r.lower()
        for c in comps:
            if kw_hit(c.lower(), rl):
                relabel.append({"from": c, "to": r})
                break
    delta = {
        "remove": [e["label"] for e in extra],
        "relabel": relabel,
        "add": list(missing),
    }

    return {
        "decision": decision,
        "coverage": coverage,
        "present": present,
        "missing": missing,
        "extra": extra,
        "heavyGuardOk": heavy_guard,
        "delta": delta,
    }


def choose_decision(index: dict, ranked: list[Ranked], requested,
                    templates_dir=None) -> tuple[dict, Ranked | None, list[dict]]:
    """Pick the first ranked candidate that can actually reuse a template.

    Ranking still determines the order, but a top candidate that falls back to
    ``generate`` because it would be gutted or fails coverage/heavy guards no
    longer prevents the next close candidate from being reused.
    """
    evaluated: list[dict] = []
    entries = {e.get("id"): e for e in index.get("templates", [])}

    first: tuple[dict, Ranked] | None = None
    for r in ranked:
        entry = entries.get(r.id, {"id": r.id, "file": r.file})
        result = decide(entry, requested, r.recommended, templates_dir)
        result["template"] = r.id
        result["recommended"] = r.recommended
        result["rankScore"] = r.score
        result["rankReasons"] = list(r.reasons)
        evaluated.append({
            "template": r.id,
            "decision": result["decision"],
            "rankScore": r.score,
            "coverage": result["coverage"],
            "heavyGuardOk": result["heavyGuardOk"],
            "recommended": r.recommended,
        })
        if first is None:
            first = (result, r)
        if result["decision"] != "generate":
            result["candidatesEvaluated"] = evaluated
            return result, r, evaluated

    if first is not None:
        result, r = first
        result["candidatesEvaluated"] = evaluated
        return result, r, evaluated

    result = {
        "decision": "generate",
        "coverage": 0.0,
        "present": [],
        "missing": _clean_requested(requested),
        "extra": [],
        "heavyGuardOk": False,
        "delta": {"remove": [], "relabel": [], "add": _clean_requested(requested)},
        "template": None,
        "recommended": False,
        "rankScore": 0.0,
        "rankReasons": [],
        "candidatesEvaluated": evaluated,
    }
    return result, None, evaluated


def load_index(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def restrict_to_available(index: dict) -> dict:
    """On Desktop the loose ``assets/templates/`` corpus isn't bundled, only the
    curated ``templates-pack.json``. Rank only what can actually be scaffolded
    here: the loose corpus when present, else the packed subset. When neither is
    present, leave the index as-is (ranking still informs the procedural path)."""
    loose = _REPO / "assets" / "templates"
    if loose.exists() and any(loose.glob("*.drawio")):
        return index
    pack = _REPO / "assets" / "templates-pack.json"
    if pack.exists():
        ids = {e.get("id") for e in json.loads(pack.read_text(encoding="utf-8")).get("templates", [])}
        kept = [e for e in index.get("templates", []) if e.get("id") in ids]
        if kept:
            return {**index, "templates": kept}
    return index


def _emit_coverage(index: dict, ranked: list[Ranked], components: str,
                   as_json: bool, suggest: bool = False,
                   best_practice: str = "") -> int:
    """Report component coverage + the routing decision for the top candidate."""
    requested = _clean_requested(components.split(","))
    result, top, _evaluated = choose_decision(index, ranked, requested)

    if suggest:
        entries = [next((e for e in index.get("templates", []) if e.get("id") == r.id),
                        {"id": r.id, "file": r.file}) for r in ranked]
        result["suggestions"] = suggest_extras(
            entries, requested,
            _clean_requested((best_practice or "").split(",")),
            top_n=len(entries))

    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    top_label = f"{top.id}  [{top.family}/{top.level}]" if top else "none"
    print("template : " + top_label
          + ("  * recommended" if top and top.recommended else ""))
    print(f"decision : {result['decision']}")
    print(f"coverage : {result['coverage']:.2f}  (min {COVERAGE_MIN})")
    if result["present"]:
        print("present  : " + ", ".join(result["present"]))
    if result["missing"]:
        print("missing  : " + ", ".join(result["missing"]))
    if result["extra"]:
        print("extra    : " + ", ".join(
            f"{e['label']} [{e['weight']}]" for e in result["extra"]))
    if result["decision"] != "generate":
        d = result["delta"]
        print(f"delta    : remove={len(d['remove'])} "
              f"relabel={len(d['relabel'])} add={len(d['add'])}")
    if suggest and result.get("suggestions"):
        print("suggest  : " + ", ".join(
            f"{s['label']} ({s['reason']})" for s in result["suggestions"]))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("request", nargs="*", help="free-text diagram request; stdin if omitted")
    ap.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--level", help="constrain / bias to a level (L0|L1|L2|L3)")
    ap.add_argument("--components",
                    help="comma-separated components to check against the top "
                         "candidate; prints a coverage report + a scaffold / "
                         "scaffold-extend / generate decision")
    ap.add_argument("--suggest", action="store_true",
                    help="with --components: also emit completeness suggestions "
                         "(template extras seen across the top candidates)")
    ap.add_argument("--best-practice", default="",
                    help="comma-separated best-practice component names to OR into "
                         "the --suggest triage")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)

    query = " ".join(args.request).strip() or sys.stdin.read().strip()
    if not query:
        print("request required", file=sys.stderr)
        return 2
    if not args.index.exists():
        print(f"{args.index}: template index not found "
              "(run scripts/build-template-index.py)", file=sys.stderr)
        return 2

    req_level = args.level.upper() if args.level else None
    index = restrict_to_available(load_index(args.index))
    ranked = rank(index, query, args.top, req_level)

    # Coverage + decision path (only when --components is given). Without it the
    # output below is EXACTLY the pre-existing ranking behaviour.
    if args.suggest and args.components is None:
        print("--suggest requires --components", file=sys.stderr)
        return 2
    if args.components is not None:
        return _emit_coverage(index, ranked, args.components, args.json,
                              suggest=args.suggest, best_practice=args.best_practice)

    if args.json:
        payload = {
            "query": query,
            "threshold": RECOMMEND_THRESHOLD,
            "recommended": ranked[0].id if ranked and ranked[0].recommended else None,
            "candidates": [asdict(r) for r in ranked],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(f"query    : {query}")
    print(f"threshold: {RECOMMEND_THRESHOLD}  (recommended when top score clears it)")
    for i, r in enumerate(ranked, 1):
        flag = "  * recommended" if r.recommended else ""
        print(f"{i}. {r.score:6.1f}  {r.id}  [{r.family}/{r.level}]{flag}")
        for reason in r.reasons[:3]:
            print(f"     - {reason}")
    if not (ranked and ranked[0].recommended):
        print("\nno template clears the confidence threshold — "
              "use the procedural engine (generate-drawio.py).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
