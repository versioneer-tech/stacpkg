# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import textwrap
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DIR = REPO_ROOT / "tests" / "e2e"
DEFAULT_SOURCE_PATTERN = "test_usecase_*.py"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "usecases"

_CLI_RE = re.compile(r"^\s*#\s*CLI:\s?(.*)$")
_COMMENT_RE = re.compile(r"^\s*#\s?(.*)$")
_NOTEBOOK_RE = re.compile(r"^\s*#\s*NOTEBOOK:\s?(.*)$")
_NOTEBOOK_OUTPUT_RE = re.compile(r"^\s*#\s*NOTEBOOK_OUTPUT:\s?(.*)$")
_NOTEBOOK_TABLE_RE = re.compile(r"^\s*#\s*NOTEBOOK_TABLE:\s?(.*)$")


def _source_lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def _cell_source(text: str) -> list[str]:
    if not text:
        return []
    return text.splitlines(keepends=True)


def _markdown_cell(text: str) -> dict[str, Any]:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": _cell_source(text.rstrip() + "\n"),
    }


def _code_cell(text: str, *, tags: list[str] | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if tags:
        metadata["tags"] = tags
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": metadata,
        "outputs": [],
        "source": _cell_source(text.rstrip() + "\n"),
    }


def _cell_key(cell: dict[str, Any]) -> tuple[str, str]:
    return (str(cell.get("cell_type")), "".join(str(line) for line in cell.get("source", [])))


def _preserve_existing_outputs(
    notebook: dict[str, Any],
    *,
    existing: dict[str, Any],
) -> dict[str, Any]:
    existing_outputs: defaultdict[tuple[str, str], deque[dict[str, Any]]] = defaultdict(deque)
    for cell in existing.get("cells", []):
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        outputs = cell.get("outputs")
        if outputs:
            existing_outputs[_cell_key(cell)].append(
                {
                    "execution_count": cell.get("execution_count"),
                    "outputs": outputs,
                }
            )

    for cell in notebook.get("cells", []):
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        preserved = existing_outputs[_cell_key(cell)]
        if preserved:
            output = preserved.popleft()
            cell["execution_count"] = output.get("execution_count")
            cell["outputs"] = output.get("outputs", [])
    return notebook


def _load_notebook(path: Path) -> dict[str, Any] | None:
    try:
        notebook = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(notebook, dict):
        return None
    return notebook


def _write_if_changed(path: Path, text: str) -> None:
    try:
        if path.read_text(encoding="utf-8") == text:
            return
    except OSError:
        pass
    path.write_text(text, encoding="utf-8")


def _title_from_path(path: Path) -> str:
    name = path.stem
    if name.startswith("test_"):
        name = name.removeprefix("test_")
    if name.startswith("usecase_"):
        name = name.removeprefix("usecase_")
    return name.replace("_", " ").title()


def _slug_from_path(path: Path) -> str:
    name = path.stem
    if name.startswith("test_"):
        name = name.removeprefix("test_")
    if name.startswith("usecase_"):
        name = name.removeprefix("usecase_")
    return name.replace("_", "-")


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _test_functions(tree: ast.Module) -> list[ast.FunctionDef]:
    return [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    ]


def _first_decorator_or_function_line(function: ast.FunctionDef) -> int:
    lines = [function.lineno]
    lines.extend(decorator.lineno for decorator in function.decorator_list)
    return min(lines)


def _clean_preamble(source: str) -> str:
    lines = _source_lines(source)
    while lines and (
        lines[0].startswith("# Copyright") or lines[0].startswith("# SPDX") or not lines[0].strip()
    ):
        lines.pop(0)
    cleaned = []
    for line in lines:
        if line.strip() == "from __future__ import annotations":
            continue
        if line.strip() == "import pytest":
            continue
        cleaned.append(line)
    return "".join(cleaned).strip()


