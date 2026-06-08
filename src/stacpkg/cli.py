# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Sequence

import pyarrow as pa

from stacpkg.arrow_io import (
    DEFAULT_STREAM_BATCH_SIZE,
    read_parquet,
    read_stream,
    read_stream_path,
    write_parquet_terminal_table,
    write_parquet_stream,
    write_stream,
    write_transformed_stream,
    write_terminal_table,
)
from stacpkg.assets import (
    DEFAULT_PROBE_METADATA,
    derive_asset_lock,
    relocate_asset_locations,
    write_asset_lock_parquet_stream,
)
from stacpkg.dataset import build_package
from stacpkg.enrich import enrich_items
from stacpkg.geoparquet import items_table_to_geoparquet_table, write_items_geoparquet_stream
from stacpkg.items import filter_items
from stacpkg.oci import pull_package, push_package
from stacpkg.object_store import (
    DEFAULT_COPY_CHUNK_SIZE_BYTES,
    DEFAULT_COPY_MAX_WORKERS,
    DEFAULT_COPY_MEMORY_LIMIT_BYTES,
    DEFAULT_COPY_PUT_MAX_CONCURRENCY,
    copy_assets,
    validate_assets,
)
from stacpkg.report import package_inspect_data, package_inspect_markdown, package_inspect_yaml
from stacpkg.projection import (
    PROMOTE_ALTERNATE_MODES,
    add_alternate_asset_hrefs,
    promote_alternate_asset_hrefs,
    remove_alternate_asset_hrefs,
)
from stacpkg.stac_json import (
    read_stac_json,
    read_stac_json_document,
    read_stac_ndjson,
    read_stac_ndjson_document,
    write_stac_ndjson_stream,
)


def _is_stdin_path(path: Path | None) -> bool:
    return path is None or str(path) == "-"


def _read_items_stream() -> pa.Table:
    return read_stream(sys.stdin.buffer)


def _read_asset_lock_stream() -> pa.Table:
    return read_stream(sys.stdin.buffer)


def _read_asset_lock_path(path: Path) -> pa.Table:
    return read_stream_path(path)


def _read_stac_json_input(path: Path | None) -> pa.Table:
    if _is_stdin_path(path):
        return read_stac_json_document(json.load(sys.stdin))
    assert path is not None
    return read_stac_json(path)


def _read_stac_ndjson_input(path: Path | None) -> pa.Table:
    if _is_stdin_path(path):
        return read_stac_ndjson_document(sys.stdin)
    assert path is not None
    return read_stac_ndjson(path)


def _write_items_output(table: pa.Table) -> None:
    _write_arrow_stdout(table)


def _write_asset_lock_output(table: pa.Table) -> None:
    _write_arrow_stdout(table)


def _write_arrow_stdout(table: pa.Table) -> None:
    isatty = getattr(sys.stdout, "isatty", None)
    if isatty is not None and isatty():
        write_terminal_table(table, sys.stdout)
    else:
        write_stream(table, sys.stdout.buffer)


def _write_parquet_stdout(
    path: Path,
    *,
    batch_size: int = DEFAULT_STREAM_BATCH_SIZE,
    transform: Callable[[pa.Table], pa.Table] | None = None,
) -> None:
    isatty = getattr(sys.stdout, "isatty", None)
    if isatty is not None and isatty():
        write_parquet_terminal_table(
            path,
            sys.stdout,
            batch_size=batch_size,
            transform=transform,
        )
    else:
        write_parquet_stream(path, sys.stdout.buffer, batch_size=batch_size, transform=transform)


def _write_items_transform(transform: Callable[[pa.Table], pa.Table]) -> None:
    isatty = getattr(sys.stdout, "isatty", None)
    if isatty is not None and isatty():
        _write_items_output(transform(_read_items_stream()))
    else:
        write_transformed_stream(sys.stdin.buffer, sys.stdout.buffer, transform)


def _write_text_output(text: str) -> None:
    print(text, end="")


