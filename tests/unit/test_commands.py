# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import inspect
import json
import re
import shlex
from contextlib import redirect_stderr
from io import BytesIO
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from stacpkg.arrow_io import read_parquet, read_stream, write_parquet, write_stream
from stacpkg.assets import asset_lock_table, derive_asset_lock, plan_copy_assets
from stacpkg.cli import build_parser
from stacpkg.dataset import build_package
from stacpkg.enrich import ALTERNATE_ASSETS_EXTENSION, FILE_EXTENSION, enrich_items
from stacpkg.geoparquet import write_items_geoparquet
from stacpkg.items import filter_items
from stacpkg.locators import href_from_location, location_from_href
from stacpkg.object_store import validate_assets
from stacpkg.oci import (
    ASSET_LOCK_MEDIA_TYPE,
    ASSET_MEDIA_TYPE,
    ARTIFACT_TYPE,
    CONFIG_NAME,
    EMPTY_CONFIG_DIGEST,
    EMPTY_CONFIG_MEDIA_TYPE,
    EMPTY_CONFIG_SIZE,
    FILES_ZIP_MEDIA_TYPE,
    ITEMS_MEDIA_TYPE,
    OCI_MANIFEST_MEDIA_TYPE,
    TITLE_ANNOTATION,
)
from stacpkg.oci import pull_package, push_package
from stacpkg.projection import project_item_assets, promote_alternate_asset_hrefs
from stacpkg.report import package_inspect_data, package_inspect_markdown, package_inspect_yaml
from stacpkg.schemas import ASSET_LOCK_COLUMNS
from stacpkg.stac_json import read_stac_json
from openaerialmap_fixture import (
    LOCAL_OPENAERIALMAP_ASSET_KEYS,
    write_localized_openaerialmap_item_collection_json,
)
from tests.data.openaerialmap_data import openaerialmap_items

DEFAULT_OPENAERIALMAP_LOCKED_ASSET_KEYS = tuple(
    asset_key for asset_key in LOCAL_OPENAERIALMAP_ASSET_KEYS if asset_key != "metadata"
)


EXPECTED_CLI_GROUP_DESCRIPTIONS = {
    ("items",): "Work with STAC items table commands.",
    ("asset-lock",): "Work with asset-lock table commands.",
}

EXPECTED_CLI_COMMANDS = {
    ("build",): {
        "argv": ["build", "stacpkg.pkg"],
        "description": "Build a package directory from items.",
        "handler": "_build",
        "library_calls": ("build_package",),
    },
    ("inspect",): {
        "argv": ["inspect", "stacpkg.pkg"],
        "description": "Inspect package contents as YAML, JSON, or Markdown.",
        "handler": "_inspect",
        "library_calls": (
            "package_inspect_data",
            "package_inspect_markdown",
            "package_inspect_yaml",
        ),
    },
    ("push",): {
        "argv": ["push", "stacpkg.pkg", "registry.local/stacpkg/example:v1"],
        "description": "Push package artifacts to an OCI registry.",
        "handler": "_push",
        "library_calls": ("push_package",),
    },
    ("pull",): {
        "argv": [
            "pull",
            "registry.local/stacpkg/example:v1",
            "--output-dir",
            "stacpkg.pkg",
        ],
        "description": "Pull package artifacts from an OCI registry.",
        "handler": "_pull",
        "library_calls": ("pull_package",),
    },
    ("items", "from-json"): {
        "argv": ["items", "from-json", "source.itemcollection.json"],
        "description": "Convert STAC JSON into items Arrow streams.",
        "handler": "_items_from_json",
        "library_calls": (
            "_read_stac_json_input",
            "_item_filter_transform",
            "items_table_to_geoparquet_table",
            "_write_items_output",
        ),
    },
    ("items", "from-ndjson"): {
        "argv": ["items", "from-ndjson", "source.ndjson"],
        "description": "Convert STAC NDJSON into items Arrow streams.",
        "handler": "_items_from_ndjson",
        "library_calls": (
            "write_stac_ndjson_stream",
            "_item_filter_transform",
        ),
    },
    ("items", "from-parquet"): {
        "argv": ["items", "from-parquet", "source.items.parquet"],
        "description": "Read STAC GeoParquet as Arrow IPC.",
        "handler": "_items_from_parquet",
        "library_calls": ("_write_parquet_stdout", "_item_filter_transform"),
    },
    ("items", "to-parquet"): {
        "argv": ["items", "to-parquet", "output.items.parquet"],
        "description": "Write items streams as STAC GeoParquet.",
        "handler": "_items_to_parquet",
        "library_calls": ("write_items_geoparquet_stream",),
    },
    ("items", "promote-alternate"): {
        "argv": [
            "items",
            "promote-alternate",
            "--alternate-key",
            "s3",
            "--mode",
            "switch",
        ],
        "description": "Promote alternate asset hrefs in items streams.",
        "handler": "_items_promote_alternate",
        "library_calls": ("_write_items_transform", "promote_alternate_asset_hrefs"),
    },
    ("items", "remove-alternate"): {
        "argv": [
            "items",
            "remove-alternate",
            "--alternate-key",
            "s3",
        ],
        "description": "Remove alternate asset hrefs in items streams.",
        "handler": "_items_remove_alternate",
        "library_calls": ("_write_items_transform", "remove_alternate_asset_hrefs"),
    },
    ("items", "add-alternate"): {
        "argv": [
            "items",
            "add-alternate",
            "--asset-lock",
            "source.assets.lock.arrow",
            "--alternate-key",
            "controlled",
            "--alternate-name",
            "Controlled copy",
        ],
        "description": "Add alternate asset hrefs in items streams.",
        "handler": "_items_add_alternate",
        "library_calls": ("_write_items_transform", "add_alternate_asset_hrefs"),
    },
    ("items", "enrich"): {
        "argv": [
            "items",
            "enrich",
            "--asset-lock",
            "source.assets.lock.arrow",
        ],
        "description": "Write asset lock facts into items.",
        "handler": "_items_enrich",
        "library_calls": ("_write_items_transform", "enrich_items"),
    },
    ("asset-lock", "derive"): {
        "argv": [
            "asset-lock",
            "derive",
        ],
        "description": "Derive asset lock rows from items streams.",
        "handler": "_asset_lock_derive",
        "library_calls": ("derive_asset_lock", "_write_asset_lock_output"),
    },
    ("asset-lock", "from-parquet"): {
        "argv": ["asset-lock", "from-parquet", "source.assets.lock.parquet"],
        "description": "Read asset-lock Parquet as Arrow IPC.",
        "handler": "_asset_lock_from_parquet",
        "library_calls": ("_write_parquet_stdout",),
    },
    ("asset-lock", "to-parquet"): {
        "argv": ["asset-lock", "to-parquet", "output.assets.lock.parquet"],
        "description": "Write asset-lock streams as Parquet.",
        "handler": "_asset_lock_to_parquet",
        "library_calls": ("write_asset_lock_parquet_stream",),
    },
    ("asset-lock", "validate"): {
        "argv": ["asset-lock", "validate"],
        "description": "Validate current assets against locked facts.",
        "handler": "_asset_lock_validate",
        "library_calls": ("validate_assets", "_write_text_output"),
    },
    ("asset-lock", "relocate"): {
        "argv": [
            "asset-lock",
            "relocate",
            "--store-type",
            "s3",
            "--store-container",
            "bucket",
            "--key",
            "copied/",
        ],
        "description": "Plan or relocate asset bytes into lock locations.",
        "handler": "_asset_lock_relocate",
        "library_calls": ("relocate_asset_locations", "copy_assets", "_write_asset_lock_output"),
    },
}

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_COMMENT_RE = re.compile(r"^\s*#\s*CLI:\s?(.*)$")
COMMENT_RE = re.compile(r"^\s*#\s?(.*)$")


