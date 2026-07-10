#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""Scaffold a new SAP ``.drawio`` diagram by copying the closest real reference
template — the second step of the hybrid *scaffold* path.

Given a free-text request, this runs ``select-template.py`` (or honours an
explicit ``--template``), copies the chosen ``assets/templates/<file>`` to the
requested output, and prints:

  (a) which template was used and why (the selector's reasons),
  (b) the alternates (open one if the chosen family is wrong),
  (c) a checklist of what to relabel / swap for this request — derived from the
      diff between the request's words and the template's service/label tokens.

The scaffolded copy is a pristine SAP diagram: canvas, zones, Horizon palette,
fonts and icons are all inherited. Adapt it with ``relabel.py`` (surgical label
edits) + ``sap-icons-resolve`` (icon swaps), then run the SAME downstream gate
the procedural path uses (``validate-drawio.py`` + ``check-composition.py`` +
``score-diagram.py --corpus``).

If no template clears the selector's confidence threshold, this exits with a
distinct code (3) and tells the caller to use the procedural engine
(``generate-drawio.py``) instead.

Usage:
  scaffold-diagram.py "<request>" --out out.drawio
  scaffold-diagram.py "<request>" --out out.drawio --template sap-task-center-l1
  scaffold-diagram.py "<request>" --dry-run          # rank only, copy nothing

Exit codes:
  0 — scaffolded (or --dry-run printed candidates)
  1 — error (bad --template, copy failure, …)
  2 — usage
  3 — no template clears the threshold → fall back to generate-drawio.py
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
TEMPLATES_DIR = _REPO / "assets" / "templates"
DEFAULT_INDEX = _REPO / "assets" / "template-index.json"

# Distinct exit code so a caller can branch: "no close template".
EXIT_NO_TEMPLATE = 3


def _load_selector():
    path = _HERE / "select-template.py"
    spec = importlib.util.spec_from_file_location("_select_template", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("_select_template", mod)
    spec.loader.exec_module(mod)
    return mod


_S = _load_selector()


def find_entry(index: dict, ident: str) -> dict | None:
    """Resolve a template by id or by (case-insensitive) filename/stem."""
    low = ident.lower()
    stem = low[:-7] if low.endswith(".drawio") else low
    for e in index.get("templates", []):
        if e.get("id", "").lower() == low:
            return e
        fname = str(e.get("file", "")).lower()
        if fname == low or fname == f"{stem}.drawio":
            return e
    return None


def relabel_checklist(entry: dict, query: str) -> tuple[list[str], list[str]]:
    """Diff the request against the template's tokens.

    Returns (add_or_relabel, swap_or_remove):
      * add_or_relabel — request words with no home in the template's service or
        label tokens: things you likely need to introduce (relabel a cell / swap
        an icon) to fit the request.
      * swap_or_remove — the template's service tokens whose words never appear
        in the request: candidates to relabel to your services or drop.
    """
    q_tokens = _S.query_tokens(query)
    svc_words = _S.token_bag(entry.get("serviceTokens"))
    label_words = set(entry.get("labelTokens") or [])
    covered = svc_words | label_words
    add_or_relabel = sorted(t for t in q_tokens if t not in covered)

    swap_or_remove: list[str] = []
    for svc in entry.get("serviceTokens") or []:
        words = _S.token_bag([svc])
        if words and not (words & q_tokens):
            swap_or_remove.append(str(svc))
    return add_or_relabel, sorted(set(swap_or_remove))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("request", nargs="*", help="free-text request; stdin if omitted")
    ap.add_argument("-o", "--out", type=Path, help="output .drawio path")
    ap.add_argument("--template", help="explicit template id or filename (skips ranking)")
    ap.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    ap.add_argument("--templates-dir", type=Path, default=TEMPLATES_DIR)
    ap.add_argument("--top", type=int, default=5, help="candidates to show")
    ap.add_argument("--dry-run", action="store_true", help="rank only; copy nothing")
    ap.add_argument("--force", action="store_true", help="overwrite --out if it exists")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)

    query = " ".join(args.request).strip() or (sys.stdin.read().strip() if not sys.stdin.isatty() else "")
    if not query and not args.template:
        print("request or --template required", file=sys.stderr)
        return 2
    if not args.index.exists():
        print(f"{args.index}: template index not found", file=sys.stderr)
        return 1

    index = _S.load_index(args.index)
    ranked = _S.rank(index, query, args.top) if query else []

    chosen: dict | None = None
    reasons: list[str] = []
    if args.template:
        chosen = find_entry(index, args.template)
        if chosen is None:
            print(f"--template {args.template!r}: not found in index", file=sys.stderr)
            return 1
        reasons = [f"explicitly requested via --template {args.template!r}"]
    else:
        if not ranked:
            print("no candidates", file=sys.stderr)
            return 1
        if not ranked[0].recommended:
            # No close template — signal the procedural fallback.
            if args.json:
                print(json.dumps({
                    "query": query,
                    "recommended": None,
                    "fallback": "generate-drawio.py",
                    "threshold": _S.RECOMMEND_THRESHOLD,
                    "candidates": [
                        {"id": c.id, "score": c.score, "reasons": c.reasons[:3]}
                        for c in ranked],
                }, indent=2, ensure_ascii=False))
            else:
                print(f"query: {query}")
                print(f"best score {ranked[0].score} < threshold "
                      f"{_S.RECOMMEND_THRESHOLD} ({ranked[0].id}).")
                print("no close template — use the procedural engine "
                      "(generate-drawio.py).")
            return EXIT_NO_TEMPLATE
        chosen = find_entry(index, ranked[0].id)
        reasons = ranked[0].reasons

    assert chosen is not None
    src = (args.templates_dir / chosen["file"]).resolve()
    if not src.exists():
        print(f"{src}: template file missing", file=sys.stderr)
        return 1

    add_or_relabel, swap_or_remove = relabel_checklist(chosen, query)
    alternates = [c for c in ranked if c.id != chosen["id"]][: args.top]

    if args.dry_run or args.out is None:
        if args.json:
            print(json.dumps({
                "query": query,
                "chosen": {"id": chosen["id"], "file": chosen["file"], "reasons": reasons},
                "alternates": [{"id": c.id, "score": c.score} for c in alternates],
                "relabel_add": add_or_relabel,
                "relabel_swap": swap_or_remove,
            }, indent=2, ensure_ascii=False))
        else:
            _print_report(query, chosen, reasons, alternates, add_or_relabel,
                          swap_or_remove, dest=None)
        return 0

    dest = args.out.resolve()
    if dest.exists() and not args.force:
        print(f"{dest}: already exists (use --force)", file=sys.stderr)
        return 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)

    if args.json:
        print(json.dumps({
            "query": query,
            "template": chosen["id"],
            "file": chosen["file"],
            "destination": str(dest),
            "reasons": reasons,
            "alternates": [{"id": c.id, "score": c.score} for c in alternates],
            "relabel_add": add_or_relabel,
            "relabel_swap": swap_or_remove,
        }, indent=2, ensure_ascii=False))
    else:
        print(f"scaffolded {dest} from {chosen['file']}\n")
        _print_report(query, chosen, reasons, alternates, add_or_relabel,
                      swap_or_remove, dest=dest)
    return 0