def _byte_size(value: str) -> int:
    text = value.strip()
    if not text:
        raise argparse.ArgumentTypeError("size must not be empty")

    units = {
        "b": 1,
        "kb": 1000,
        "mb": 1000**2,
        "gb": 1000**3,
        "tb": 1000**4,
        "kib": 1024,
        "mib": 1024**2,
        "gib": 1024**3,
        "tib": 1024**4,
    }
    lower = text.lower()
    for unit, multiplier in sorted(units.items(), key=lambda item: len(item[0]), reverse=True):
        if lower.endswith(unit):
            number = text[: -len(unit)].strip()
            break
    else:
        number = text
        multiplier = 1

    try:
        size = int(float(number) * multiplier)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid byte size: {value}") from error
    if size < 1:
        raise argparse.ArgumentTypeError("size must be at least 1 byte")
    return size


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid integer: {value}") from error
    if number < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return number


def _asset_lock_path(value: str) -> Path:
    if value == "-":
        raise argparse.ArgumentTypeError(
            "--asset-lock expects a path to an Arrow IPC stream, not stdin"
        )
    return Path(value)


def _items_from_json(args: argparse.Namespace) -> int:
    table = _read_stac_json_input(args.input_file)
    transform = _item_filter_transform(args)
    if transform is not None:
        table = transform(table)
    table = items_table_to_geoparquet_table(table)
    _write_items_output(table)
    return 0


def _items_from_ndjson(args: argparse.Namespace) -> int:
    transform = _item_filter_transform(args)
    isatty = getattr(sys.stdout, "isatty", None)
    if isatty is not None and isatty():
        table = items_table_to_geoparquet_table(_read_stac_ndjson_input(args.input_file))
        if transform is not None:
            table = transform(table)
        _write_items_output(table)
        return 0

    if _is_stdin_path(args.input_file):
        write_stac_ndjson_stream(
            sys.stdin,
            sys.stdout.buffer,
            batch_size=args.batch_size,
            transform=transform,
        )
        return 0

    assert args.input_file is not None
    with args.input_file.open("r", encoding="utf-8") as source:
        write_stac_ndjson_stream(
            source,
            sys.stdout.buffer,
            batch_size=args.batch_size,
            transform=transform,
            source_href=str(args.input_file),
        )
    return 0


def _items_from_parquet(args: argparse.Namespace) -> int:
    _write_parquet_stdout(
        args.input_file,
        batch_size=args.batch_size,
        transform=_item_filter_transform(args),
    )
    return 0


def _items_to_parquet(args: argparse.Namespace) -> int:
    write_items_geoparquet_stream(sys.stdin.buffer, args.output_file)
    return 0


def _items_promote_alternate(args: argparse.Namespace) -> int:
    if args.drop_alternates and args.mode != "replace":
        _parser_error(args, "--drop-alternates is only used with --mode replace")
    _write_items_transform(
        lambda table: promote_alternate_asset_hrefs(
            table,
            alternate_key=args.alternate_key,
            mode=args.mode,
            drop_alternates=args.drop_alternates,
            switched_alternate_name=args.switched_alternate_name,
        )
    )
    return 0


def _items_remove_alternate(args: argparse.Namespace) -> int:
    _write_items_transform(
        lambda table: remove_alternate_asset_hrefs(
            table,
            alternate_key=args.alternate_key,
        )
    )
    return 0


def _items_add_alternate(args: argparse.Namespace) -> int:
    asset_lock = _read_asset_lock_path(args.asset_lock)
    _write_items_transform(
        lambda table: add_alternate_asset_hrefs(
            table,
            asset_lock,
            alternate_key=args.alternate_key,
            alternate_name=args.alternate_name or args.alternate_key,
        )
    )
    return 0


def _parser_error(args: argparse.Namespace, message: str) -> None:
    parser = getattr(args, "_parser", None)
    if isinstance(parser, argparse.ArgumentParser):
        parser.error(message)
    raise ValueError(message)