def _write_source(tmp_path: Path) -> Path:
    return write_localized_openaerialmap_item_collection_json(
        tmp_path,
        tmp_path / "openaerialmap-local.itemcollection.json",
    )


def _href(row: dict[str, object]) -> str:
    value = href_from_location(row)
    assert isinstance(value, str)
    return value


def _set_href(row: dict[str, object], href: str) -> dict[str, object]:
    row.update(location_from_href(href))
    return row


def _stream_buffer(table: Any) -> BytesIO:
    buffer = BytesIO()
    write_stream(table, buffer)
    buffer.seek(0)
    return buffer


def _write_stream_file(path: Path, table: Any) -> None:
    with path.open("wb") as handle:
        write_stream(table, handle)


def _item_assets(row: dict[str, Any]) -> dict[str, Any]:
    if "assets_json" in row:
        return json.loads(str(row.get("assets_json") or "{}"))
    assets = row.get("assets")
    assert isinstance(assets, dict)
    return assets


def test_command_parser_exposes_public_command_groups() -> None:
    subcommands = dict(_subcommand_parsers(build_parser()))

    assert set(subcommands) == set(EXPECTED_CLI_GROUP_DESCRIPTIONS) | set(EXPECTED_CLI_COMMANDS)
    assert ("register",) not in subcommands
    assert ("create-geoparquet",) not in subcommands
    assert ("assets",) not in subcommands
    assert ("asset-lock", "to-jsonl") not in subcommands
    assert ("asset-lock", "lock") not in subcommands
    assert ("asset-lock", "map-locations") not in subcommands
    assert ("items", "write") not in subcommands
    assert ("items", "project-assets") not in subcommands


def test_command_parser_does_not_expose_file_option_flags() -> None:
    parser = build_parser()
    deprecated_options = {f"--{name}-file" for name in ("input", "output", "asset-lock")}
    exposed_options = {
        option
        for _, subparser in _subcommand_parsers(parser)
        for action in subparser._actions
        for option in action.option_strings
    }

    assert deprecated_options.isdisjoint(exposed_options)


def test_items_promote_alternate_only_exposes_replace_and_switch_modes() -> None:
    subcommands = dict(_subcommand_parsers(build_parser()))
    promote_alternate = subcommands[("items", "promote-alternate")]
    mode_actions = [action for action in promote_alternate._actions if action.dest == "mode"]

    assert len(mode_actions) == 1
    assert mode_actions[0].choices == ("replace", "switch")
    assert mode_actions[0].default == "replace"


def _subcommand_parsers(
    parser: argparse.ArgumentParser,
    *,
    prefix: tuple[str, ...] = (),
) -> list[tuple[tuple[str, ...], argparse.ArgumentParser]]:
    parsers: list[tuple[tuple[str, ...], argparse.ArgumentParser]] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, subparser in action.choices.items():
                path = (*prefix, name)
                parsers.append((path, subparser))
                parsers.extend(_subcommand_parsers(subparser, prefix=path))
    return parsers


def _word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)?", text))


def test_all_cli_commands_have_short_help_descriptions() -> None:
    parser = build_parser()
    expected_descriptions = EXPECTED_CLI_GROUP_DESCRIPTIONS | {
        command: spec["description"] for command, spec in EXPECTED_CLI_COMMANDS.items()
    }

    assert 5 <= _word_count(parser.description or "") <= 10
    for path, subparser in _subcommand_parsers(parser):
        description = subparser.description or ""
        assert description == expected_descriptions[path]
        assert 5 <= _word_count(description) <= 10, " ".join(path)
        help_text = subparser.format_help()
        assert description in help_text
        if path in EXPECTED_CLI_COMMANDS:
            method_label = "Library method"
            assert method_label in help_text
            for library_call in EXPECTED_CLI_COMMANDS[path]["library_calls"]:
                if library_call.startswith("_"):
                    continue
                assert library_call in help_text


