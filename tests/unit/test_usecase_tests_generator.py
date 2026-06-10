# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_generator() -> object:
    module_path = Path(__file__).parents[2] / "scripts" / "generate_usecase_tests.py"
    spec = importlib.util.spec_from_file_location("generate_usecase_tests", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GENERATOR = _load_generator()

SMALL_FLOW = [
    "# title: Small Flow",
    "# test: test_small_flow",
    "# test-setup: openaerialmap-items source.items.parquet --item-count 3",
    "# ## Build package",
    "stacpkg items from-parquet source.items.parquet \\",
    "  | stacpkg build small.pkg/",
    "# test-assert: package-items small.pkg 3",
]


def _source(root: Path, name: str, lines: list[str]) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([*lines, ""]), encoding="utf-8")
    return path


def _parse(root: Path, lines: list[str], *, name: str = "small-flow.sh"):
    return GENERATOR.parse_usecase_shell(_source(root, name, lines))


def _event_types(usecase: object) -> list[str]:
    return [type(event).__name__ for event in usecase.events]


def _has_test_assertion(usecase: object) -> bool:
    return any(
        type(event).__name__ == "TestCommand" and event.text.startswith("assert-")
        for event in usecase.events
    )


def test_parse_usecase_shell_extracts_metadata_markdown_and_commands(tmp_path: Path) -> None:
    usecase = _parse(
        tmp_path,
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            *SMALL_FLOW,
        ],
    )

    assert (usecase.slug, usecase.title, usecase.test_name, usecase.generate_test) == (
        "small-flow",
        "Small Flow",
        "test_small_flow",
        True,
    )
    assert _event_types(usecase) == [
        "TestCommand",
        "Markdown",
        "Command",
        "TestCommand",
    ]


def test_markdown_from_usecase_renders_shell_source_without_test_directives(
    tmp_path: Path,
) -> None:
    markdown = GENERATOR.markdown_from_usecase(
        _parse(
            tmp_path,
            [
                "# title: Small Flow",
                "# test: test_small_flow",
                "# test-setup: openaerialmap-items source.items.parquet --item-count 3",
                "# ## Build package",
                "# Keep neighboring comment lines in the same Markdown block.",
                "",
                "# ## Run command",
                "stacpkg items from-parquet source.items.parquet \\",
                "  | stacpkg build small.pkg/",
                "# test-assert: package-items small.pkg 3",
            ],
        )
    )

    assert markdown.startswith("# Small Flow\n")
    assert "Generated from" in markdown
    assert "## Build package" in markdown
    assert "## Build package\nKeep neighboring comment lines" in markdown
    assert "## Build package\n\nKeep neighboring comment lines" not in markdown
    assert "same Markdown block.\n\n## Run command" in markdown
    assert "```bash\nstacpkg items from-parquet source.items.parquet \\\n" in markdown
    assert "| stacpkg build small.pkg/" in markdown
    assert "test-setup" not in markdown
    assert "test-assert" not in markdown
    assert "assert-package-items" not in markdown


def test_python_test_from_usecase_runs_shell_commands_and_assertions(tmp_path: Path) -> None:
    usecase = _parse(
        tmp_path,
        [
            "# title: OpenAerialMap Provider Package with Asset Bytes",
            "# test: test_openaerialmap_provider_package",
            "# test-setup: openaerialmap-items openaerialmap-2025.items.parquet --item-count 3",
            "stacpkg items from-parquet openaerialmap-2025.items.parquet --providers ODM \\",
            "  | stacpkg items to-parquet openaerialmap-provider.items.parquet",
            "stacpkg items from-parquet openaerialmap-provider.items.parquet \\",
            "  | stacpkg asset-lock derive --no-probe-metadata \\",
            "  | stacpkg asset-lock to-parquet openaerialmap-provider.assets.lock.parquet",
            "stacpkg items from-parquet openaerialmap-provider.items.parquet \\",
            "  | stacpkg build openaerialmap-provider.pkg \\",
            "  --asset-lock <(stacpkg asset-lock from-parquet openaerialmap-provider.assets.lock.parquet) \\",
            "  --include-assets",
            "# test-assert: package-items openaerialmap-provider.pkg 1",
            "# test-assert: asset-lock-store openaerialmap-provider.assets.lock.parquet file",
        ],
        name="openaerialmap-provider-search-package.sh",
    )

    python = GENERATOR.python_test_from_usecase(usecase)

    assert "def test_openaerialmap_provider_package(" in python
    assert "@pytest.mark.usecase" in python
    assert "shell = UsecaseShell(tmp_path)" in python
    assert (
        'setup_openaerialmap_items(tmp_path, "openaerialmap-2025.items.parquet", item_count=3)'
        in python
    )
    assert "stacpkg items from-parquet openaerialmap-2025.items.parquet --providers ODM" in python
    assert "stacpkg asset-lock derive --no-probe-metadata" in python
    assert "stacpkg build openaerialmap-provider.pkg" in python
    assert 'assert_package_items(tmp_path, "openaerialmap-provider.pkg", 1)' in python
    assert (
        'assert_asset_lock_store(tmp_path, "openaerialmap-provider.assets.lock.parquet", "file")'
        in python
    )
    assert "filter_items(" not in python
    assert "build_package(" not in python