def _module_preamble(path: Path, lines: list[str], functions: list[ast.FunctionDef]) -> str:
    if not functions:
        return ""
    start_line = min(_first_decorator_or_function_line(function) for function in functions)
    preamble = _clean_preamble("".join(lines[: start_line - 1]))
    rel_path = _display_path(path)
    source_file = f'from pathlib import Path\n__file__ = str(Path("{rel_path}").resolve())'
    if preamble:
        return f"{source_file}\n\n{preamble}"
    return source_file


def _notebook_runtime_setup() -> str:
    return textwrap.dedent(
        """
        import logging
        from collections.abc import Mapping

        from IPython.display import Markdown, display

        logging.basicConfig(
            level=logging.INFO,
            format="%(levelname)s %(name)s: %(message)s",
            force=True,
        )


        def _stacpkg_cell(value: object) -> str:
            text = str(value)
            text = text.replace("\\n", " ").replace("|", "\\\\|")
            if len(text) > 96:
                return f"{text[:93]}..."
            return text


        def _stacpkg_rows(data: object) -> list[object]:
            if hasattr(data, "to_pylist"):
                return list(data.to_pylist())
            if isinstance(data, Mapping):
                return [data]
            return list(data)


        def _stacpkg_show_table(
            data: object,
            *,
            columns: list[str] | None = None,
            limit: int = 10,
        ) -> None:
            rows = _stacpkg_rows(data)
            if columns is None:
                columns = []
                for row in rows:
                    if isinstance(row, Mapping):
                        for key in row:
                            if key not in columns:
                                columns.append(str(key))
                    elif not columns:
                        columns.append("value")
            lines = [
                "| " + " | ".join(columns) + " |",
                "| " + " | ".join("---" for _ in columns) + " |",
            ]
            for row in rows[:limit]:
                if isinstance(row, Mapping):
                    values = [_stacpkg_cell(row.get(column)) for column in columns]
                else:
                    values = [_stacpkg_cell(row)]
                lines.append("| " + " | ".join(values) + " |")
            if len(rows) > limit:
                lines.append("")
                lines.append(f"Showing {limit} of {len(rows)} rows.")
            display(Markdown("\\n".join(lines)))
        """
    ).strip()


def _function_body(lines: list[str], function: ast.FunctionDef) -> str:
    if not function.body:
        return ""
    first_statement = function.body[0].lineno - 1
    body_start = first_statement
    for index in range(function.lineno - 1, first_statement):
        if lines[index].rstrip().endswith(":"):
            body_start = index + 1
            break
    body = "".join(lines[body_start : function.end_lineno])
    return textwrap.dedent(body).strip("\n")


def _collect_marker_block(
    lines: list[str],
    start: int,
    pattern: re.Pattern[str],
) -> tuple[list[str], int]:
    collected: list[str] = []
    index = start
    while index < len(lines):
        match = pattern.match(lines[index])
        if match is None:
            break
        collected.append(match.group(1).rstrip())
        index += 1
    return collected, index


def _collect_cli(lines: list[str], start: int) -> tuple[list[str], int]:
    match = _CLI_RE.match(lines[start])
    if match is None:
        return [], start

    command_lines = [match.group(1).rstrip()]
    index = start + 1
    while index < len(lines):
        if _NOTEBOOK_RE.match(lines[index]) or _NOTEBOOK_OUTPUT_RE.match(lines[index]):
            break
        if _CLI_RE.match(lines[index]):
            break
        comment = _COMMENT_RE.match(lines[index])
        if comment is None:
            break
        text = comment.group(1)
        if text and not text.startswith(" "):
            break
        command_lines.append(text.rstrip())
        index += 1
    return command_lines, index


def _table_code(marker: str) -> str:
    expression, separator, column_text = marker.partition("|")
    expression = expression.strip()
    if not expression:
        raise ValueError("NOTEBOOK_TABLE marker must include a Python expression")
    if not separator:
        return f"_stacpkg_show_table({expression})"
    columns = [column.strip() for column in column_text.split(",") if column.strip()]
    return f"_stacpkg_show_table({expression}, columns={columns!r})"


def _execute_notebook(
    notebook: dict[str, Any],
    *,
    timeout: int,
    allow_errors: bool,
) -> dict[str, Any]:
    import nbformat
    from nbclient import NotebookClient

    execution_notebook = json.loads(json.dumps(notebook))
    for cell in execution_notebook.get("cells", []):
        if isinstance(cell, dict) and isinstance(cell.get("source"), list):
            cell["source"] = "".join(str(line) for line in cell["source"])
    nb = nbformat.from_dict(execution_notebook)
    client = NotebookClient(
        nb,
        timeout=timeout,
        allow_errors=allow_errors,
        kernel_name="python3",
        resources={"metadata": {"path": str(REPO_ROOT)}},
    )
    client.execute()
    executed = json.loads(nbformat.writes(nb))
    for cell in executed.get("cells", []):
        if isinstance(cell, dict) and isinstance(cell.get("source"), str):
            cell["source"] = _cell_source(cell["source"])
    return executed


def _flush_code(cells: list[dict[str, Any]], code_lines: list[str]) -> None:
    text = "".join(code_lines).strip()
    if text:
        cells.append(_code_cell(text))
    code_lines.clear()


def _body_cells(body: str) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    code_lines: list[str] = []
    lines = _source_lines(body)
    index = 0
    while index < len(lines):
        line = lines[index]
        if _NOTEBOOK_RE.match(line):
            _flush_code(cells, code_lines)
            markdown, index = _collect_marker_block(lines, index, _NOTEBOOK_RE)
            cells.append(_markdown_cell("\n".join(markdown)))
            continue
        if _NOTEBOOK_OUTPUT_RE.match(line):
            _flush_code(cells, code_lines)
            output, index = _collect_marker_block(lines, index, _NOTEBOOK_OUTPUT_RE)
            cells.append(
                _markdown_cell(
                    "Representative output:\n\n```text\n" + "\n".join(output).rstrip() + "\n```"
                )
            )
            continue
        table = _NOTEBOOK_TABLE_RE.match(line)
        if table:
            _flush_code(cells, code_lines)
            cells.append(_code_cell(_table_code(table.group(1))))
            index += 1
            continue
        if _CLI_RE.match(line):
            _flush_code(cells, code_lines)
            command, index = _collect_cli(lines, index)
            cells.append(
                _markdown_cell("CLI equivalent:\n\n```bash\n" + "\n".join(command) + "\n```")
            )
            continue
        code_lines.append(line)
        index += 1
    _flush_code(cells, code_lines)
    return cells


def _fixture_setup(function: ast.FunctionDef) -> str:
    parameters = [argument.arg for argument in function.args.args]
    lines: list[str] = []
    if "tmp_path" in parameters:
        lines.extend(
            [
                "from pathlib import Path",
                "from tempfile import TemporaryDirectory",
                "",
                "# pytest supplies tmp_path in the test; notebooks allocate one explicitly.",
                "_stacpkg_tmp_context = TemporaryDirectory()",
                "tmp_path = Path(_stacpkg_tmp_context.name)",
                "tmp_path",
            ]
        )
    unsupported = sorted(set(parameters) - {"tmp_path"})
    if unsupported:
        lines.extend(
            [
                "",
                f"# Unsupported pytest fixtures for notebook execution: {', '.join(unsupported)}",
            ]
        )
    return "\n".join(lines)


def notebook_from_pytest(path: Path) -> dict[str, Any]:
    source = path.read_text(encoding="utf-8")
    lines = _source_lines(source)
    tree = ast.parse(source, filename=str(path))
    functions = _test_functions(tree)
    if not functions:
        raise ValueError(f"no pytest use case functions found in {path}")

    title = _title_from_path(path)
    source_path = _display_path(path)
    cells: list[dict[str, Any]] = [
        _markdown_cell(f"# {title}\n\nGenerated from `{source_path}`."),
    ]
    preamble = _module_preamble(path, lines, functions)
    if preamble:
        cells.append(_code_cell(preamble, tags=["setup"]))
    cells.append(_code_cell(_notebook_runtime_setup(), tags=["setup"]))

    for function in functions:
        if len(functions) > 1:
            cells.append(_markdown_cell(f"## {function.name.replace('_', ' ').title()}"))
        setup = _fixture_setup(function)
        if setup:
            cells.append(_code_cell(setup, tags=["setup"]))
        cells.extend(_body_cells(_function_body(lines, function)))
    for index, cell in enumerate(cells, start=1):
        cell["id"] = f"stacpkg-{index:03d}"

    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {
                "name": "python",
                "pygments_lexer": "ipython3",
            },
            "stacpkg": {
                "generated_from": source_path,
                "generator": "scripts/generate_usecase_notebooks.py",
            },
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def generate_usecase_notebooks(
    *,
    source_dir: Path = DEFAULT_SOURCE_DIR,
    source_pattern: str | None = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    execute: bool = False,
    execute_ignore: set[str] | None = None,
    execute_timeout: int = 1200,
    allow_errors: bool = False,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if source_pattern is None:
        source_pattern = DEFAULT_SOURCE_PATTERN if source_dir == DEFAULT_SOURCE_DIR else "test_*.py"
    sources = sorted(source_dir.glob(source_pattern))
    expected_outputs = {output_dir / f"{_slug_from_path(path)}.ipynb" for path in sources}
    for stale in sorted(output_dir.glob("*.ipynb")):
        if stale not in expected_outputs and _generated_by_this_script(stale):
            stale.unlink()

    outputs: list[Path] = []
    execute_ignore = execute_ignore or set()
    for path in sources:
        output = output_dir / f"{_slug_from_path(path)}.ipynb"
        notebook = notebook_from_pytest(path)
        existing = _load_notebook(output)
        if existing is not None:
            notebook = _preserve_existing_outputs(notebook, existing=existing)
        if execute and not _ignored(output, execute_ignore):
            notebook = _execute_notebook(
                notebook,
                timeout=execute_timeout,
                allow_errors=allow_errors,
            )
        _write_if_changed(output, json.dumps(notebook, indent=2, sort_keys=True) + "\n")
        outputs.append(output)
    return outputs


def _ignored(path: Path, patterns: set[str]) -> bool:
    names = {path.name, path.as_posix()}
    try:
        names.add(path.resolve().relative_to(REPO_ROOT).as_posix())
    except ValueError:
        pass
    return bool(names & patterns)


def _generated_by_this_script(path: Path) -> bool:
    try:
        notebook = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    metadata = notebook.get("metadata")
    if not isinstance(metadata, dict):
        return False
    stacpkg = metadata.get("stacpkg")
    if not isinstance(stacpkg, dict):
        return False
    return stacpkg.get("generator") == "scripts/generate_usecase_notebooks.py"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate Jupyter notebooks from e2e pytest use cases."
    )
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--source-pattern", default=DEFAULT_SOURCE_PATTERN)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--execute",
        action="store_true",
        default=os.environ.get("STACPKG_GENERATE_NOTEBOOK_OUTPUTS", "").lower()
        in {"1", "true", "yes", "on"},
        help="execute generated notebooks and save cell outputs",
    )
    parser.add_argument(
        "--execute-ignore",
        action="append",
        default=[],
        help="generated notebook path or filename to skip when --execute is used",
    )
    parser.add_argument("--execute-timeout", type=int, default=1200)
    parser.add_argument("--allow-errors", action="store_true")
    args = parser.parse_args()
    for output in generate_usecase_notebooks(
        source_dir=args.source_dir,
        source_pattern=args.source_pattern,
        output_dir=args.output_dir,
        execute=args.execute,
        execute_ignore=set(args.execute_ignore),
        execute_timeout=args.execute_timeout,
        allow_errors=args.allow_errors,
    ):
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
