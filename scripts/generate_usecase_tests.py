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


class UsecaseSource(NamedTuple):
    path: Path
    slug: str
    title: str
    test_name: str
    generate_test: bool
    events: tuple[Markdown | Command, ...]


class GeneratedArtifact(NamedTuple):
    source: Path
    markdown: Path
    test: Path


class UnsupportedCommand(ValueError):
    pass


class Codegen:
    def __init__(self) -> None:
        self.imports: set[str] = set()
        self.lines: list[str] = []
        self.asset_lock_parquet_by_arrow: dict[str, str] = {}
        self._counter: dict[str, int] = {}

    def emit(self, command: str) -> None:
        tokens = _command_tokens(command)
        if not tokens:
            return
        if tokens[0] == "setup-openaerialmap-items":
            self._setup_openaerialmap_items(tokens)
            return
        if tokens[0].startswith("assert-"):
            self._assertion(tokens)
            return
        segments = _pipeline_segments(tokens)
        if segments and segments[0][:1] == ["stacpkg"]:
            self._stacpkg_pipeline(segments, command)
            return
        raise UnsupportedCommand(f"unsupported usecase command: {command}")

    def _setup_openaerialmap_items(self, tokens: list[str]) -> None:
        if len(tokens) < 2:
            raise UnsupportedCommand("setup-openaerialmap-items requires an output path")
        output = tokens[1]
        item_count = _option_value(tokens, "--item-count") or "3"
        _require_int(item_count, "--item-count")
        self.imports.add("from stacpkg.arrow_io import write_parquet")
        self.imports.add(
            "from tests.unit.openaerialmap_fixture import localized_openaerialmap_items"
        )
        self.lines.extend(
            [
                "write_parquet(",
                f"    localized_openaerialmap_items(tmp_path, item_count={item_count}),",
                f"    _p(tmp_path, {_py_string(output)}),",
                ")",
            ]
        )

    def _assertion(self, tokens: list[str]) -> None:
        name = tokens[0]
        if name == "assert-parquet-rows" and len(tokens) == 3:
            self._assert_parquet_rows(tokens[1], tokens[2])
            return
        if name == "assert-package-items" and len(tokens) == 3:
            self._assert_parquet_rows(f"{tokens[1].rstrip('/')}/items.parquet", tokens[2])
            return
        if name == "assert-package-assets" and len(tokens) == 3:
            self._assert_parquet_rows(f"{tokens[1].rstrip('/')}/assets.lock.parquet", tokens[2])
            return
        if name == "assert-file-exists" and len(tokens) == 2:
            self.lines.append(f"assert _p(tmp_path, {_py_string(tokens[1])}).exists()")
            return
        if name == "assert-no-file" and len(tokens) == 2:
            self.lines.append(f"assert not _p(tmp_path, {_py_string(tokens[1])}).exists()")
            return
        raise UnsupportedCommand(f"unsupported assertion command: {' '.join(tokens)}")

    def _assert_parquet_rows(self, path: str, count: str) -> None:
        _require_int(count, "row count")
        self.imports.add("from stacpkg.arrow_io import read_parquet")
        self.lines.append(f"_assert_parquet_rows(tmp_path, {_py_string(path)}, {count})")

    def _stacpkg_pipeline(self, segments: list[list[str]], command: str) -> None:
        if len(segments) == 2 and _is_items_from_parquet(segments[0]):
            if _is_items_to_parquet(segments[1]):
                self._items_from_parquet_to_parquet(segments[0], segments[1])
                return
            if _is_build(segments[1]):
                self._items_from_parquet_to_build(segments[0], segments[1])
                return
        if (
            len(segments) == 3
            and _is_items_from_parquet(segments[0])
            and _is_asset_lock_derive(segments[1])
            and _is_asset_lock_to_parquet(segments[2])
        ):
            self._items_from_parquet_to_asset_lock(segments[0], segments[1], segments[2])
            return
        raise UnsupportedCommand(f"unsupported stacpkg pipeline: {command}")

    def _items_from_parquet_to_parquet(
        self,
        source_stage: list[str],
        output_stage: list[str],
    ) -> None:
        table = self._items_variable(source_stage)
        output = output_stage[3]
        self.imports.add("from stacpkg.geoparquet import write_items_geoparquet")
        self.lines.append(f"write_items_geoparquet({table}, _p(tmp_path, {_py_string(output)}))")

    def _items_from_parquet_to_asset_lock(
        self,
        source_stage: list[str],
        derive_stage: list[str],
        output_stage: list[str],
    ) -> None:
        table = self._items_variable(source_stage)
        output = output_stage[3]
        probe_metadata = "--no-probe-metadata" not in derive_stage
        self.imports.add("from stacpkg.arrow_io import write_parquet")
        self.imports.add("from stacpkg.assets import derive_asset_lock")
        asset_lock = self._var("asset_lock")
        self.lines.extend(
            [
                f"{asset_lock} = derive_asset_lock({table}, probe_metadata={probe_metadata})",
                "write_parquet(",
                f"    {asset_lock},",
                f"    _p(tmp_path, {_py_string(output)}),",
                ")",
            ]
        )
        self.asset_lock_parquet_by_arrow[_arrow_name_for_parquet(output)] = output

    def _items_from_parquet_to_build(
        self,
        source_stage: list[str],
        build_stage: list[str],
    ) -> None:
        input_path = source_stage[3]
        package_dir = build_stage[2]
        self.imports.add("from stacpkg.dataset import build_package")
        args = [
            f"_p(tmp_path, {_py_string(input_path)})",
            f"_p(tmp_path, {_py_string(package_dir)})",
        ]
        kwargs = []
        asset_lock = _option_value(build_stage, "--asset-lock")
        if asset_lock is not None:
            self.imports.add("from stacpkg.arrow_io import read_parquet")
            asset_lock_parquet = self.asset_lock_parquet_by_arrow.get(asset_lock, asset_lock)
            if asset_lock_parquet.endswith(".arrow"):
                asset_lock_parquet = asset_lock_parquet.removesuffix(".arrow") + ".parquet"
            asset_lock_var = self._var("asset_lock")
            self.lines.append(
                f"{asset_lock_var} = read_parquet(_p(tmp_path, {_py_string(asset_lock_parquet)}))"
            )
            kwargs.append(f"asset_lock={asset_lock_var}")
        if "--include-assets" in build_stage:
            kwargs.append("include_assets=True")
        call_args = args + kwargs
        self.lines.extend(["build_package(", *(f"    {arg}," for arg in call_args), ")"])

    def _items_variable(self, stage: list[str]) -> str:
        input_path = stage[3]
        self.imports.add("from stacpkg.arrow_io import read_parquet")
        items = self._var("items")
        providers = _option_values(stage, "--providers")
        if providers:
            self.imports.add("from stacpkg.items import filter_items")
            provider_set = "{" + ", ".join(_py_string(v) for v in providers) + "}"
            self.lines.extend(
                [
                    f"{items} = filter_items(",
                    f"    read_parquet(_p(tmp_path, {_py_string(input_path)})),",
                    f"    providers={provider_set},",
                    ")",
                ]
            )
            return items
        self.lines.append(f"{items} = read_parquet(_p(tmp_path, {_py_string(input_path)}))")
        return items

    def _var(self, prefix: str) -> str:
        next_value = self._counter.get(prefix, 0) + 1
        self._counter[prefix] = next_value
        return f"{prefix}_{next_value}"


