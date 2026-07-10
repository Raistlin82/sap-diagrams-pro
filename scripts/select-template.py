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


_B = _load_builder()
FAMILY_KEYWORDS = _B.FAMILY_KEYWORDS
SCENARIO_ALIASES = _B.SCENARIO_ALIASES
kw_hit = _B.kw_hit
infer_family = _B.infer_family
detect_scenarios = _B.detect_scenarios

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


def load_index(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("request", nargs="*", help="free-text diagram request; stdin if omitted")
    ap.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--level", help="constrain / bias to a level (L0|L1|L2|L3)")
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
    ranked = rank(load_index(args.index), query, args.top, req_level)

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