def _items_enrich(args: argparse.Namespace) -> int:
    asset_lock = _read_asset_lock_path(args.asset_lock)
    _write_items_transform(
        lambda table: enrich_items(
            table,
            asset_lock,
            alternate_key=args.alternate_key,
        )
    )
    return 0


def _asset_lock_derive(args: argparse.Namespace) -> int:
    table = _read_items_stream()
    asset_lock = derive_asset_lock(
        table,
        probe_metadata=args.probe_metadata,
        item_ids=_filter_set(args.item_ids),
        providers=_filter_set(args.providers),
        asset_keys=_filter_set(args.asset_keys),
        include_metadata_assets=args.include_metadata_assets,
        keep_going=args.keep_going,
        max_workers=args.max_workers,
    )
    _write_asset_lock_output(asset_lock)
    return 0


def _asset_lock_from_parquet(args: argparse.Namespace) -> int:
    _write_parquet_stdout(args.input_file, batch_size=args.batch_size)
    return 0


def _asset_lock_to_parquet(args: argparse.Namespace) -> int:
    write_asset_lock_parquet_stream(sys.stdin.buffer, args.output_file)
    return 0


def _filter_set(values: list[str] | None) -> set[str] | None:
    return set(values) if values else None


def _item_filter_transform(args: argparse.Namespace) -> Callable[[pa.Table], pa.Table] | None:
    item_ids = _filter_set(getattr(args, "item_ids", None))
    collections = _filter_set(getattr(args, "collections", None))
    providers = _filter_set(getattr(args, "providers", None))
    if not (item_ids or collections or providers):
        return None
    return lambda table: filter_items(
        table,
        item_ids=item_ids,
        collections=collections,
        providers=providers,
    )


def _asset_lock_validate(args: argparse.Namespace) -> int:
    results = validate_assets(
        _read_asset_lock_stream(),
        item_ids=_filter_set(args.item_ids),
        asset_keys=_filter_set(args.asset_keys),
        keep_going=args.keep_going,
        max_workers=args.max_workers,
    )
    text = "".join(f"{json.dumps(result, sort_keys=True, default=str)}\n" for result in results)
    _write_text_output(text)
    return 0 if all(result["valid"] for result in results) else 1


def _asset_lock_relocate(args: argparse.Namespace) -> int:
    source_assets = _read_asset_lock_stream()
    if args.destination_lock is not None:
        if _relocation_destination_options_supplied(args):
            _parser_error(
                args,
                "--source-prefix, --store-container, --store-endpoint-url, --key, "
                "and --layout are only used with --store-type",
            )
        target_assets = read_parquet(args.destination_lock)
    else:
        try:
            target_assets = relocate_asset_locations(
                source_assets,
                source_prefix=args.source_prefix,
                store_type=args.store_type,
                store_container=args.store_container,
                store_endpoint_url=args.store_endpoint_url,
                key=args.key,
                layout=args.layout,
            )
        except ValueError as error:
            _parser_error(args, str(error))
    if args.dry_run:
        _write_asset_lock_output(target_assets)
        return 0
    copied = copy_assets(
        source_assets,
        target_assets,
        overwrite=args.overwrite,
        keep_going=args.keep_going,
        max_workers=args.max_workers,
        memory_limit_bytes=args.memory_limit_bytes,
        chunk_size_bytes=args.chunk_size_bytes,
        put_max_concurrency=args.put_max_concurrency,
    )
    _write_asset_lock_output(copied)
    return 0


def _relocation_destination_options_supplied(args: argparse.Namespace) -> bool:
    return bool(
        getattr(args, "source_prefix", None)
        or getattr(args, "store_container", None)
        or getattr(args, "store_endpoint_url", None)
        or getattr(args, "key", None)
        or getattr(args, "layout", "item-asset") != "item-asset"
    )


def _build(args: argparse.Namespace) -> int:
    items = _read_items_stream()
    build_package(
        items,
        args.output_dir,
        asset_lock=args.asset_lock,
        includes=args.includes,
        include_assets=args.include_assets,
        probe_metadata=args.probe_metadata,
        include_metadata_assets=args.include_metadata_assets,
        item_ids=_filter_set(args.item_ids),
        providers=_filter_set(args.providers),
    )
    return 0


