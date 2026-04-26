#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""CI check: verify shape-index.json structure and counts."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    index_path = root / "assets" / "shape-index.json"
    schema_path = root / "assets" / "shape-index.schema.json"

    if not index_path.exists():
        print(f"FAIL: missing {index_path}")
        return 1
    if not schema_path.exists():
        print(f"FAIL: missing {schema_path}")
        return 1

    index = json.loads(index_path.read_text(encoding="utf-8"))

    for required in ("meta", "sets", "services"):
        if required not in index:
            print(f"FAIL: index missing '{required}' top-level key")
            return 1

    services = index["services"]
    declared = index["meta"].get("totalServices")
    if declared != len(services):
        print(f"FAIL: meta.totalServices={declared} but len(services)={len(services)}")
        return 1

    for idx, service in enumerate(services):
        for key in ("name", "set", "size"):
            if key not in service:
                print(f"FAIL: services[{idx}] missing '{key}'")
                return 1

    print(
        f"OK: shape-index.json — {len(services)} services across "
        f"{len(index['sets'])} sets (commit {index['meta'].get('sourceCommit', '?')})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
