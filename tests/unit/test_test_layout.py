# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path


TESTS_ROOT = Path(__file__).resolve().parents[1]


def _source_dirs(path: Path) -> set[str]:
    return {
        child.name for child in path.iterdir() if child.is_dir() and child.name != "__pycache__"
    }


def test_tests_tree_has_documented_top_level_source_folders() -> None:
    assert _source_dirs(TESTS_ROOT) == {"data", "e2e", "integration", "setup", "unit"}


def test_tests_tree_has_single_readme_at_root() -> None:
    readmes = sorted(
        path.relative_to(TESTS_ROOT).as_posix() for path in TESTS_ROOT.rglob("README.md")
    )

    assert readmes == ["README.md"]


def test_e2e_tests_are_flattened_and_named_by_kind() -> None:
    assert _source_dirs(TESTS_ROOT / "e2e") == set()

    e2e_tests = {path.name for path in (TESTS_ROOT / "e2e").glob("test_*.py")}
    assert any(name.startswith("test_usecase_") for name in e2e_tests)
    assert any(name.startswith("test_performance_") for name in e2e_tests)
    assert all(name.startswith(("test_usecase_", "test_performance_")) for name in e2e_tests)


def test_setup_contains_kind_service_manifests() -> None:
    setup_files = {path.name for path in (TESTS_ROOT / "setup").glob("*.yaml")}

    assert setup_files == {"registry.yaml", "s3-stores.yaml"}