def _inspect(args: argparse.Namespace) -> int:
    if args.format == "json":
        text = json.dumps(package_inspect_data(args.package), indent=2, sort_keys=True)
        text = f"{text}\n"
    elif args.format in {"markdown", "md"}:
        text = package_inspect_markdown(args.package)
    else:
        text = package_inspect_yaml(args.package)
    _write_text_output(text)
    return 0


def _push(args: argparse.Namespace) -> int:
    push_package(
        args.package,
        args.target,
        plain_http=args.plain_http,
        insecure=args.insecure,
    )
    return 0


def _pull(args: argparse.Namespace) -> int:
    pull_package(
        args.source,
        args.output_dir,
        plain_http=args.plain_http,
        insecure=args.insecure,
    )
    return 0


def _add_command(
    subcommands: argparse._SubParsersAction,
    name: str,
    description: str,
    *,
    library_methods: Sequence[str] = (),
) -> argparse.ArgumentParser:
    epilog = None
    if library_methods:
        label = "Library method" if len(library_methods) == 1 else "Library methods"
        epilog = f"{label}: {', '.join(library_methods)}"
    return subcommands.add_parser(name, help=description, description=description, epilog=epilog)


def _add_item_filter_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--collections",
        dest="collections",
        action="append",
        metavar="COLLECTION",
        help="collection id to keep; repeat for multiple collection ids",
    )
    command.add_argument(
        "--providers",
        dest="providers",
        action="append",
        metavar="PROVIDER",
        help="provider name to keep; repeat for multiple provider names",
    )
    command.add_argument(
        "--item-ids",
        dest="item_ids",
        action="append",
        metavar="ITEM_ID",
        help="item id to keep; repeat for multiple item ids",
    )