def test_cli_commands_route_to_expected_library_methods() -> None:
    parser = build_parser()

    for path, spec in EXPECTED_CLI_COMMANDS.items():
        args = parser.parse_args(spec["argv"])
        handler = args.func
        handler_source = inspect.getsource(handler)

        assert handler.__name__ == spec["handler"], " ".join(path)
        for library_call in spec["library_calls"]:
            assert f"{library_call}(" in handler_source, " ".join(path)


def test_cli_stream_contract_reference_lists_current_commands() -> None:
    reference = (REPO_ROOT / "docs/reference-guides/cli.md").read_text(encoding="utf-8")
    asset_lock_reference = (REPO_ROOT / "docs/reference-guides/asset-lock.md").read_text(
        encoding="utf-8"
    )

    assert "## Command Overview" in reference
    assert "## CLI Stream Contract" not in asset_lock_reference
    for path in EXPECTED_CLI_COMMANDS:
        command = " ".join(path)
        assert f"| `{command}` |" in reference


def _cli_comment_blocks() -> list[tuple[Path, int, str]]:
    blocks: list[tuple[Path, int, str]] = []
    for path in sorted((REPO_ROOT / "tests").rglob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        index = 0
        while index < len(lines):
            match = CLI_COMMENT_RE.match(lines[index])
            if match is None:
                index += 1
                continue

            start_line = index + 1
            command_lines = [match.group(1).rstrip()]
            index += 1
            while index < len(lines):
                if CLI_COMMENT_RE.match(lines[index]):
                    break
                comment = COMMENT_RE.match(lines[index])
                if comment is None:
                    break
                text = comment.group(1)
                if text and not text.startswith(" "):
                    break
                command_lines.append(text.rstrip())
                index += 1

            command_parts = []
            for part in command_lines:
                stripped = part.strip()
                if stripped.endswith("\\"):
                    stripped = stripped[:-1].rstrip()
                if stripped:
                    command_parts.append(stripped)
            command = " ".join(command_parts)
            blocks.append((path, start_line, command))
    return blocks


def _pipeline_segments(command: str) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for token in shlex.split(command):
        if token == "|":
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def test_cli_comment_examples_parse_with_current_cli() -> None:
    parser = build_parser()
    failures = []
    parsed_segments = 0

    for path, line_number, command in _cli_comment_blocks():
        for segment in _pipeline_segments(command):
            if segment[:2] == ["uv", "run"]:
                segment = segment[2:]
            if not segment or segment[0] != "stacpkg":
                continue

            stderr = StringIO()
            try:
                with redirect_stderr(stderr):
                    parser.parse_args(segment[1:])
            except SystemExit as error:
                details = stderr.getvalue().strip()
                failures.append(
                    f"{path.relative_to(REPO_ROOT)}:{line_number}: exit {error.code}: "
                    f"{' '.join(segment)}\n{details}"
                )
            else:
                parsed_segments += 1

    assert parsed_segments > 0
    assert not failures, "\n\n".join(failures)


def test_probe_metadata_defaults_to_true() -> None:
    parser = build_parser()

    derive_args = parser.parse_args(["asset-lock", "derive"])
    build_args = parser.parse_args(["build", "package.pkg"])

    assert derive_args.probe_metadata is True
    assert build_args.probe_metadata is True


def test_asset_lock_derive_accepts_probe_metadata_options() -> None:
    args = build_parser().parse_args(
        [
            "asset-lock",
            "derive",
            "--no-probe-metadata",
            "--keep-going",
            "--max-workers",
            "2",
            "--asset-keys",
            "image",
            "--providers",
            "WebODM",
            "--include-metadata-assets",
        ]
    )

    assert args.probe_metadata is False
    assert args.keep_going is True
    assert args.max_workers == 2
    assert args.asset_keys == ["image"]
    assert args.providers == ["WebODM"]
    assert args.include_metadata_assets is True


def test_from_parquet_commands_accept_batch_size() -> None:
    parser = build_parser()

    item_args = parser.parse_args(
        ["items", "from-parquet", "source.items.parquet", "--batch-size", "7"]
    )
    asset_args = parser.parse_args(
        ["asset-lock", "from-parquet", "source.assets.lock.parquet", "--batch-size", "11"]
    )

    assert item_args.batch_size == 7
    assert asset_args.batch_size == 11


def test_items_from_ndjson_accepts_batch_size() -> None:
    args = build_parser().parse_args(["items", "from-ndjson", "items.ndjson", "--batch-size", "5"])

    assert args.batch_size == 5


def test_item_source_commands_accept_item_filters() -> None:
    parser = build_parser()

    from_json = parser.parse_args(
        [
            "items",
            "from-json",
            "source.itemcollection.json",
            "--collections",
            "openaerialmap",
            "--providers",
            "ODM",
            "--item-ids",
            "item-1",
        ]
    )
    from_ndjson = parser.parse_args(
        [
            "items",
            "from-ndjson",
            "source.ndjson",
            "--collections",
            "openaerialmap",
            "--providers",
            "ODM",
            "--item-ids",
            "item-1",
        ]
    )
    from_parquet = parser.parse_args(
        [
            "items",
            "from-parquet",
            "source.items.parquet",
            "--collections",
            "openaerialmap",
            "--providers",
            "ODM",
            "--item-ids",
            "item-1",
        ]
    )

    for args in (from_json, from_ndjson, from_parquet):
        assert args.collections == ["openaerialmap"]
        assert args.providers == ["ODM"]
        assert args.item_ids == ["item-1"]


def test_asset_lock_validate_accepts_filter_and_runtime_options() -> None:
    args = build_parser().parse_args(
        [
            "asset-lock",
            "validate",
            "--asset-keys",
            "image",
            "--keep-going",
            "--max-workers",
            "2",
        ]
    )

    assert args.asset_keys == ["image"]
    assert args.keep_going is True
    assert args.max_workers == 2


def test_item_and_asset_lock_command_flow(tmp_path: Path) -> None:
    source = _write_source(tmp_path)
    items_path = tmp_path / "source.items.parquet"
    assets_path = tmp_path / "source.assets.lock.parquet"

    # CLI: stacpkg items from-json source.json | stacpkg items to-parquet source.items.parquet
    write_items_geoparquet(read_stac_json(source), items_path)
    # CLI: stacpkg items from-parquet source.items.parquet | stacpkg asset-lock derive | stacpkg asset-lock to-parquet source.assets.lock.parquet
    write_parquet(derive_asset_lock(read_parquet(items_path)), assets_path)

    items = read_parquet(items_path)
    assets = read_parquet(assets_path).to_pylist()
    assert items.num_rows == 1
    assert items.schema.metadata[b"stac_geoparquet:version"] == b"1.0.0"
    assert {row["asset_key"] for row in assets} == set(DEFAULT_OPENAERIALMAP_LOCKED_ASSET_KEYS)
    assert read_parquet(assets_path).schema.names == list(ASSET_LOCK_COLUMNS)


def test_filter_items_can_select_by_provider(tmp_path: Path) -> None:
    items_path = tmp_path / "openaerialmap.items.parquet"
    write_items_geoparquet(openaerialmap_items(item_count=2), items_path)

    filtered = filter_items(read_parquet(items_path), providers={"Pierre d'Huy"})

    assert filtered.num_rows == 1
    assert filtered.to_pylist()[0]["providers"][0]["name"] == "Pierre d'Huy"


def test_filter_items_can_select_by_collection(tmp_path: Path) -> None:
    items_path = tmp_path / "openaerialmap.items.parquet"
    write_items_geoparquet(openaerialmap_items(item_count=2), items_path)

    filtered = filter_items(read_parquet(items_path), collections={"openaerialmap"})

    assert filtered.num_rows == 2
    assert {row["collection"] for row in filtered.to_pylist()} == {"openaerialmap"}


def test_asset_lock_derive_can_filter_by_provider(tmp_path: Path) -> None:
    items_path = tmp_path / "openaerialmap.items.parquet"
    write_items_geoparquet(openaerialmap_items(item_count=2), items_path)

    assets = derive_asset_lock(
        read_parquet(items_path),
        providers={"not-present"},
        include_metadata_assets=True,
    )

    assert assets.num_rows == 0
    assert assets.schema.names == list(ASSET_LOCK_COLUMNS)


def test_plan_copy_assets_rewrites_only_matching_prefix(
    tmp_path: Path,
) -> None:
    source = _write_source(tmp_path)
    source_base = (tmp_path / "source").as_uri()
    source_other_base = (tmp_path / "source-other").as_uri()
    target_base = (tmp_path / "target-products").as_uri()

    rows = []
    for row in derive_asset_lock(read_stac_json(source), include_metadata_assets=True).to_pylist():
        row = dict(row)
        if row["asset_key"] == "thumbnail":
            _set_href(row, f"{source_base}/thumbnail.png")
        else:
            _set_href(row, f"{source_other_base}/{row['asset_key']}.bin")
        rows.append(row)

    planned = plan_copy_assets(
        asset_lock_table(rows), source_prefix=source_base, target=target_base
    )

    planned_rows = {row["asset_key"]: row for row in planned.to_pylist()}
    assert _href(planned_rows["thumbnail"]).startswith(f"{target_base}/")
    assert _href(planned_rows["metadata"]) == f"{source_other_base}/metadata.bin"


def test_parser_exposes_relocate_store_enrich_store_and_item_transform_names() -> None:
    parser = build_parser()

    relocate_args = parser.parse_args(
        [
            "asset-lock",
            "relocate",
            "--store-type",
            "s3",
            "--store-container",
            "bucket",
            "--key",
            "copied/",
            "--store-endpoint-url",
            "https://s3.amazonaws.com",
        ]
    )
    enrich_args = parser.parse_args(
        [
            "items",
            "enrich",
            "--asset-lock",
            "source.assets.lock.arrow",
            "--alternate-key",
            "s3",
        ]
    )
    add_alternate_args = parser.parse_args(
        [
            "items",
            "add-alternate",
            "--asset-lock",
            "target.assets.lock.arrow",
            "--alternate-key",
            "controlled",
            "--alternate-name",
            "Controlled copy",
        ]
    )
    stream_promote_args = parser.parse_args(
        [
            "items",
            "promote-alternate",
            "--alternate-key",
            "s3",
            "--mode",
            "switch",
        ]
    )
    stream_remove_args = parser.parse_args(
        [
            "items",
            "remove-alternate",
            "--key",
            "s3",
        ]
    )
    stream_validate_args = parser.parse_args(
        ["asset-lock", "validate", "--asset-keys", "thumbnail"]
    )
    stream_build_args = parser.parse_args(["build", "streamed.pkg"])

    assert relocate_args.asset_lock_command == "relocate"
    assert relocate_args.store_type == "s3"
    assert relocate_args.store_container == "bucket"
    assert relocate_args.key == "copied/"
    assert relocate_args.destination_lock is None
    assert relocate_args.store_endpoint_url == "https://s3.amazonaws.com"
    assert relocate_args.dry_run is False
    assert enrich_args.items_command == "enrich"
    assert enrich_args.asset_lock == Path("source.assets.lock.arrow")
    assert enrich_args.alternate_key == "s3"
    assert add_alternate_args.items_command == "add-alternate"
    assert add_alternate_args.asset_lock == Path("target.assets.lock.arrow")
    assert add_alternate_args.alternate_key == "controlled"
    assert add_alternate_args.alternate_name == "Controlled copy"
    assert stream_promote_args.items_command == "promote-alternate"
    assert stream_promote_args.mode == "switch"
    assert stream_promote_args.switched_alternate_name == "original"
    assert stream_remove_args.items_command == "remove-alternate"
    assert stream_remove_args.alternate_key == "s3"
    assert stream_validate_args.asset_lock_command == "validate"
    assert stream_build_args.output_dir == Path("streamed.pkg")


def test_parser_exposes_build_include_assets_flag() -> None:
    args = build_parser().parse_args(
        [
            "build",
            "self-contained.pkg",
            "--include-assets",
            "--include-metadata-assets",
        ]
    )

    assert args.command == "build"
    assert args.include_assets is True
    assert args.include_metadata_assets is True


def test_asset_lock_option_does_not_read_stdin() -> None:
    parser = build_parser()
    stderr = StringIO()

    with redirect_stderr(stderr):
        try:
            parser.parse_args(["items", "enrich", "--asset-lock", "-"])
        except SystemExit as error:
            assert error.code == 2
        else:
            raise AssertionError("expected parser to reject --asset-lock -")

    assert "--asset-lock expects a path to an Arrow IPC stream" in stderr.getvalue()


def test_items_enrich_command_flow_writes_file_info_and_alternate_assets(tmp_path: Path) -> None:
    source = _write_source(tmp_path)
    items_path = tmp_path / "source.items.parquet"
    assets_path = tmp_path / "mirror.assets.lock.parquet"
    enriched_path = tmp_path / "enriched.items.parquet"

    write_items_geoparquet(read_stac_json(source), items_path)
    asset_rows = []
    for row in derive_asset_lock(read_parquet(items_path)).to_pylist():
        row = dict(row)
        _set_href(row, (tmp_path / "mirror" / str(row["item_id"]) / str(row["asset_key"])).as_uri())
        asset_rows.append(row)
    write_parquet(asset_lock_table(asset_rows), assets_path)

    # CLI: stacpkg items from-parquet source.items.parquet | stacpkg items enrich --asset-lock mirror.assets.lock.arrow --alternate-key mirror | stacpkg items to-parquet enriched.items.parquet
    write_parquet(
        enrich_items(read_parquet(items_path), read_parquet(assets_path), alternate_key="mirror"),
        enriched_path,
    )

    row = read_parquet(enriched_path).to_pylist()[0]
    assert FILE_EXTENSION in row["stac_extensions"]
    assert ALTERNATE_ASSETS_EXTENSION in row["stac_extensions"]
    assert row["assets"]["thumbnail"]["file:size"] > 0
    assert row["assets"]["thumbnail"]["alternate"]["mirror"]["href"].endswith(
        "/" + row["id"] + "/thumbnail"
    )


def test_items_enrich_command_writes_alternates_from_preplanned_store_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _write_source(tmp_path)
    items = read_stac_json(source)
    assets_path = tmp_path / "s3.assets.lock.arrow"
    _write_stream_file(
        assets_path,
        plan_copy_assets(derive_asset_lock(items), target="s3://bucket/mapped/"),
    )
    output = BytesIO()
    monkeypatch.setattr("stacpkg.cli.sys.stdin", SimpleNamespace(buffer=_stream_buffer(items)))
    monkeypatch.setattr("stacpkg.cli.sys.stdout", SimpleNamespace(buffer=output))

    args = build_parser().parse_args(
        [
            "items",
            "enrich",
            "--asset-lock",
            str(assets_path),
            "--alternate-key",
            "s3",
        ]
    )

    assert args.func(args) == 0
    output.seek(0)
    row = read_stream(output).to_pylist()[0]
    asset = _item_assets(row)["thumbnail"]
    assert asset["file:size"] > 0
    assert asset["alternate"]["s3"]["href"].startswith("s3://bucket/mapped/")


def test_items_promote_alternate_command_writes_arrow_stream(tmp_path: Path, monkeypatch) -> None:
    source = _write_source(tmp_path)
    items = read_stac_json(source)
    with_alternate = project_item_assets(
        items,
        plan_copy_assets(derive_asset_lock(items), target="s3://bucket/promoted/"),
        strategy="set-alternate",
        alternate_key="s3",
    )
    output = BytesIO()
    monkeypatch.setattr(
        "stacpkg.cli.sys.stdin", SimpleNamespace(buffer=_stream_buffer(with_alternate))
    )
    monkeypatch.setattr("stacpkg.cli.sys.stdout", SimpleNamespace(buffer=output))

    args = build_parser().parse_args(
        ["items", "promote-alternate", "--alternate-key", "s3", "--mode", "switch"]
    )

    assert args.func(args) == 0
    output.seek(0)
    promoted = read_stream(output)
    asset = _item_assets(promoted.to_pylist()[0])["thumbnail"]
    assert asset["href"].startswith("s3://bucket/promoted/")
    assert asset["alternate"]["s3"]["href"].startswith("file://")
    assert asset["alternate"]["s3"]["alternate:name"] == "local"


def test_items_remove_alternate_command_writes_arrow_stream(tmp_path: Path, monkeypatch) -> None:
    source = _write_source(tmp_path)
    items = read_stac_json(source)
    with_alternate = project_item_assets(
        items,
        plan_copy_assets(derive_asset_lock(items), target="s3://bucket/promoted/"),
        strategy="set-alternate",
        alternate_key="s3",
    )
    output = BytesIO()
    monkeypatch.setattr(
        "stacpkg.cli.sys.stdin", SimpleNamespace(buffer=_stream_buffer(with_alternate))
    )
    monkeypatch.setattr("stacpkg.cli.sys.stdout", SimpleNamespace(buffer=output))

    args = build_parser().parse_args(["items", "remove-alternate", "--alternate-key", "s3"])

    assert args.func(args) == 0
    output.seek(0)
    restored = read_stream(output)
    asset = _item_assets(restored.to_pylist()[0])["thumbnail"]
    assert asset["href"].startswith("file://")
    assert "alternate" not in asset


def test_items_from_json_command_can_read_json_stdin(tmp_path: Path, monkeypatch) -> None:
    source = _write_source(tmp_path)
    output = BytesIO()
    monkeypatch.setattr("stacpkg.cli.sys.stdin", StringIO(source.read_text(encoding="utf-8")))
    monkeypatch.setattr("stacpkg.cli.sys.stdout", SimpleNamespace(buffer=output))

    args = build_parser().parse_args(["items", "from-json"])

    assert args.func(args) == 0
    output.seek(0)
    items = read_stream(output)
    assert items.num_rows == 1
    assert "assets" in items.schema.names
    assert "assets_json" not in items.schema.names
    assert items.schema.metadata[b"stac_geoparquet:version"] == b"1.0.0"


def test_items_add_alternate_command_writes_arrow_stream_by_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _write_source(tmp_path)
    items = read_stac_json(source)
    assets_path = tmp_path / "mirror.assets.lock.arrow"
    _write_stream_file(
        assets_path,
        plan_copy_assets(derive_asset_lock(items), target="s3://bucket/mirror/"),
    )
    output = BytesIO()
    monkeypatch.setattr("stacpkg.cli.sys.stdin", SimpleNamespace(buffer=_stream_buffer(items)))
    monkeypatch.setattr("stacpkg.cli.sys.stdout", SimpleNamespace(buffer=output))

    args = build_parser().parse_args(
        [
            "items",
            "add-alternate",
            "--asset-lock",
            str(assets_path),
            "--alternate-key",
            "mirror",
            "--alternate-name",
            "Mirror copy",
        ]
    )

    assert args.func(args) == 0
    output.seek(0)
    projected = read_stream(output)
    asset = _item_assets(projected.to_pylist()[0])["thumbnail"]
    assert asset["alternate"]["mirror"]["href"].startswith("s3://bucket/mirror/")
    assert asset["alternate"]["mirror"]["alternate:name"] == "Mirror copy"


def test_asset_lock_relocate_command_accepts_store_columns_without_planned_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _write_source(tmp_path)
    destination = tmp_path / "mirror"
    assets = derive_asset_lock(read_stac_json(source), include_metadata_assets=True)
    output = BytesIO()
    monkeypatch.setattr("stacpkg.cli.sys.stdin", SimpleNamespace(buffer=_stream_buffer(assets)))
    monkeypatch.setattr("stacpkg.cli.sys.stdout", SimpleNamespace(buffer=output))

    args = build_parser().parse_args(
        [
            "asset-lock",
            "relocate",
            "--store-type",
            "file",
            "--key",
            str(destination),
        ]
    )

    assert args.func(args) == 0
    output.seek(0)
    copied = read_stream(output)
    row_by_key = {row["asset_key"]: row for row in copied.to_pylist()}
    assert _href(row_by_key["thumbnail"]).startswith(destination.as_uri())
    assert Path(row_by_key["thumbnail"]["key"]).is_file()


def test_asset_lock_relocate_command_dry_run_outputs_planned_lock_without_copying(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _write_source(tmp_path)
    assets = derive_asset_lock(read_stac_json(source), include_metadata_assets=True)
    output = BytesIO()

    def fail_copy(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("dry-run must not copy assets")

    monkeypatch.setattr("stacpkg.cli.copy_assets", fail_copy)
    monkeypatch.setattr("stacpkg.cli.sys.stdin", SimpleNamespace(buffer=_stream_buffer(assets)))
    monkeypatch.setattr("stacpkg.cli.sys.stdout", SimpleNamespace(buffer=output))

    args = build_parser().parse_args(
        [
            "asset-lock",
            "relocate",
            "--store-type",
            "s3",
            "--store-container",
            "bucket",
            "--key",
            "mapped/",
            "--dry-run",
        ]
    )

    assert args.func(args) == 0
    output.seek(0)
    planned = read_stream(output)
    row_by_key = {row["asset_key"]: row for row in planned.to_pylist()}
    assert row_by_key["thumbnail"]["store_type"] == "s3"
    assert row_by_key["thumbnail"]["store_container"] == "bucket"
    assert _href(row_by_key["thumbnail"]).startswith("s3://bucket/mapped/")


def test_asset_lock_to_parquet_command_writes_arrow_stream_to_parquet(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _write_source(tmp_path)
    assets = derive_asset_lock(read_stac_json(source))
    output_path = tmp_path / "output.assets.lock.parquet"
    monkeypatch.setattr("stacpkg.cli.sys.stdin", SimpleNamespace(buffer=_stream_buffer(assets)))

    args = build_parser().parse_args(["asset-lock", "to-parquet", str(output_path)])

    assert args.func(args) == 0
    assert read_parquet(output_path).num_rows == assets.num_rows


def test_asset_lock_validate_command_can_read_asset_lock_stream(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    source = _write_source(tmp_path)
    assets = derive_asset_lock(read_stac_json(source))
    first_row = {row["asset_key"]: row for row in assets.to_pylist()}["thumbnail"]
    monkeypatch.setattr("stacpkg.cli.sys.stdin", SimpleNamespace(buffer=_stream_buffer(assets)))

    async def fake_head_href(_href: str) -> dict[str, object]:
        return {"size": first_row["size_bytes"]}

    monkeypatch.setattr("stacpkg.object_store._head_href", fake_head_href)

    args = build_parser().parse_args(["asset-lock", "validate", "--asset-keys", "thumbnail"])

    assert args.func(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["asset_key"] == "thumbnail"
    assert result["valid"] is True


def test_build_command_can_read_promoted_item_stream(tmp_path: Path, monkeypatch) -> None:
    source = _write_source(tmp_path)
    items = read_stac_json(source)
    with_alternate = project_item_assets(
        items,
        plan_copy_assets(derive_asset_lock(items), target="s3://bucket/promoted/"),
        strategy="set-alternate",
        alternate_key="s3",
    )
    promoted = promote_alternate_asset_hrefs(with_alternate, alternate_key="s3", mode="switch")
    output = tmp_path / "streamed.pkg"
    monkeypatch.setattr("stacpkg.cli.sys.stdin", SimpleNamespace(buffer=_stream_buffer(promoted)))

    args = build_parser().parse_args(["build", str(output), "--no-probe-metadata"])

    assert args.func(args) == 0
    row = {
        row["asset_key"]: row for row in read_parquet(output / "assets.lock.parquet").to_pylist()
    }["thumbnail"]
    asset = read_parquet(output / "items.parquet").to_pylist()[0]["assets"]["thumbnail"]
    assert _href(row).startswith("s3://bucket/promoted/")
    assert asset["href"].startswith("s3://bucket/promoted/")


def test_build_command_flow_can_use_existing_lock_and_include_files(tmp_path: Path) -> None:
    source = _write_source(tmp_path)
    assets_path = tmp_path / "source.assets.lock.arrow"
    readme = tmp_path / "README.md"
    provenance = tmp_path / "provenance.json"
    output = tmp_path / "stacpkg.pkg"
    readme.write_text("# Example package\n", encoding="utf-8")
    provenance.write_text('{"createdBy":"test"}\n', encoding="utf-8")
    _write_stream_file(assets_path, derive_asset_lock(read_stac_json(source)))

    # CLI: stacpkg items from-json source.json | stacpkg build stacpkg.pkg --asset-lock source.assets.lock.arrow --includes README.md --includes provenance.json
    build_package(
        source,
        output,
        asset_lock=assets_path,
        includes=[readme, provenance],
    )

    assert (output / "README.md").exists()
    assert (output / "provenance.json").exists()
    assert not (output / "manifest.json").exists()
    assert read_parquet(output / "assets.lock.parquet").num_rows == len(
        DEFAULT_OPENAERIALMAP_LOCKED_ASSET_KEYS
    )
    files = package_inspect_data(output)["files"]
    assert {entry["path"] for entry in files} == {
        "items.parquet",
        "assets.lock.parquet",
        "README.md",
        "provenance.json",
    }
    assert all(entry["digest"].startswith("sha256:") for entry in files)


def test_build_command_flow_can_include_assets_with_provenance(tmp_path: Path) -> None:
    source = _write_source(tmp_path)
    output = tmp_path / "self-contained.pkg"

    # CLI: stacpkg items from-json source.json | stacpkg build self-contained.pkg --include-assets
    build_package(source, output, include_assets=True)

    asset_rows = read_parquet(output / "assets.lock.parquet").to_pylist()
    files = package_inspect_data(output)["files"]
    asset_entries = [entry for entry in files if str(entry["path"]).startswith("assets/")]

    assert len(asset_rows) == len(DEFAULT_OPENAERIALMAP_LOCKED_ASSET_KEYS)
    assert len(asset_entries) == len(DEFAULT_OPENAERIALMAP_LOCKED_ASSET_KEYS)
    assert {row["asset_key"] for row in asset_rows} == set(DEFAULT_OPENAERIALMAP_LOCKED_ASSET_KEYS)

    manifest_paths = {entry["path"] for entry in asset_entries}
    for row in asset_rows:
        href = _href(row)
        packaged_asset = output / href
        assert href.startswith("assets/")
        assert href in manifest_paths
        assert packaged_asset.exists()
        assert row["size_bytes"] == packaged_asset.stat().st_size
        assert "file_checksum" not in row

    assert read_parquet(output / "assets.lock.parquet").schema.names == list(ASSET_LOCK_COLUMNS)
    assert all(entry["digest"].startswith("sha256:") for entry in asset_entries)


def test_build_command_flow_requires_asset_lock_listing(tmp_path: Path) -> None:
    source = _write_source(tmp_path)
    output = tmp_path / "metadata-only.pkg"

    try:
        build_package(source, output, skip=["assets-lock"])
    except ValueError as error:
        assert str(error) == "assets.lock.parquet is mandatory for package builds"
    else:
        raise AssertionError("build_package accepted an assets-lock skip")

    stderr = StringIO()
    with redirect_stderr(stderr):
        try:
            build_parser().parse_args(["build", "metadata-only.pkg", "--skip", "assets-lock"])
        except SystemExit as error:
            assert error.code == 2
        else:
            raise AssertionError("parser accepted --skip assets-lock")
    assert "unrecognized arguments: --skip assets-lock" in stderr.getvalue()


def test_inspect_command_flow_writes_package_summary(tmp_path: Path) -> None:
    source = _write_source(tmp_path)
    package = tmp_path / "stacpkg.pkg"

    build_package(source, package)
    # CLI: stacpkg inspect stacpkg.pkg
    summary = package_inspect_yaml(package)
    markdown = package_inspect_markdown(package)

    assert "kind:" not in summary
    assert "items:" in summary
    assert "count: 1" in summary
    assert "# stacpkg Inspect" in markdown
    assert "- Kind:" not in markdown
    assert "- Created:" not in markdown
    assert "- Source:" not in markdown
    assert "- Items: 1" in markdown
    assert f"- Assets: {len(DEFAULT_OPENAERIALMAP_LOCKED_ASSET_KEYS)}" in markdown
    assert "`items.parquet`" in markdown


def test_validate_command_flow_records_error_for_tampered_asset_lock_entry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _write_source(tmp_path)
    tampered = tmp_path / "tampered.assets.lock.parquet"

    rows = derive_asset_lock(read_stac_json(source)).to_pylist()
    actual_size = None
    for row in rows:
        if row["asset_key"] == "thumbnail":
            actual_size = int(row["size_bytes"])
            row["size_bytes"] = actual_size + 1
            break
    assert actual_size is not None
    write_parquet(asset_lock_table(rows), tampered)

    async def fake_head_href(_href: str) -> dict[str, object]:
        return {"size": actual_size}

    monkeypatch.setattr("stacpkg.object_store._head_href", fake_head_href)

    # CLI: stacpkg asset-lock from-parquet tampered.assets.lock.parquet | stacpkg asset-lock validate --asset-keys thumbnail --keep-going
    results = validate_assets(
        read_parquet(tampered),
        asset_keys={"thumbnail"},
        keep_going=True,
    )

    assert len(results) == 1
    result = results[0]
    error = "; ".join(str(error) for error in result["errors"])
    assert result["asset_key"] == "thumbnail"
    assert result["valid"] is False
    assert f"expected {actual_size + 1}" in error
    assert f"actual {actual_size}" in error


def test_push_and_pull_command_flow_uses_typed_oci_layers(tmp_path: Path, monkeypatch) -> None:
    source = _write_source(tmp_path)
    package = tmp_path / "stacpkg.pkg"
    pulled = tmp_path / "pulled.pkg"
    readme = tmp_path / "README.md"
    docs = tmp_path / "docs"
    readme.write_text("# Example package\n", encoding="utf-8")
    docs.mkdir()
    (docs / "note.txt").write_text("reviewed\n", encoding="utf-8")
    calls: list[dict[str, Any]] = []
    manifest: dict[str, Any] = {}
    blobs: dict[str, bytes] = {}

    class FakeOrasClient:
        def __init__(self, *, insecure: bool = False, tls_verify: bool = True):
            self.auth = SimpleNamespace(load_configs=lambda _container: None)
            calls.append(
                {
                    "method": "__init__",
                    "insecure": insecure,
                    "tls_verify": tls_verify,
                }
            )

        def get_container(self, target: str) -> str:
            return target

        def upload_blob(
            self,
            blob: str,
            _container: str,
            layer: dict[str, object],
        ) -> object:
            digest = str(layer["digest"])
            blobs[digest] = Path(blob).read_bytes()
            if layer["mediaType"] == EMPTY_CONFIG_MEDIA_TYPE:
                assert Path(blob).name == CONFIG_NAME
            calls.append(
                {
                    "method": "upload_blob",
                    "media_type": layer["mediaType"],
                }
            )
            return object()

        def upload_manifest(self, pushed_manifest: dict[str, Any], container: str) -> object:
            manifest.clear()
            manifest.update(pushed_manifest)
            calls.append(
                {
                    "method": "upload_manifest",
                    "target": container,
                    "artifact_type": pushed_manifest.get("artifactType"),
                    "config": pushed_manifest.get("config"),
                    "media_types": [
                        layer["mediaType"] for layer in pushed_manifest.get("layers", [])
                    ],
                    "titles": [
                        (layer.get("annotations") or {}).get(TITLE_ANNOTATION)
                        for layer in pushed_manifest.get("layers", [])
                    ],
                }
            )
            return object()

        def _check_200_response(self, _response: object) -> None:
            return None

        def get_manifest(self, target: str) -> dict[str, object]:
            calls.append(
                {
                    "method": "get_manifest",
                    "target": target,
                }
            )
            return manifest

        def download_blob(self, _target: str, digest: str, outfile: str) -> str:
            Path(outfile).write_bytes(blobs[digest])
            return outfile

    build_package(source, package, includes=[readme, docs], include_assets=True)
    monkeypatch.setattr("stacpkg.oci.OrasClient", FakeOrasClient)

    # CLI: stacpkg push stacpkg.pkg registry.local/stacpkg/openaerialmap-preview:v1
    push_package(package, "registry.local/stacpkg/openaerialmap-preview:v1")
    # CLI: stacpkg pull registry.local/stacpkg/openaerialmap-preview:v1 --output-dir pulled.pkg
    pull_package("registry.local/stacpkg/openaerialmap-preview:v1", pulled)

    assert calls[0] == {"method": "__init__", "insecure": False, "tls_verify": True}
    upload = next(call for call in calls if call["method"] == "upload_manifest")
    assert upload["target"] == "registry.local/stacpkg/openaerialmap-preview:v1"
    assert manifest["schemaVersion"] == 2
    assert manifest["mediaType"] == OCI_MANIFEST_MEDIA_TYPE
    assert manifest["artifactType"] == ARTIFACT_TYPE
    assert upload["artifact_type"] == ARTIFACT_TYPE
    assert upload["config"] == {
        "mediaType": EMPTY_CONFIG_MEDIA_TYPE,
        "digest": EMPTY_CONFIG_DIGEST,
        "size": EMPTY_CONFIG_SIZE,
    }
    assert manifest["config"] == upload["config"]
    assert isinstance(manifest["layers"], list)
    assert ITEMS_MEDIA_TYPE in upload["media_types"]
    assert ASSET_LOCK_MEDIA_TYPE in upload["media_types"]
    assert FILES_ZIP_MEDIA_TYPE in upload["media_types"]
    assert ASSET_MEDIA_TYPE in upload["media_types"]
    assert "README.md" in upload["titles"]
    assert "docs" in upload["titles"]
    assert {"method": "__init__", "insecure": False, "tls_verify": True} in calls[1:]
    get_manifest = next(call for call in calls if call["method"] == "get_manifest")
    assert get_manifest["target"] == "registry.local/stacpkg/openaerialmap-preview:v1"
    assert not (pulled / "manifest.json").exists()
    assert read_parquet(pulled / "items.parquet").num_rows == 1
    assert read_parquet(pulled / "assets.lock.parquet").num_rows == len(
        DEFAULT_OPENAERIALMAP_LOCKED_ASSET_KEYS
    )
    assert (pulled / "README.md").read_text(encoding="utf-8") == "# Example package\n"
    assert (pulled / "docs" / "note.txt").read_text(encoding="utf-8") == "reviewed\n"
    asset_paths = [
        href_from_location(row) for row in read_parquet(pulled / "assets.lock.parquet").to_pylist()
    ]
    assert all(isinstance(path, str) and (pulled / path).exists() for path in asset_paths)

    calls.clear()
    # CLI: stacpkg push stacpkg.pkg registry.local/stacpkg/openaerialmap-preview:plain-http --plain-http --insecure
    push_package(
        package,
        "registry.local/stacpkg/openaerialmap-preview:plain-http",
        plain_http=True,
        insecure=True,
    )
    assert calls[0] == {"method": "__init__", "insecure": True, "tls_verify": False}