def parse_usecase_shell(path: Path) -> UsecaseSource:
    title = _title_from_slug(path.stem)
    test_name = f"test_{path.stem.replace('-', '_')}"
    generate_test = True
    events: list[Markdown | Command] = []
    command_lines: list[str] = []

    def flush_command() -> None:
        if command_lines:
            events.append(Command("\n".join(command_lines).strip()))
            command_lines.clear()

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#!") or stripped == "set -euo pipefail":
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
    for event in usecase.events:
        if isinstance(event, Markdown):
            lines.extend([event.text, ""])
            continue
        lines.extend(["```bash", event.text, "```", ""])
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
    codegen = Codegen()
    for event in usecase.events:
        if isinstance(event, Command):
            codegen.emit(event.text)
    codegen.imports.add("from stacpkg.arrow_io import read_parquet")

    imports = [
        "from pathlib import Path",
        "",
        "import pytest",
        *sorted(codegen.imports),
    ]
    body = _indent("\n".join(codegen.lines), "    ")
    return (
        "# Copyright 2026, Versioneer (https://versioneer.at)\n"
        "# SPDX-License-Identifier: Apache-2.0\n\n"
        f"# Generated from `{_display_path(usecase.path)}` by {GENERATOR_ID}; do not edit by hand.\n\n"
        "from __future__ import annotations\n\n" + "\n".join(imports) + "\n\n\n"
        "def _p(tmp_path: Path, value: str) -> Path:\n"
        '    return tmp_path / value.rstrip("/")\n\n\n'
        "def _assert_parquet_rows(tmp_path: Path, value: str, count: int) -> None:\n"
        "    assert read_parquet(_p(tmp_path, value)).num_rows == count\n\n\n"
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


def _pipeline_segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token == "|":
            segments.append([])
            continue
        segments[-1].append(token)
    return segments


def _is_items_from_parquet(stage: list[str]) -> bool:
    return len(stage) >= 4 and stage[:3] == ["stacpkg", "items", "from-parquet"]


def _is_items_to_parquet(stage: list[str]) -> bool:
    return len(stage) == 4 and stage[:3] == ["stacpkg", "items", "to-parquet"]


def _is_asset_lock_derive(stage: list[str]) -> bool:
    return len(stage) >= 3 and stage[:3] == ["stacpkg", "asset-lock", "derive"]


def _is_asset_lock_to_parquet(stage: list[str]) -> bool:
    return len(stage) == 4 and stage[:3] == ["stacpkg", "asset-lock", "to-parquet"]


def _is_build(stage: list[str]) -> bool:
    return len(stage) >= 3 and stage[:2] == ["stacpkg", "build"]


def _option_value(tokens: list[str], option: str) -> str | None:
    values = _option_values(tokens, option)
    return values[-1] if values else None


def _option_values(tokens: list[str], option: str) -> list[str]:
    values = []
    for index, token in enumerate(tokens):
        if token == option and index + 1 < len(tokens):
            values.append(tokens[index + 1])
    return values


def _require_int(value: str, label: str) -> None:
    try:
        int(value)
    except ValueError as error:
        raise UnsupportedCommand(f"{label} must be an integer: {value}") from error


def _py_string(value: str) -> str:
    return json.dumps(value)


def _arrow_name_for_parquet(path: str) -> str:
    if path.endswith(".parquet"):
        return path.removesuffix(".parquet") + ".arrow"
    return f"{path}.arrow"


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
