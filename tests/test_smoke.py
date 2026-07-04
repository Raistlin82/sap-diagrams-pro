# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
from conftest import load_script


def test_zone_icon_size():
    zl = load_script("_zone_layout")
    assert zl.icon_size("L1") == 48
    assert zl.icon_size("L2") == 32


def test_generator_imports():
    gen = load_script("generate-drawio")
    assert hasattr(gen, "emit")
