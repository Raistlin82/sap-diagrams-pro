#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""CI check: verify each SKILL.md has the required frontmatter keys."""
from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIRED = {"name", "description", "version"}


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    skill_files = sorted((root / "skills").glob("*/SKILL.md"))
    if not skill_files:
        print("FAIL: no SKILL.md files found under skills/")
        return 1

    failed = 0
    for path in skill_files:
        content = path.read_text(encoding="utf-8")
        m = re.match(r"---\n(.*?)\n---\n", content, re.DOTALL)
        if not m:
            print(f"FAIL: {path.relative_to(root)} — missing frontmatter")
            failed += 1
            continue
        keys = set(re.findall(r"^([\w-]+):", m.group(1), re.MULTILINE))
        missing = REQUIRED - keys
        if missing:
            print(
                f"FAIL: {path.relative_to(root)} — missing required keys: "
                f"{sorted(missing)}"
            )
            failed += 1
            continue
        print(f"OK: {path.relative_to(root)} — keys = {sorted(keys)}")

    if failed:
        print(f"FAIL: {failed} skill(s) failed frontmatter check")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
