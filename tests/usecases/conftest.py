# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
from pathlib import Path


def pytest_configure() -> None:
    generator = _load_generator()
    generator.generate_usecase_tests(test_dir=Path(__file__).parent)


def _load_generator() -> object:
    module_path = Path(__file__).parents[2] / "scripts" / "generate_usecase_tests.py"
    spec = importlib.util.spec_from_file_location("generate_usecase_tests", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
