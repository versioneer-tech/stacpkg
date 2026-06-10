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


def test_parse_usecase_shell_extracts_metadata_markdown_and_commands(tmp_path: Path) -> None:
    source = tmp_path / "small-flow.sh"
    source.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "# title: Small Flow",
                "# test: test_small_flow",
                "",
                "# ## Prepare data",
                "setup-openaerialmap-items source.items.parquet --item-count 3",
                "",
                "# ## Build package",
                "stacpkg items from-parquet source.items.parquet \\",
                "  | stacpkg build small.pkg/",
                "assert-package-items small.pkg 3",
                "",
            ]
        ),
        encoding="utf-8",
    )

    usecase = GENERATOR.parse_usecase_shell(source)

    assert usecase.slug == "small-flow"
    assert usecase.title == "Small Flow"
    assert usecase.test_name == "test_small_flow"
    assert usecase.generate_test
    assert [type(event).__name__ for event in usecase.events] == [
        "Markdown",
        "Command",
        "Markdown",
        "Command",
        "Command",
    ]


def test_markdown_from_usecase_renders_shell_source(tmp_path: Path) -> None:
    source = tmp_path / "small-flow.sh"
    source.write_text(
        "\n".join(
            [
                "# title: Small Flow",
                "# ## Build package",
                "stacpkg items from-parquet source.items.parquet \\",
                "  | stacpkg build small.pkg/",
                "",
            ]
        ),
        encoding="utf-8",
    )
    usecase = GENERATOR.parse_usecase_shell(source)

    markdown = GENERATOR.markdown_from_usecase(usecase)

    assert markdown.startswith("# Small Flow\n")
    assert "Generated from" in markdown
    assert "## Build package" in markdown
    assert "```bash\nstacpkg items from-parquet source.items.parquet \\\n" in markdown
    assert "| stacpkg build small.pkg/" in markdown


def test_python_test_from_usecase_maps_supported_cli_to_library_calls(tmp_path: Path) -> None:
    source = tmp_path / "openaerialmap-provider-search-package.sh"
    source.write_text(
        "\n".join(
            [
                "# title: OpenAerialMap Provider Package with Asset Bytes",
                "# test: test_openaerialmap_provider_package",
                "setup-openaerialmap-items openaerialmap-2025.items.parquet --item-count 3",
                "stacpkg items from-parquet openaerialmap-2025.items.parquet --providers ODM \\",
                "  | stacpkg items to-parquet openaerialmap-provider.items.parquet",
                "stacpkg items from-parquet openaerialmap-provider.items.parquet \\",
                "  | stacpkg asset-lock derive --no-probe-metadata \\",
                "  | stacpkg asset-lock to-parquet openaerialmap-provider.assets.lock.parquet",
                "stacpkg items from-parquet openaerialmap-provider.items.parquet \\",
                "  | stacpkg build openaerialmap-provider.pkg \\",
                "  --asset-lock openaerialmap-provider.assets.lock.arrow \\",
                "  --include-assets",
                "assert-package-items openaerialmap-provider.pkg 1",
                "",
            ]
        ),
        encoding="utf-8",
    )
    usecase = GENERATOR.parse_usecase_shell(source)

    python = GENERATOR.python_test_from_usecase(usecase)

    assert "def test_openaerialmap_provider_package(tmp_path: Path) -> None:" in python
    assert "@pytest.mark.usecase" in python
    assert "localized_openaerialmap_items(tmp_path, item_count=3)" in python
    assert " = filter_items(" in python
    assert 'providers={"ODM"}' in python
    assert "write_items_geoparquet(" in python
    assert "derive_asset_lock(" in python
    assert "probe_metadata=False" in python
    assert "build_package(" in python
    assert '_p(tmp_path, "openaerialmap-provider.assets.lock.parquet")' in python
    assert "asset_lock=asset_lock_" in python
    assert "include_assets=True" in python
    assert '_assert_parquet_rows(tmp_path, "openaerialmap-provider.pkg/items.parquet", 1)' in python


def test_generate_usecase_artifacts_writes_markdown_and_python_test(tmp_path: Path) -> None:
    source_dir = tmp_path / "docs" / "usecases"
    markdown_dir = tmp_path / "docs" / "generated" / "usecases"
    test_dir = tmp_path / "tests" / "usecases"
    source_dir.mkdir(parents=True)
    source = source_dir / "small-flow.sh"
    source.write_text(
        "\n".join(
            [
                "# title: Small Flow",
                "# test: test_small_flow",
                "setup-openaerialmap-items source.items.parquet --item-count 3",
                "stacpkg items from-parquet source.items.parquet \\",
                "  | stacpkg build small.pkg/",
                "assert-package-items small.pkg 3",
                "",
            ]
        ),
        encoding="utf-8",
    )

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
    source_dir.mkdir(parents=True)
    source = source_dir / "small-flow.sh"
    source.write_text(
        "\n".join(
            [
                "# title: Small Flow",
                "setup-openaerialmap-items source.items.parquet --item-count 3",
                "",
            ]
        ),
        encoding="utf-8",
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
    source_dir.mkdir(parents=True)
    source = source_dir / "external-flow.sh"
    source.write_text(
        "\n".join(
            [
                "# title: External Flow",
                "# test: none",
                "# ## Fetch data",
                "curl -fsS https://example.test/data.json --output data.json",
                "",
            ]
        ),
        encoding="utf-8",
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


def test_generate_usecase_tests_removes_stale_generated_tests(tmp_path: Path) -> None:
    source_dir = tmp_path / "docs" / "usecases"
    test_dir = tmp_path / "tests" / "usecases"
    source_dir.mkdir(parents=True)
    test_dir.mkdir(parents=True)
    (test_dir / "test_generated_stale.py").write_text("# stale\n", encoding="utf-8")
    source = source_dir / "small-flow.sh"
    source.write_text(
        "\n".join(
            [
                "# title: Small Flow",
                "setup-openaerialmap-items source.items.parquet --item-count 3",
                "",
            ]
        ),
        encoding="utf-8",
    )

    tests = GENERATOR.generate_usecase_tests(source_dir=source_dir, test_dir=test_dir)

    assert tests == [test_dir / "test_generated_small_flow.py"]
    assert (test_dir / "test_generated_small_flow.py").exists()
    assert not (test_dir / "test_generated_stale.py").exists()


def test_unsupported_commands_fail_loudly(tmp_path: Path) -> None:
    source = tmp_path / "unsupported.sh"
    source.write_text("curl -fsS https://example.test/data.json\n", encoding="utf-8")
    usecase = GENERATOR.parse_usecase_shell(source)

    with pytest.raises(GENERATOR.UnsupportedCommand):
        GENERATOR.python_test_from_usecase(usecase)