def _add_items_commands(subcommands: argparse._SubParsersAction) -> None:
    items = _add_command(subcommands, "items", "Work with STAC items table commands.")
    item_commands = items.add_subparsers(dest="items_command", required=True)

    from_json = _add_command(
        item_commands,
        "from-json",
        "Convert STAC JSON into items Arrow streams.",
        library_methods=(
            "read_stac_json",
            "filter_items",
            "items_table_to_geoparquet_table",
        ),
    )
    from_json.add_argument(
        "input_file", nargs="?", type=Path, help="STAC JSON path; omit for stdin"
    )
    _add_item_filter_arguments(from_json)
    from_json.set_defaults(func=_items_from_json)

    from_ndjson = _add_command(
        item_commands,
        "from-ndjson",
        "Convert STAC NDJSON into items Arrow streams.",
        library_methods=("write_stac_ndjson_stream", "filter_items"),
    )
    from_ndjson.add_argument(
        "input_file", nargs="?", type=Path, help="STAC NDJSON path; omit for stdin"
    )
    from_ndjson.add_argument(
        "--batch-size",
        type=_positive_int,
        default=DEFAULT_STREAM_BATCH_SIZE,
        help="maximum rows per IPC batch while reading STAC items NDJSON",
    )
    _add_item_filter_arguments(from_ndjson)
    from_ndjson.set_defaults(func=_items_from_ndjson)

    from_parquet = _add_command(
        item_commands,
        "from-parquet",
        "Read STAC GeoParquet as Arrow IPC.",
        library_methods=("write_parquet_stream", "filter_items"),
    )
    from_parquet.add_argument("input_file", type=Path, help="STAC GeoParquet input path")
    from_parquet.add_argument(
        "--batch-size",
        type=_positive_int,
        default=DEFAULT_STREAM_BATCH_SIZE,
        help="maximum rows per IPC batch while reading Parquet",
    )
    _add_item_filter_arguments(from_parquet)
    from_parquet.set_defaults(func=_items_from_parquet)

    to_parquet = _add_command(
        item_commands,
        "to-parquet",
        "Write items streams as STAC GeoParquet.",
        library_methods=("write_items_geoparquet_stream",),
    )
    to_parquet.add_argument("output_file", type=Path, help="STAC GeoParquet output path")
    to_parquet.set_defaults(func=_items_to_parquet)

    promote_alternate = _add_command(
        item_commands,
        "promote-alternate",
        "Promote alternate asset hrefs in items streams.",
        library_methods=("promote_alternate_asset_hrefs",),
    )
    promote_alternate.add_argument(
        "--alternate-key",
        "--key",
        dest="alternate_key",
        required=True,
        help="alternate asset map key to promote",
    )
    promote_alternate.add_argument(
        "--mode",
        choices=PROMOTE_ALTERNATE_MODES,
        default="replace",
        help="promotion mode to apply",
    )
    promote_alternate.add_argument(
        "--switched-alternate-name",
        default="original",
        help="alternate:name to use for the demoted primary href in switch mode",
    )
    promote_alternate.add_argument(
        "--drop-alternates",
        action="store_true",
        help="remove the alternate asset map after replace promotion",
    )
    promote_alternate.set_defaults(func=_items_promote_alternate, _parser=promote_alternate)

    remove_alternate = _add_command(
        item_commands,
        "remove-alternate",
        "Remove alternate asset hrefs in items streams.",
        library_methods=("remove_alternate_asset_hrefs",),
    )
    remove_alternate.add_argument(
        "--alternate-key",
        "--key",
        dest="alternate_key",
        required=True,
        help="alternate asset map key to remove",
    )
    remove_alternate.set_defaults(func=_items_remove_alternate, _parser=remove_alternate)

    add_alternate = _add_command(
        item_commands,
        "add-alternate",
        "Add alternate asset hrefs in items streams.",
        library_methods=("add_alternate_asset_hrefs",),
    )
    add_alternate.add_argument(
        "--asset-lock",
        required=True,
        type=_asset_lock_path,
        help="asset-lock Arrow IPC stream path with alternate hrefs to add",
    )
    add_alternate.add_argument(
        "--alternate-key",
        "--key",
        dest="alternate_key",
        required=True,
        help="alternate asset map key to write",
    )
    add_alternate.add_argument(
        "--alternate-name",
        "--name",
        dest="alternate_name",
        help="alternate:name to write; defaults to --alternate-key",
    )
    add_alternate.set_defaults(func=_items_add_alternate, _parser=add_alternate)

    enrich = _add_command(
        item_commands,
        "enrich",
        "Write asset lock facts into items.",
        library_methods=("enrich_items",),
    )
    enrich.add_argument(
        "--asset-lock",
        required=True,
        type=_asset_lock_path,
        help="asset-lock Arrow IPC stream path",
    )
    enrich.add_argument(
        "--alternate-key",
        help="alternate asset map key to write when adding reconstructed lock hrefs",
    )
    enrich.set_defaults(func=_items_enrich, _parser=enrich)