def _print_report(query, chosen, reasons, alternates, add_or_relabel,
                  swap_or_remove, dest) -> None:
    print(f"template used : {chosen['id']}  ({chosen['file']})")
    print(f"  family/level: {chosen.get('family')}/{chosen.get('level')}")
    print("  why         :")
    for r in reasons[:4]:
        print(f"    - {r}")
    if alternates:
        print("\nalternates (open one if the chosen family is wrong):")
        for i, c in enumerate(alternates, 1):
            print(f"  {i}. {c.score:6.1f}  {c.id}  [{c.family}/{c.level}]")
    print("\nrelabel checklist for this request:")
    if add_or_relabel:
        print("  add / relabel a cell to cover (in request, not in template):")
        print("    " + ", ".join(add_or_relabel[:20]))
    if swap_or_remove:
        print("  template services to relabel to yours or remove (not in request):")
        for s in swap_or_remove[:12]:
            print(f"    - {s}")
    if not add_or_relabel and not swap_or_remove:
        print("  (request already well-covered by the template's tokens)")
    if dest is not None:
        print("\nnext steps:")
        print(f"  1. relabel surgically (preserves geometry/style/ids):")
        print(f"       python3 scripts/relabel.py {dest.name} \\")
        print(f"         --replace \"Old Service=New Service\" --set <cellId>=\"New label\"")
        print(f"  2. swap icons via the sap-icons-resolve skill as needed.")
        print(f"  3. gate (same as the procedural path):")
        print(f"       python3 scripts/validate-drawio.py {dest.name} --strict")
        print(f"       python3 scripts/check-composition.py {dest.name}")
        print(f"       python3 scripts/score-diagram.py --corpus assets/templates {dest.name} --min-score 82")


if __name__ == "__main__":
    raise SystemExit(main())