def test_generate_usecase_artifacts_writes_markdown_and_python_test(tmp_path: Path) -> None:
    source_dir = tmp_path / "docs" / "usecases"
    markdown_dir = tmp_path / "docs" / "generated" / "usecases"
    test_dir = tmp_path / "tests" / "usecases"
    source = _source(source_dir, "small-flow.sh", SMALL_FLOW)

    artifacts = GENERATOR.generate_usecase_artifacts(
        source_dir=source_dir,
        markdown_dir=markdown_dir,
        test_dir=test_dir,
    )

    assert artifacts == [
        GENERATOR.GeneratedArtifact(
            source=source,
            markdown=markdown_dir / "small-flow.md",
            test=test_dir / "test_generated_small_flow.py",
        )
    ]
    assert (markdown_dir / "index.md").exists()
    assert (markdown_dir / "small-flow.md").exists()
    assert (test_dir / "test_generated_small_flow.py").exists()


def test_check_mode_does_not_require_generated_files(tmp_path: Path) -> None:
    source_dir = tmp_path / "docs" / "usecases"
    markdown_dir = tmp_path / "docs" / "generated" / "usecases"
    test_dir = tmp_path / "tests" / "usecases"
    _source(
        source_dir,
        "small-flow.sh",
        [
            "# title: Small Flow",
            "# test-setup: openaerialmap-items source.items.parquet --item-count 3",
            "# test-assert: file-exists source.items.parquet",
        ],
    )

    artifacts = GENERATOR.generate_usecase_artifacts(
        source_dir=source_dir,
        markdown_dir=markdown_dir,
        test_dir=test_dir,
        check=True,
        write_tests=False,
    )

    assert artifacts[0].test == test_dir / "test_generated_small_flow.py"
    assert not (markdown_dir / "index.md").exists()
    assert not (markdown_dir / "small-flow.md").exists()
    assert not artifacts[0].test.exists()


def test_docs_only_usecase_can_use_unsupported_cli_commands(tmp_path: Path) -> None:
    source_dir = tmp_path / "docs" / "usecases"
    markdown_dir = tmp_path / "docs" / "generated" / "usecases"
    test_dir = tmp_path / "tests" / "usecases"
    source = _source(
        source_dir,
        "external-flow.sh",
        [
            "# title: External Flow",
            "# test: none",
            "# ## Fetch data",
            "curl -fsS https://example.test/data.json --output data.json",
        ],
    )

    usecase = GENERATOR.parse_usecase_shell(source)
    artifacts = GENERATOR.generate_usecase_artifacts(
        source_dir=source_dir,
        markdown_dir=markdown_dir,
        test_dir=test_dir,
    )
    tests = GENERATOR.generate_usecase_tests(source_dir=source_dir, test_dir=test_dir)

    assert not usecase.generate_test
    assert artifacts[0].markdown == markdown_dir / "external-flow.md"
    assert "curl -fsS" in (markdown_dir / "external-flow.md").read_text(encoding="utf-8")
    assert tests == []
    assert not (test_dir / "test_generated_external_flow.py").exists()


def test_testable_usecase_requires_an_assertion(tmp_path: Path) -> None:
    usecase = _parse(
        tmp_path,
        [
            "# title: Missing Assertion",
            "# test-setup: openaerialmap-items source.items.parquet --item-count 3",
            "stacpkg items from-parquet source.items.parquet \\",
            "  | stacpkg build small.pkg/",
        ],
        name="missing-assertion.sh",
    )

    with pytest.raises(GENERATOR.UnsupportedCommand, match="# test-assert"):
        GENERATOR.python_test_from_usecase(usecase)


def test_repository_usecase_sources_all_generate_python_tests() -> None:
    source_dir = Path(__file__).parents[2] / "docs" / "usecases"
    usecases = [GENERATOR.parse_usecase_shell(source) for source in sorted(source_dir.glob("*.sh"))]

    assert {usecase.slug for usecase in usecases} == {
        "asset-handover-to-recipient-storage",
        "cdse-stac-to-geoparquet",
        "hls2-vienna-s3-package",
        "openaerialmap-package-handover-to-recipient-storage",
        "openaerialmap-provider-search-package",
        "openaerialmap-s3-alternate-package",
        "reproducible-data-inputs",
    }
    assert [usecase.slug for usecase in usecases if not usecase.generate_test] == []
    assert [usecase.slug for usecase in usecases if not _has_test_assertion(usecase)] == []

    for usecase in usecases:
        python = GENERATOR.python_test_from_usecase(usecase)
        assert f"def {usecase.test_name}(" in python


def test_generate_usecase_tests_removes_stale_generated_tests(tmp_path: Path) -> None:
    source_dir = tmp_path / "docs" / "usecases"
    test_dir = tmp_path / "tests" / "usecases"
    test_dir.mkdir(parents=True)
    (test_dir / "test_generated_stale.py").write_text("# stale\n", encoding="utf-8")
    _source(
        source_dir,
        "small-flow.sh",
        [
            "# title: Small Flow",
            "# test-setup: openaerialmap-items source.items.parquet --item-count 3",
            "# test-assert: file-exists source.items.parquet",
        ],
    )

    tests = GENERATOR.generate_usecase_tests(source_dir=source_dir, test_dir=test_dir)

    assert tests == [test_dir / "test_generated_small_flow.py"]
    assert (test_dir / "test_generated_small_flow.py").exists()
    assert not (test_dir / "test_generated_stale.py").exists()


def test_unsupported_test_directives_fail_loudly(tmp_path: Path) -> None:
    usecase = _parse(
        tmp_path,
        [
            "# title: Unsupported",
            "stacpkg inspect package/",
            "# test-assert: unsupported-check package/",
        ],
        name="unsupported.sh",
    )

    with pytest.raises(GENERATOR.UnsupportedCommand, match="unsupported test directive"):
        GENERATOR.python_test_from_usecase(usecase)