def _add_asset_lock_commands(subcommands: argparse._SubParsersAction) -> None:
    asset_lock = _add_command(subcommands, "asset-lock", "Work with asset-lock table commands.")
    asset_commands = asset_lock.add_subparsers(dest="asset_lock_command", required=True)

    derive = _add_command(
        asset_commands,
        "derive",
        "Derive asset lock rows from items streams.",
        library_methods=("derive_asset_lock",),
    )
    derive.add_argument(
        "--probe-metadata",
        default=DEFAULT_PROBE_METADATA,
        action=argparse.BooleanOptionalAction,
        help="query referenced objects for current metadata while deriving rows",
    )
    derive.add_argument(
        "--item-ids",
        dest="item_ids",
        action="append",
        metavar="ITEM_ID",
        help="item id to keep; repeat for multiple item ids",
    )
    derive.add_argument(
        "--providers",
        dest="providers",
        action="append",
        metavar="PROVIDER",
        help="provider name to keep; repeat for multiple provider names",
    )
    derive.add_argument(
        "--asset-keys",
        dest="asset_keys",
        action="append",
        metavar="ASSET_KEY",
        help="asset key to keep; repeat for multiple asset keys",
    )
    derive.add_argument(
        "--include-metadata-assets",
        action="store_true",
        help="include assets whose asset key is metadata",
    )
    derive.add_argument(
        "--keep-going",
        action="store_true",
        help="keep rows after recoverable metadata errors",
    )
    derive.add_argument(
        "--max-workers",
        type=int,
        default=DEFAULT_COPY_MAX_WORKERS,
        help="maximum concurrent object metadata requests",
    )
    derive.set_defaults(func=_asset_lock_derive)

    from_parquet = _add_command(
        asset_commands,
        "from-parquet",
        "Read asset-lock Parquet as Arrow IPC.",
        library_methods=("write_parquet_stream",),
    )
    from_parquet.add_argument("input_file", type=Path, help="asset-lock Parquet input path")
    from_parquet.add_argument(
        "--batch-size",
        type=_positive_int,
        default=DEFAULT_STREAM_BATCH_SIZE,
        help="maximum rows per IPC batch while reading Parquet",
    )
    from_parquet.set_defaults(func=_asset_lock_from_parquet)

    to_parquet = _add_command(
        asset_commands,
        "to-parquet",
        "Write asset-lock streams as Parquet.",
        library_methods=("write_asset_lock_parquet_stream",),
    )
    to_parquet.add_argument("output_file", type=Path, help="asset-lock Parquet output path")
    to_parquet.set_defaults(func=_asset_lock_to_parquet)

    validate = _add_command(
        asset_commands,
        "validate",
        "Validate current assets against locked facts.",
        library_methods=("validate_assets",),
    )
    validate.add_argument(
        "--item-ids",
        dest="item_ids",
        action="append",
        metavar="ITEM_ID",
        help="item id to validate; repeat for multiple item ids",
    )
    validate.add_argument(
        "--asset-keys",
        dest="asset_keys",
        action="append",
        metavar="ASSET_KEY",
        help="asset key to validate; repeat for multiple asset keys",
    )
    validate.add_argument(
        "--keep-going",
        action="store_true",
        help="emit invalid rows after recoverable validation errors",
    )
    validate.add_argument(
        "--max-workers",
        type=int,
        default=DEFAULT_COPY_MAX_WORKERS,
        help="maximum concurrent object metadata requests",
    )
    validate.set_defaults(func=_asset_lock_validate)

    relocate = _add_command(
        asset_commands,
        "relocate",
        "Plan or relocate asset bytes into lock locations.",
        library_methods=("relocate_asset_locations", "copy_assets"),
    )
    relocate_destination = relocate.add_mutually_exclusive_group(required=True)
    relocate_destination.add_argument(
        "--destination-lock",
        type=Path,
        help="destination asset-lock Parquet path",
    )
    relocate_destination.add_argument(
        "--store-type",
        choices=("file", "s3", "gs", "az", "http", "https"),
        help="destination obstore storage type",
    )
    relocate.add_argument(
        "--store-container",
        help="destination bucket, container, or HTTP origin",
    )
    relocate.add_argument(
        "--store-endpoint-url",
        help="destination object-store endpoint URL",
    )
    relocate.add_argument(
        "--key",
        help="destination key or path prefix",
    )
    relocate.add_argument(
        "--source-prefix",
        dest="source_prefix",
        help="only map asset hrefs at or below this source href prefix",
    )
    relocate.add_argument(
        "--layout",
        choices=["item-asset", "source-key"],
        default="item-asset",
        help="destination key layout for relocated rows",
    )
    relocate.add_argument(
        "--dry-run",
        action="store_true",
        help="write planned destination lock rows without copying asset bytes",
    )
    relocate.add_argument(
        "--overwrite",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="overwrite existing destination objects",
    )
    relocate.add_argument(
        "--keep-going",
        action="store_true",
        help="keep rows after recoverable relocation errors",
    )
    relocate.add_argument(
        "--max-workers",
        type=int,
        default=DEFAULT_COPY_MAX_WORKERS,
        help="maximum concurrent relocation tasks",
    )
    relocate.add_argument(
        "--memory-limit-bytes",
        dest="memory_limit_bytes",
        type=_byte_size,
        default=DEFAULT_COPY_MEMORY_LIMIT_BYTES,
        help="maximum reserved streaming relocation memory, e.g. 2GiB or 512MiB",
    )
    relocate.add_argument(
        "--chunk-size-bytes",
        dest="chunk_size_bytes",
        type=_byte_size,
        default=DEFAULT_COPY_CHUNK_SIZE_BYTES,
        help="streaming relocation chunk size, e.g. 8MiB",
    )
    relocate.add_argument(
        "--put-max-concurrency",
        type=int,
        default=DEFAULT_COPY_PUT_MAX_CONCURRENCY,
        help="maximum concurrent multipart puts per relocation task",
    )
    relocate.set_defaults(func=_asset_lock_relocate, _parser=relocate)


def _add_package_commands(subcommands: argparse._SubParsersAction) -> None:
    build = _add_command(
        subcommands,
        "build",
        "Build a package directory from items.",
        library_methods=("build_package",),
    )
    build.add_argument("output_dir", type=Path, help="package directory to create")
    build.add_argument(
        "--asset-lock",
        type=_asset_lock_path,
        help="asset-lock Arrow IPC stream path",
    )
    build.add_argument(
        "--includes",
        dest="includes",
        action="append",
        default=[],
        type=Path,
        help="file or directory to include; repeat for multiple paths",
    )
    build.add_argument(
        "--include-assets",
        action="store_true",
        help="copy referenced asset bytes into the package directory",
    )
    build.add_argument(
        "--include-metadata-assets",
        action="store_true",
        help="include assets whose asset key is metadata",
    )
    build.add_argument(
        "--item-ids",
        dest="item_ids",
        action="append",
        metavar="ITEM_ID",
        help="item id to package; repeat for multiple item ids",
    )
    build.add_argument(
        "--providers",
        dest="providers",
        action="append",
        metavar="PROVIDER",
        help="provider name to package; repeat for multiple provider names",
    )
    build.add_argument(
        "--probe-metadata",
        default=DEFAULT_PROBE_METADATA,
        action=argparse.BooleanOptionalAction,
        help="query referenced objects for current metadata when deriving package rows",
    )
    build.set_defaults(func=_build)

    inspect = _add_command(
        subcommands,
        "inspect",
        "Inspect package contents as YAML, JSON, or Markdown.",
        library_methods=(
            "package_inspect_data",
            "package_inspect_markdown",
            "package_inspect_yaml",
        ),
    )
    inspect.add_argument("package", type=Path, help="package directory to inspect")
    inspect.add_argument(
        "--format",
        choices=["yaml", "json", "markdown", "md"],
        default="yaml",
        help="summary output format",
    )
    inspect.set_defaults(func=_inspect)

    push = _add_command(
        subcommands,
        "push",
        "Push package artifacts to an OCI registry.",
        library_methods=("push_package",),
    )
    push.add_argument("package", type=Path, help="package directory to push")
    push.add_argument("target", help="OCI registry target reference")
    push.add_argument("--plain-http", action="store_true", help="use plain HTTP for the registry")
    push.add_argument("--insecure", action="store_true", help="disable TLS certificate checks")
    push.set_defaults(func=_push)

    pull = _add_command(
        subcommands,
        "pull",
        "Pull package artifacts from an OCI registry.",
        library_methods=("pull_package",),
    )
    pull.add_argument("source", help="OCI registry source reference")
    pull.add_argument("--output-dir", required=True, type=Path, help="package directory to write")
    pull.add_argument("--plain-http", action="store_true", help="use plain HTTP for the registry")
    pull.add_argument("--insecure", action="store_true", help="disable TLS certificate checks")
    pull.set_defaults(func=_pull)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stacpkg",
        description="Package STAC metadata and external asset references.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    _add_package_commands(subcommands)
    _add_items_commands(subcommands)
    _add_asset_lock_commands(subcommands)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
