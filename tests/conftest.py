# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_script(name: str):
    """Import a scripts/ module even when its filename contains dashes.

    Tests must load scripts/ modules exclusively via load_script (never plain
    `import _zone_layout`) so a single mechanism owns module identity.
    """
    mod_name = name.replace("-", "_")
    # Memoize: re-executing a module would clobber sys.modules and break
    # dataclass identity (isinstance/get_type_hints) across test files.
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: modules using `from __future__ import annotations`
    # (e.g. generate-drawio.py) need sys.modules[mod.__module__] populated so
    # dataclasses can resolve string annotations (ClassVar/InitVar/KW_ONLY
    # lookups) during class creation. See the importlib docs' own recipe for
    # "Importing a source file directly".
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod
