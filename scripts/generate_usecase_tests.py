# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import difflib
import json
import re
import shlex
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DIR = REPO_ROOT / "docs" / "usecases"
DEFAULT_SOURCE_PATTERN = "*.sh"
DEFAULT_MARKDOWN_DIR = REPO_ROOT / "docs" / "generated" / "usecases"
DEFAULT_TEST_DIR = REPO_ROOT / "tests" / "usecases"
GENERATOR_ID = "scripts/generate_usecase_tests.py"
GENERATED_TEST_PATTERN = "test_generated_*.py"

_DIRECTIVE_RE = re.compile(r"^#\s*([a-z][a-z0-9_-]*):\s*(.*)$")
_TITLE_WORDS = {
    "api": "API",
    "cdse": "CDSE",
    "geoparquet": "GeoParquet",
    "hls2": "HLS2",
    "oci": "OCI",
    "openaerialmap": "OpenAerialMap",
    "s3": "S3",
    "stac": "STAC",
}


class Markdown(NamedTuple):
    text: str


class Command(NamedTuple):
    text: str


class TestCommand(NamedTuple):
    text: str


class UsecaseSource(NamedTuple):
    path: Path
    slug: str
    title: str
    test_name: str
    generate_test: bool
    events: tuple[Markdown | Command | TestCommand, ...]


class GeneratedArtifact(NamedTuple):
    source: Path
    markdown: Path
    test: Path


class UnsupportedCommand(ValueError):
    pass


def parse_usecase_shell(path: Path) -> UsecaseSource:
    title = _title_from_slug(path.stem)
    test_name = f"test_{path.stem.replace('-', '_')}"
    generate_test = True
    events: list[Markdown | Command | TestCommand] = []
    command_lines: list[str] = []

    def flush_command() -> None:
        if command_lines:
            events.append(Command("\n".join(command_lines).strip()))
            command_lines.clear()

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            flush_command()
            if events and isinstance(events[-1], Markdown) and events[-1].text:
                events.append(Markdown(""))
            continue
        if stripped.startswith("#!") or stripped == "set -euo pipefail":
            flush_command()
            continue
        if stripped.startswith("#"):
            flush_command()
            match = _DIRECTIVE_RE.match(stripped)
            if match:
                key, value = match.groups()
                if key == "title":
                    title = value
                    continue
                if key == "test":
                    if value.lower() in {"false", "no", "none", "skip"}:
                        generate_test = False
                        continue
                    test_name = value
                    continue
                if key in {"test-setup", "setup"}:
                    events.append(TestCommand(_directive_command(value, "setup")))
                    continue
                if key in {"test-assert", "assert"}:
                    events.append(TestCommand(_directive_command(value, "assert")))
                    continue
                if key in {"description", "mark"}:
                    continue
            comment = stripped.removeprefix("#").strip()
            if comment:
                events.append(Markdown(comment))
            continue
        command_lines.append(raw_line)
        if not stripped.endswith("\\"):
            flush_command()
    flush_command()
    return UsecaseSource(
        path=path,
        slug=path.stem,
        title=title,
        test_name=test_name,
        generate_test=generate_test,
        events=tuple(events),
    )


def markdown_from_usecase(usecase: UsecaseSource) -> str:
    lines = [
        f"# {usecase.title}",
        "",
        f"<!-- Generated from `{_display_path(usecase.path)}` by {GENERATOR_ID}; do not edit by hand. -->",
        "",
    ]
    markdown_lines: list[str] = []

    def flush_markdown() -> None:
        if markdown_lines:
            lines.extend(markdown_lines)
            lines.append("")
            markdown_lines.clear()

    for event in usecase.events:
        if isinstance(event, Markdown):
            markdown_lines.append(event.text)
            continue
        flush_markdown()
        if isinstance(event, TestCommand):
            continue
        lines.extend(["```bash", event.text, "```", ""])
    flush_markdown()
    return "\n".join(lines).rstrip() + "\n"


def markdown_index_from_usecases(usecases: list[UsecaseSource]) -> str:
    lines = [
        "# Use Cases",
        "",
        f"<!-- Generated from `{_display_path(DEFAULT_SOURCE_DIR)}` by {GENERATOR_ID}; do not edit by hand. -->",
        "",
        "These pages are generated from shell sources in `docs/usecases`.",
        "",
    ]
    if not usecases:
        lines.extend(["No use case shell sources are available yet.", ""])
        return "\n".join(lines)
    for usecase in usecases:
        lines.append(f"- [{usecase.title}]({usecase.slug}.md)")
    lines.append("")
    return "\n".join(lines)


def python_test_from_usecase(usecase: UsecaseSource) -> str:
    if not usecase.generate_test:
        raise UnsupportedCommand(f"{_display_path(usecase.path)} is marked as docs-only")
    if not _has_test_assertion(usecase):
        raise UnsupportedCommand(
            f"{_display_path(usecase.path)} must include at least one # test-assert directive"
        )

    body_lines = ["shell = UsecaseShell(tmp_path)"]
    body_lines.extend(_setup_calls(usecase))
    shell_script = "\n\n".join(event.text for event in usecase.events if isinstance(event, Command))
    if shell_script:
        body_lines.extend(
            ["shell.run(", _indent(_python_multiline_string(shell_script), "    "), ")"]
        )
    body_lines.extend(_assertion_calls(usecase))
    body = _indent("\n".join(body_lines), "    ")

    return (
        "# Copyright 2026, Versioneer (https://versioneer.at)\n"
        "# SPDX-License-Identifier: Apache-2.0\n\n"
        f"# Generated from `{_display_path(usecase.path)}` by {GENERATOR_ID}; do not edit by hand.\n\n"
        "from __future__ import annotations\n\n"
        "from pathlib import Path\n\n"
        "import pytest\n\n"
        "from tests.usecases.runtime import (\n"
        "    UsecaseShell,\n"
        "    assert_asset_lock_keys,\n"
        "    assert_asset_lock_store,\n"
        "    assert_file_exists,\n"
        "    assert_item_alternate_hrefs,\n"
        "    assert_item_asset_hrefs,\n"
        "    assert_item_provider_names,\n"
        "    assert_no_file,\n"
        "    assert_package_asset_files,\n"
        "    assert_package_assets,\n"
        "    assert_package_file,\n"
        "    assert_package_items,\n"
        "    assert_parquet_columns,\n"
        "    assert_parquet_equals,\n"
        "    assert_parquet_rows,\n"
        "    setup_file,\n"
        "    setup_openaerialmap_provider_asset_lock,\n"
        "    setup_openaerialmap_provider_items,\n"
        "    setup_openaerialmap_asset_lock,\n"
        "    setup_openaerialmap_items,\n"
        "    setup_openaerialmap_s3_items,\n"
        ")\n\n\n"
        "@pytest.mark.usecase\n"
        f"def {usecase.test_name}(tmp_path: Path) -> None:\n"
        f"{body}\n"
    )


def generate_usecase_artifacts(
    *,
    source_dir: Path = DEFAULT_SOURCE_DIR,
    source_pattern: str = DEFAULT_SOURCE_PATTERN,
    markdown_dir: Path = DEFAULT_MARKDOWN_DIR,
    test_dir: Path = DEFAULT_TEST_DIR,
    check: bool = False,
    check_tests: bool = False,
    write_tests: bool = True,
) -> list[GeneratedArtifact]:
    artifacts: list[GeneratedArtifact] = []
    generated_tests: list[Path] = []
    sources_and_usecases = [
        (source, parse_usecase_shell(source)) for source in sorted(source_dir.glob(source_pattern))
    ]
    usecases = [usecase for _, usecase in sources_and_usecases]
    if not check:
        _write_or_check(
            markdown_dir / "index.md",
            markdown_index_from_usecases(usecases),
            check=False,
        )
    for source, usecase in sources_and_usecases:
        markdown_path = markdown_dir / f"{usecase.slug}.md"
        test_path = test_dir / _test_filename(usecase)
        if not check:
            _write_or_check(markdown_path, markdown_from_usecase(usecase), check=False)
        else:
            markdown_from_usecase(usecase)
        if usecase.generate_test:
            test_text = python_test_from_usecase(usecase)
        else:
            test_text = ""
        if usecase.generate_test and write_tests and (not check or check_tests):
            _write_or_check(test_path, test_text, check=check)
            generated_tests.append(test_path)
        artifacts.append(GeneratedArtifact(source=source, markdown=markdown_path, test=test_path))
    if write_tests and not check:
        _prune_generated_tests(test_dir, keep=generated_tests)
    return artifacts


def generate_usecase_tests(
    *,
    source_dir: Path = DEFAULT_SOURCE_DIR,
    source_pattern: str = DEFAULT_SOURCE_PATTERN,
    test_dir: Path = DEFAULT_TEST_DIR,
) -> list[Path]:
    tests: list[Path] = []
    for source in sorted(source_dir.glob(source_pattern)):
        usecase = parse_usecase_shell(source)
        if not usecase.generate_test:
            continue
        test_path = test_dir / _test_filename(usecase)
        _write_or_check(test_path, python_test_from_usecase(usecase), check=False)
        tests.append(test_path)
    _prune_generated_tests(test_dir, keep=tests)
    return tests


def _setup_calls(usecase: UsecaseSource) -> list[str]:
    return [
        _directive_call(event.text)
        for event in usecase.events
        if isinstance(event, TestCommand) and event.text.startswith("setup-")
    ]


def _assertion_calls(usecase: UsecaseSource) -> list[str]:
    return [
        _directive_call(event.text)
        for event in usecase.events
        if isinstance(event, TestCommand) and event.text.startswith("assert-")
    ]


def _directive_call(command: str) -> str:
    tokens = _command_tokens(command)
    if not tokens:
        raise UnsupportedCommand("empty test directive")

    name = tokens[0]
    if name == "setup-openaerialmap-items" and len(tokens) >= 2:
        return _fixture_call("setup_openaerialmap_items", tokens)
    if name == "setup-openaerialmap-s3-items" and len(tokens) >= 2:
        return _fixture_call("setup_openaerialmap_s3_items", tokens)
    if name == "setup-openaerialmap-asset-lock" and len(tokens) >= 2:
        return _fixture_call("setup_openaerialmap_asset_lock", tokens)
    if name == "setup-openaerialmap-provider-items" and len(tokens) >= 2:
        return _fixture_call("setup_openaerialmap_provider_items", tokens)
    if name == "setup-openaerialmap-provider-asset-lock" and len(tokens) >= 2:
        return _fixture_call("setup_openaerialmap_provider_asset_lock", tokens)
    if name == "setup-file" and len(tokens) >= 2:
        text = _option_value(tokens, "--text") or "# Generated usecase include\n"
        return f"setup_file(tmp_path, {_py_string(tokens[1])}, text={_py_string(text)})"
    if name == "assert-parquet-rows" and len(tokens) == 3:
        return _row_assertion_call("assert_parquet_rows", tokens)
    if name == "assert-parquet-columns" and len(tokens) >= 3:
        return _varargs_call("assert_parquet_columns", tokens[1:])
    if name == "assert-parquet-equals" and len(tokens) == 3:
        return _varargs_call("assert_parquet_equals", tokens[1:])
    if name == "assert-package-items" and len(tokens) == 3:
        return _row_assertion_call("assert_package_items", tokens)
    if name == "assert-package-assets" and len(tokens) == 3:
        return _row_assertion_call("assert_package_assets", tokens)
    if name == "assert-package-file" and len(tokens) == 3:
        return _varargs_call("assert_package_file", tokens[1:])
    if name == "assert-package-asset-files" and len(tokens) == 3:
        _require_int(tokens[2], "asset file count")
        return _varargs_call("assert_package_asset_files", [tokens[1], int(tokens[2])])
    if name == "assert-item-provider-names" and len(tokens) >= 3:
        return _varargs_call("assert_item_provider_names", tokens[1:])
    if name == "assert-item-asset-hrefs" and len(tokens) >= 3:
        return _href_assertion_call("assert_item_asset_hrefs", tokens[1:])
    if name == "assert-item-alternate-hrefs" and len(tokens) >= 4:
        return _href_assertion_call("assert_item_alternate_hrefs", tokens[1:])
    if name == "assert-asset-lock-keys" and len(tokens) >= 3:
        return _varargs_call("assert_asset_lock_keys", tokens[1:])
    if name == "assert-asset-lock-store" and len(tokens) >= 3:
        return _asset_lock_store_call(tokens[1:])
    if name == "assert-file-exists" and len(tokens) == 2:
        return f"assert_file_exists(tmp_path, {_py_string(tokens[1])})"
    if name == "assert-no-file" and len(tokens) == 2:
        return f"assert_no_file(tmp_path, {_py_string(tokens[1])})"
    raise UnsupportedCommand(f"unsupported test directive: {command}")


def _fixture_call(function: str, tokens: list[str]) -> str:
    item_count = _option_value(tokens, "--item-count") or "3"
    _require_int(item_count, "--item-count")
    return f"{function}(tmp_path, {_py_string(tokens[1])}, item_count={item_count})"


def _row_assertion_call(function: str, tokens: list[str]) -> str:
    _require_int(tokens[2], "row count")
    return f"{function}(tmp_path, {_py_string(tokens[1])}, {tokens[2]})"


def _varargs_call(function: str, args: list[str | int]) -> str:
    rendered = ", ".join(_py_value(arg) for arg in args)
    return f"{function}(tmp_path, {rendered})"


def _href_assertion_call(function: str, args: list[str]) -> str:
    asset_keys = _option_values(args, "--asset-key")
    positional = _without_option_values(args, "--asset-key")
    call = _varargs_call(function, positional)
    if asset_keys:
        return call[:-1] + f", asset_keys={_py_tuple(asset_keys)})"
    return call


def _asset_lock_store_call(args: list[str]) -> str:
    positional = _without_option_values(args, "--container")
    positional = _without_option_values(positional, "--key-prefix")
    call = _varargs_call("assert_asset_lock_store", positional)
    kwargs = []
    if container := _option_value(args, "--container"):
        kwargs.append(f"container={_py_string(container)}")
    if key_prefix := _option_value(args, "--key-prefix"):
        kwargs.append(f"key_prefix={_py_string(key_prefix)}")
    if not kwargs:
        return call
    return call[:-1] + ", " + ", ".join(kwargs) + ")"


def _write_or_check(path: Path, text: str, *, check: bool) -> None:
    if check:
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError:
            existing = ""
        if existing != text:
            diff = "".join(
                difflib.unified_diff(
                    existing.splitlines(keepends=True),
                    text.splitlines(keepends=True),
                    fromfile=str(path),
                    tofile=f"{path} (generated)",
                )
            )
            raise SystemExit(f"{path} is not up to date\n{diff}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.read_text(encoding="utf-8") == text:
            return
    except OSError:
        pass
    path.write_text(text, encoding="utf-8")


def _test_filename(usecase: UsecaseSource) -> str:
    return f"test_generated_{usecase.slug.replace('-', '_')}.py"


def _has_test_assertion(usecase: UsecaseSource) -> bool:
    return any(
        isinstance(event, TestCommand) and event.text.startswith("assert-")
        for event in usecase.events
    )


def _prune_generated_tests(test_dir: Path, *, keep: list[Path]) -> None:
    if not test_dir.exists():
        return
    keep_names = {path.name for path in keep}
    for path in test_dir.glob(GENERATED_TEST_PATTERN):
        if path.name not in keep_names:
            path.unlink()


def _command_tokens(command: str) -> list[str]:
    text = command.replace("\\\n", " ").replace("\n", " ")
    return shlex.split(text)


def _directive_command(value: str, prefix: str) -> str:
    tokens = _command_tokens(value)
    if not tokens:
        raise UnsupportedCommand(f"{prefix} directive requires a command")
    if not tokens[0].startswith(f"{prefix}-"):
        tokens[0] = f"{prefix}-{tokens[0]}"
    return shlex.join(tokens)


def _option_value(tokens: list[str], option: str) -> str | None:
    values = _option_values(tokens, option)
    return values[-1] if values else None


def _option_values(tokens: list[str], option: str) -> list[str]:
    values = []
    for index, token in enumerate(tokens):
        prefix = f"{option}="
        if token.startswith(prefix):
            values.append(token.removeprefix(prefix))
            continue
        if token == option and index + 1 < len(tokens):
            values.append(tokens[index + 1])
    return values


def _without_option_values(tokens: list[str], option: str) -> list[str]:
    result = []
    skip_next = False
    prefix = f"{option}="
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token == option:
            skip_next = True
            continue
        if token.startswith(prefix):
            continue
        result.append(token)
    return result


def _require_int(value: str, label: str) -> None:
    try:
        int(value)
    except ValueError as error:
        raise UnsupportedCommand(f"{label} must be an integer: {value}") from error


def _python_multiline_string(value: str) -> str:
    if '"""' not in value:
        return f'r"""\n{value}\n"""'
    return repr(value)


def _py_string(value: str) -> str:
    return json.dumps(value)


def _py_value(value: str | int) -> str:
    return str(value) if isinstance(value, int) else _py_string(value)


def _py_tuple(values: list[str]) -> str:
    suffix = "," if len(values) == 1 else ""
    return "(" + ", ".join(_py_string(value) for value in values) + suffix + ")"


def _title_from_slug(slug: str) -> str:
    return " ".join(_TITLE_WORDS.get(word, word.capitalize()) for word in slug.split("-"))


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _indent(text: str, prefix: str) -> str:
    if not text:
        return f"{prefix}pass"
    return "\n".join(f"{prefix}{line}" if line else line for line in text.splitlines())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate use case Markdown pages and tests from shell sources."
    )
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--source-pattern", default=DEFAULT_SOURCE_PATTERN)
    parser.add_argument("--markdown-dir", type=Path, default=DEFAULT_MARKDOWN_DIR)
    parser.add_argument("--test-dir", type=Path, default=DEFAULT_TEST_DIR)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--no-tests", action="store_true", help="generate Markdown only")
    parser.add_argument(
        "--check-tests",
        action="store_true",
        help="also require generated Python tests to be present and fresh",
    )
    args = parser.parse_args()
    for artifact in generate_usecase_artifacts(
        source_dir=args.source_dir,
        source_pattern=args.source_pattern,
        markdown_dir=args.markdown_dir,
        test_dir=args.test_dir,
        check=args.check,
        check_tests=args.check_tests,
        write_tests=not args.no_tests,
    ):
        print(artifact.markdown)
        if not args.no_tests:
            print(artifact.test)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
