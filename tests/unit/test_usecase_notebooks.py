# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _load_generator() -> object:
    module_path = Path(__file__).parents[2] / "scripts" / "generate_usecase_notebooks.py"
    spec = importlib.util.spec_from_file_location("generate_usecase_notebooks", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GENERATOR = _load_generator()


def _cell_text(cell: dict[str, object]) -> str:
    source = cell["source"]
    assert isinstance(source, list)
    return "".join(str(line) for line in source)


def test_notebook_from_pytest_splits_cli_and_output_markers(tmp_path: Path) -> None:
    source = tmp_path / "test_example_usecase.py"
    source.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "",
                "import pytest",
                "",
                "HELPER = 'value'",
                "",
                "def helper() -> str:",
                "    return HELPER",
                "",
                "@pytest.mark.e2e",
                "def test_example_usecase(tmp_path: Path) -> None:",
                "    # NOTEBOOK: ## Prepare data",
                "    path = tmp_path / 'result.txt'",
                "    # CLI: stacpkg items from-parquet source.items.parquet",
                "    #      | stacpkg build pkg/",
                "    #      stacpkg inspect pkg/",
                "    path.write_text(helper(), encoding='utf-8')",
                "    # NOTEBOOK_TABLE: [{'name': path.name, 'size': 5}] | name,size",
                "    # NOTEBOOK_OUTPUT: wrote result.txt",
                "    # NOTEBOOK_OUTPUT: package items: 1",
                "    assert path.read_text(encoding='utf-8') == 'value'",
                "",
            ]
        ),
        encoding="utf-8",
    )

    notebook = GENERATOR.notebook_from_pytest(source)
    cells = notebook["cells"]
    assert isinstance(cells, list)
    text = "\n".join(_cell_text(cell) for cell in cells)

    assert all("id" in cell for cell in cells)
    assert "Generated from" in _cell_text(cells[0])
    assert "import pytest" not in text
    assert "__file__" in text
    assert "logging.basicConfig(" in text
    assert "def _stacpkg_show_table(" in text
    assert "tmp_path = Path(_stacpkg_tmp_context.name)" in text
    assert "## Prepare data" in text
    assert "```bash\nstacpkg items from-parquet source.items.parquet\n" in text
    assert "| stacpkg build pkg/" in text
    assert "stacpkg build pkg/" in text
    assert "stacpkg inspect pkg/" in text
    assert "_stacpkg_show_table([{'name': path.name, 'size': 5}], columns=['name', 'size'])" in text
    assert "Representative output:" in text
    assert "wrote result.txt" in text
    assert "path.write_text(helper(), encoding='utf-8')" in text


def test_generate_usecase_notebooks_writes_ipynb_files(tmp_path: Path) -> None:
    source_dir = tmp_path / "usecases"
    output_dir = tmp_path / "notebooks"
    source_dir.mkdir()
    (source_dir / "test_small_flow.py").write_text(
        "\n".join(
            [
                "def test_small_flow() -> None:",
                "    # NOTEBOOK_OUTPUT: ok",
                "    assert True",
                "",
            ]
        ),
        encoding="utf-8",
    )

    outputs = GENERATOR.generate_usecase_notebooks(source_dir=source_dir, output_dir=output_dir)

    assert outputs == [output_dir / "small-flow.ipynb"]
    notebook = json.loads(outputs[0].read_text(encoding="utf-8"))
    assert notebook["nbformat"] == 4
    assert notebook["metadata"]["stacpkg"]["generated_from"].endswith("test_small_flow.py")


def test_generate_usecase_notebooks_can_filter_flattened_e2e_usecases(tmp_path: Path) -> None:
    source_dir = tmp_path / "e2e"
    output_dir = tmp_path / "notebooks"
    source_dir.mkdir()
    (source_dir / "test_usecase_small_flow.py").write_text(
        "\n".join(
            [
                "def test_small_flow() -> None:",
                "    # NOTEBOOK_OUTPUT: ok",
                "    assert True",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (source_dir / "test_performance_large_flow.py").write_text(
        "\n".join(
            [
                "def test_large_flow() -> None:",
                "    assert True",
                "",
            ]
        ),
        encoding="utf-8",
    )

    outputs = GENERATOR.generate_usecase_notebooks(
        source_dir=source_dir,
        source_pattern="test_usecase_*.py",
        output_dir=output_dir,
    )

    assert outputs == [output_dir / "small-flow.ipynb"]


def test_generate_usecase_notebooks_preserves_matching_cell_outputs(tmp_path: Path) -> None:
    source_dir = tmp_path / "usecases"
    output_dir = tmp_path / "notebooks"
    source_dir.mkdir()
    source = source_dir / "test_table_flow.py"
    source.write_text(
        "\n".join(
            [
                "def test_table_flow() -> None:",
                "    rows = [{'name': 'source.assets.lock.parquet', 'size': 42}]",
                "    # NOTEBOOK_TABLE: rows | name,size",
                "    assert rows[0]['size'] == 42",
                "",
            ]
        ),
        encoding="utf-8",
    )

    outputs = GENERATOR.generate_usecase_notebooks(source_dir=source_dir, output_dir=output_dir)
    notebook_path = outputs[0]
    notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
    table_cell = next(
        cell for cell in notebook["cells"] if "_stacpkg_show_table(rows" in _cell_text(cell)
    )
    table_cell["execution_count"] = 7
    table_cell["outputs"] = [
        {
            "data": {
                "text/markdown": "| name | size |\n| --- | --- |\n| source.assets.lock.parquet | 42 |"
            },
            "metadata": {},
            "output_type": "display_data",
        }
    ]
    notebook_path.write_text(
        json.dumps(notebook, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    outputs = GENERATOR.generate_usecase_notebooks(source_dir=source_dir, output_dir=output_dir)
    regenerated = json.loads(outputs[0].read_text(encoding="utf-8"))
    regenerated_table_cell = next(
        cell for cell in regenerated["cells"] if "_stacpkg_show_table(rows" in _cell_text(cell)
    )

    assert regenerated_table_cell["execution_count"] == 7
    assert regenerated_table_cell["outputs"][0]["data"]["text/markdown"].startswith(
        "| name | size |"
    )


def test_generate_usecase_notebooks_can_execute_table_cells(tmp_path: Path) -> None:
    pytest.importorskip("nbformat")
    pytest.importorskip("nbclient")
    pytest.importorskip("IPython")

    source_dir = tmp_path / "usecases"
    output_dir = tmp_path / "notebooks"
    source_dir.mkdir()
    (source_dir / "test_executed_flow.py").write_text(
        "\n".join(
            [
                "def test_executed_flow() -> None:",
                "    rows = [{'name': 'source.assets.lock.parquet', 'size': 42}]",
                "    # NOTEBOOK_TABLE: rows | name,size",
                "    assert rows[0]['name'] == 'source.assets.lock.parquet'",
                "",
            ]
        ),
        encoding="utf-8",
    )

    outputs = GENERATOR.generate_usecase_notebooks(
        source_dir=source_dir,
        output_dir=output_dir,
        execute=True,
    )

    notebook = json.loads(outputs[0].read_text(encoding="utf-8"))
    table_cell = next(
        cell for cell in notebook["cells"] if "_stacpkg_show_table(rows" in _cell_text(cell)
    )
    assert table_cell["outputs"]
    table_output = table_cell["outputs"][0]["data"]["text/markdown"]
    if isinstance(table_output, list):
        table_output = "".join(table_output)
    assert "| name | size |" in table_output
    assert "source.assets.lock.parquet" in table_output
