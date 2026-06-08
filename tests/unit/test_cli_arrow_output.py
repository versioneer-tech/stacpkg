# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from io import BytesIO
from io import StringIO
from types import SimpleNamespace

import pyarrow as pa

from stacpkg.arrow_io import (
    format_table,
    read_parquet,
    read_stream,
    write_parquet,
    write_stream,
    write_stream_to_parquet,
)
from stacpkg.cli import _write_asset_lock_output, _write_items_output, build_parser
from stacpkg.geoparquet import stac_items_to_geoparquet_table
from stacpkg.schemas import (
    ASSET_LOCK_COLUMNS,
    ASSET_LOCK_FIELDS,
    ASSET_LOCK_SCHEMA_VERSION,
    SCHEMA_KIND_KEY,
    SCHEMA_VERSION_KEY,
    SchemaKind,
    asset_lock_schema,
)


class _PipeStdout(StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.buffer = BytesIO()

    def isatty(self) -> bool:
        return False


class _TtyStdout(StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.buffer = BytesIO()

    def isatty(self) -> bool:
        return True


def _fail_materialized_parquet(*_args, **_kwargs):
    raise AssertionError("parquet was materialized")


def _asset_lock_table(
    *,
    asset_key: str = "thumbnail",
    key: str = "item-1/thumbnail.png",
) -> pa.Table:
    return pa.Table.from_pylist(
        [
            {
                "item_id": "item-1",
                "asset_key": asset_key,
                "store_type": "s3",
                "store_container": "bucket",
                "store_endpoint_url": None,
                "key": key,
                "size_bytes": 12,
                "etag": '"abc"',
                "last_modified": None,
            }
        ],
        schema=asset_lock_schema(),
    )


def _item_table(*, alternate: bool = False) -> pa.Table:
    asset = {"href": "file:///source/item-1/image.tif", "type": "image/tiff"}
    if alternate:
        asset["alternate"] = {
            "s3": {
                "href": "s3://bucket/item-1/image.tif",
                "alternate:name": "s3",
            }
        }
    return stac_items_to_geoparquet_table(
        [
            {
                "type": "Feature",
                "stac_version": "1.1.0",
                "id": "item-1",
                "collection": "example",
                "geometry": {"type": "Point", "coordinates": [16.0, 48.0]},
                "bbox": [16.0, 48.0, 16.0, 48.0],
                "properties": {
                    "datetime": "2026-01-01T00:00:00Z",
                    "providers": [{"name": "Provider A"}],
                },
                "links": [],
                "assets": {"image": asset},
            }
        ]
    )


def _filterable_stac_items() -> list[dict[str, object]]:
    items = []
    for item_id, collection, provider in (
        ("item-1", "example", "Provider A"),
        ("item-2", "alternate", "Provider A"),
        ("item-3", "example", "Provider B"),
    ):
        items.append(
            {
                "type": "Feature",
                "stac_version": "1.1.0",
                "id": item_id,
                "collection": collection,
                "geometry": {"type": "Point", "coordinates": [16.0, 48.0]},
                "bbox": [16.0, 48.0, 16.0, 48.0],
                "properties": {
                    "datetime": "2026-01-01T00:00:00Z",
                    "providers": [{"name": provider}],
                },
                "links": [],
                "assets": {
                    "image": {
                        "href": f"file:///source/{item_id}/image.tif",
                        "type": "image/tiff",
                    }
                },
            }
        )
    return items


def _read_pipe(stdout: _PipeStdout) -> pa.Table:
    stdout.buffer.seek(0)
    return read_stream(stdout.buffer)


def _write_stream_file(path, table: pa.Table) -> None:
    with path.open("wb") as handle:
        write_stream(table, handle)


def _run_item_stream_command(monkeypatch, table: pa.Table, argv: list[str]) -> pa.Table:
    stdin = BytesIO()
    write_stream(table, stdin)
    stdin.seek(0)
    stdout = _PipeStdout()
    monkeypatch.setattr("stacpkg.cli.sys.stdin", SimpleNamespace(buffer=stdin))
    monkeypatch.setattr("stacpkg.cli.sys.stdout", stdout)
    monkeypatch.setattr(
        "stacpkg.cli._read_items_stream",
        lambda: (_ for _ in ()).throw(AssertionError("stream was materialized")),
    )

    args = build_parser().parse_args(argv)

    assert args.func(args) == 0
    return _read_pipe(stdout)


def test_format_table_renders_arrow_table_as_shell_text() -> None:
    table = pa.table({"item_id": ["item-1"], "size_bytes": [12]})

    text = format_table(table, max_width=80)

    assert "item_id" in text
    assert "size_bytes" in text
    assert "item-1" in text
    assert "12" in text
    assert "1 rows x 2 columns" in text


def test_cli_arrow_stdout_stays_ipc_stream_for_pipes(monkeypatch) -> None:
    table = _asset_lock_table()
    stdout = _PipeStdout()
    monkeypatch.setattr("stacpkg.cli.sys.stdout", stdout)

    _write_asset_lock_output(table)

    stdout.buffer.seek(0)
    restored = read_stream(stdout.buffer)
    assert stdout.getvalue() == ""
    assert restored.schema == table.schema
    assert restored.to_pylist() == table.to_pylist()


def test_cli_arrow_stdout_pretty_prints_for_tty(monkeypatch) -> None:
    table = _asset_lock_table()
    stdout = _TtyStdout()
    monkeypatch.setattr("stacpkg.cli.sys.stdout", stdout)

    _write_asset_lock_output(table)

    text = stdout.getvalue()
    assert stdout.buffer.getvalue() == b""
    assert "item_id" in text
    assert "asset_key" in text
    assert "item-1" in text
    assert "thumbnail" in text
    assert "1 rows x 9 columns" in text


def test_cli_item_stdout_pretty_prints_for_tty(monkeypatch) -> None:
    table = pa.table({"id": ["item-1"], "collection": ["example"]})
    stdout = _TtyStdout()
    monkeypatch.setattr("stacpkg.cli.sys.stdout", stdout)

    _write_items_output(table)

    text = stdout.getvalue()
    assert stdout.buffer.getvalue() == b""
    assert "id" in text
    assert "collection" in text
    assert "item-1" in text
    assert "example" in text
    assert "1 rows x 2 columns" in text


def test_asset_lock_from_parquet_command_streams_parquet_batches(tmp_path, monkeypatch) -> None:
    table = pa.Table.from_pylist(
        _asset_lock_table().to_pylist() * 3,
        schema=asset_lock_schema(),
    )
    source = tmp_path / "source.assets.lock.parquet"
    write_parquet(table, source)
    stdout = _PipeStdout()
    monkeypatch.setattr("stacpkg.cli.sys.stdout", stdout)
    monkeypatch.setattr("stacpkg.cli.read_parquet", _fail_materialized_parquet)

    args = build_parser().parse_args(
        ["asset-lock", "from-parquet", str(source), "--batch-size", "1"]
    )

    assert args.func(args) == 0
    stdout.buffer.seek(0)
    with pa.ipc.open_stream(stdout.buffer) as reader:
        batches = list(reader)
    assert [batch.num_rows for batch in batches] == [1, 1, 1]


def test_asset_lock_from_parquet_tty_uses_bounded_preview(tmp_path, monkeypatch) -> None:
    table = pa.Table.from_pylist(
        _asset_lock_table().to_pylist() * 25,
        schema=asset_lock_schema(),
    )
    source = tmp_path / "source.assets.lock.parquet"
    write_parquet(table, source)
    stdout = _TtyStdout()
    monkeypatch.setattr("stacpkg.cli.sys.stdout", stdout)
    monkeypatch.setattr("stacpkg.cli.read_parquet", _fail_materialized_parquet)

    args = build_parser().parse_args(
        ["asset-lock", "from-parquet", str(source), "--batch-size", "3"]
    )

    assert args.func(args) == 0
    assert stdout.buffer.getvalue() == b""
    assert "25 rows x 9 columns (showing first 20)" in stdout.getvalue()


def test_items_from_parquet_command_filters_item_rows(tmp_path, monkeypatch) -> None:
    table = stac_items_to_geoparquet_table(_filterable_stac_items())
    source = tmp_path / "source.items.parquet"
    write_parquet(table, source)
    monkeypatch.setattr("stacpkg.cli.read_parquet", _fail_materialized_parquet)

    cases = (
        (["--collections", "example"], ["item-1", "item-3"]),
        (["--providers", "Provider A"], ["item-1", "item-2"]),
        (["--item-ids", "item-3"], ["item-3"]),
    )
    for options, expected_ids in cases:
        stdout = _PipeStdout()
        monkeypatch.setattr("stacpkg.cli.sys.stdout", stdout)

        args = build_parser().parse_args(
            ["items", "from-parquet", str(source), "--batch-size", "1", *options]
        )

        assert args.func(args) == 0
        restored = _read_pipe(stdout)
        assert [row["id"] for row in restored.to_pylist()] == expected_ids
        assert restored.schema.metadata[b"stac_geoparquet:version"] == b"1.0.0"


def test_items_from_json_command_filters_item_rows(tmp_path, monkeypatch) -> None:
    source = tmp_path / "items.json"
    source.write_text(
        json.dumps({"type": "FeatureCollection", "features": _filterable_stac_items()}),
        encoding="utf-8",
    )

    cases = (
        (["--collections", "example"], ["item-1", "item-3"]),
        (["--providers", "Provider A"], ["item-1", "item-2"]),
        (["--item-ids", "item-3"], ["item-3"]),
    )
    for options, expected_ids in cases:
        stdout = _PipeStdout()
        monkeypatch.setattr("stacpkg.cli.sys.stdout", stdout)

        args = build_parser().parse_args(["items", "from-json", str(source), *options])

        assert args.func(args) == 0
        restored = _read_pipe(stdout)
        assert [row["id"] for row in restored.to_pylist()] == expected_ids
        assert restored.schema.metadata[b"stac_geoparquet:version"] == b"1.0.0"


def test_items_from_ndjson_command_streams_item_batches(tmp_path, monkeypatch) -> None:
    source = tmp_path / "items.ndjson"
    source.write_text(
        "\n".join(json.dumps(item) for item in _filterable_stac_items()) + "\n\n",
        encoding="utf-8",
    )
    stdout = _PipeStdout()
    monkeypatch.setattr("stacpkg.cli.sys.stdout", stdout)

    args = build_parser().parse_args(["items", "from-ndjson", str(source), "--batch-size", "1"])

    assert args.func(args) == 0
    stdout.buffer.seek(0)
    with pa.ipc.open_stream(stdout.buffer) as reader:
        batches = list(reader)
        schema = reader.schema
    restored = pa.Table.from_batches(batches, schema=schema)
    assert [batch.num_rows for batch in batches] == [1, 1, 1]
    assert [row["id"] for row in restored.to_pylist()] == ["item-1", "item-2", "item-3"]
    assert restored.schema.metadata[b"stac_geoparquet:version"] == b"1.0.0"
    assert "assets" in restored.schema.names
    assert "assets_json" not in restored.schema.names


def test_items_from_ndjson_command_filters_item_rows(tmp_path, monkeypatch) -> None:
    source = tmp_path / "items.ndjson"
    source.write_text(
        "\n".join(json.dumps(item) for item in _filterable_stac_items()),
        encoding="utf-8",
    )

    cases = (
        (["--collections", "example"], ["item-1", "item-3"]),
        (["--providers", "Provider A"], ["item-1", "item-2"]),
        (["--item-ids", "item-3"], ["item-3"]),
    )
    for options, expected_ids in cases:
        stdout = _PipeStdout()
        monkeypatch.setattr("stacpkg.cli.sys.stdout", stdout)

        args = build_parser().parse_args(["items", "from-ndjson", str(source), *options])

        assert args.func(args) == 0
        restored = _read_pipe(stdout)
        assert [row["id"] for row in restored.to_pylist()] == expected_ids
        assert restored.schema.metadata[b"stac_geoparquet:version"] == b"1.0.0"


def test_asset_lock_to_parquet_command_writes_parquet(tmp_path, monkeypatch) -> None:
    table = _asset_lock_table()
    output = tmp_path / "output.assets.lock.parquet"
    stdin = BytesIO()
    write_stream(table, stdin)
    stdin.seek(0)
    monkeypatch.setattr("stacpkg.cli.sys.stdin", SimpleNamespace(buffer=stdin))

    args = build_parser().parse_args(["asset-lock", "to-parquet", str(output)])

    assert args.func(args) == 0
    restored = read_parquet(output)
    assert restored.schema.names == table.schema.names
    assert restored.to_pylist() == table.to_pylist()


def test_asset_lock_to_parquet_command_normalizes_schema_contract(
    tmp_path,
    monkeypatch,
) -> None:
    table = pa.Table.from_pylist(
        [
            {
                "item_id": "item-1",
                "asset_key": "thumbnail",
                "store_type": "s3",
                "store_container": "bucket",
                "key": "item-1/thumbnail.png",
                "size_bytes": 12,
            }
        ]
    )
    output = tmp_path / "output.assets.lock.parquet"
    stdin = BytesIO()
    write_stream(table, stdin)
    stdin.seek(0)
    monkeypatch.setattr("stacpkg.cli.sys.stdin", SimpleNamespace(buffer=stdin))

    args = build_parser().parse_args(["asset-lock", "to-parquet", str(output)])

    assert args.func(args) == 0
    restored = read_parquet(output)
    assert restored.schema.metadata[SCHEMA_KIND_KEY] == SchemaKind.ASSET_LOCK.value.encode()
    assert restored.schema.metadata[SCHEMA_VERSION_KEY] == ASSET_LOCK_SCHEMA_VERSION.encode()
    assert restored.schema.names == list(ASSET_LOCK_COLUMNS)
    assert [(field.name, field.type, field.nullable) for field in restored.schema] == [
        (
            ASSET_LOCK_FIELDS[name].name,
            ASSET_LOCK_FIELDS[name].type,
            ASSET_LOCK_FIELDS[name].nullable,
        )
        for name in ASSET_LOCK_COLUMNS
    ]
    assert restored.to_pylist() == [
        {
            "item_id": "item-1",
            "asset_key": "thumbnail",
            "store_type": "s3",
            "store_container": "bucket",
            "store_endpoint_url": None,
            "key": "item-1/thumbnail.png",
            "size_bytes": 12,
            "etag": None,
            "last_modified": None,
        }
    ]


def test_write_stream_to_parquet_uses_record_batch_writer(tmp_path, monkeypatch) -> None:
    table = pa.Table.from_pylist(
        _asset_lock_table().to_pylist() * 2,
        schema=asset_lock_schema(),
    )
    source = BytesIO()
    with pa.ipc.new_stream(source, table.schema) as writer:
        writer.write_batch(table.slice(0, 1).to_batches()[0])
        writer.write_batch(table.slice(1, 1).to_batches()[0])
    source.seek(0)
    written_batch_sizes = []

    class RecordingParquetWriter:
        def __init__(self, path, schema) -> None:
            self.path = path
            self.schema = schema

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            return None

        def write_batch(self, batch: pa.RecordBatch) -> None:
            written_batch_sizes.append(batch.num_rows)

        def write_table(self, table: pa.Table) -> None:
            raise AssertionError("stream was materialized as a table")

    monkeypatch.setattr("stacpkg.arrow_io.pq.ParquetWriter", RecordingParquetWriter)

    write_stream_to_parquet(source, tmp_path / "output.assets.lock.parquet")

    assert written_batch_sizes == [1, 1]


def test_write_stream_to_parquet_writes_ipc_batches(tmp_path) -> None:
    table = pa.Table.from_pylist(
        _asset_lock_table().to_pylist() * 2,
        schema=asset_lock_schema(),
    )
    first = table.slice(0, 1).to_batches()[0]
    second = table.slice(1, 1).to_batches()[0]
    source = BytesIO()
    with pa.ipc.new_stream(source, table.schema) as writer:
        writer.write_batch(first)
        writer.write_batch(second)
    source.seek(0)

    output = tmp_path / "output.assets.lock.parquet"
    write_stream_to_parquet(source, output)

    restored = read_parquet(output)
    assert restored.schema.names == table.schema.names
    assert restored.to_pylist() == table.to_pylist()


def test_asset_lock_to_parquet_command_does_not_materialize_stream(
    tmp_path,
    monkeypatch,
) -> None:
    table = _asset_lock_table()
    output = tmp_path / "output.assets.lock.parquet"
    stdin = BytesIO()
    write_stream(table, stdin)
    stdin.seek(0)
    monkeypatch.setattr("stacpkg.cli.sys.stdin", SimpleNamespace(buffer=stdin))
    monkeypatch.setattr(
        "stacpkg.cli._read_asset_lock_stream",
        lambda: (_ for _ in ()).throw(AssertionError("stream was materialized")),
    )

    args = build_parser().parse_args(["asset-lock", "to-parquet", str(output)])

    assert args.func(args) == 0
    assert read_parquet(output).to_pylist() == table.to_pylist()


def test_items_to_parquet_command_does_not_materialize_stream(tmp_path, monkeypatch) -> None:
    table = _item_table()
    output = tmp_path / "output.items.parquet"
    stdin = BytesIO()
    write_stream(table, stdin)
    stdin.seek(0)
    monkeypatch.setattr("stacpkg.cli.sys.stdin", SimpleNamespace(buffer=stdin))
    monkeypatch.setattr(
        "stacpkg.cli._read_items_stream",
        lambda: (_ for _ in ()).throw(AssertionError("stream was materialized")),
    )

    args = build_parser().parse_args(["items", "to-parquet", str(output)])

    assert args.func(args) == 0
    restored = read_parquet(output)
    assert restored.schema.metadata[b"stac_geoparquet:version"] == b"1.0.0"
    assert restored.to_pylist() == table.to_pylist()


def test_items_promote_alternate_command_does_not_materialize_stream(monkeypatch) -> None:
    restored = _run_item_stream_command(
        monkeypatch,
        _item_table(alternate=True),
        ["items", "promote-alternate", "--alternate-key", "s3", "--mode", "switch"],
    )

    asset = restored.to_pylist()[0]["assets"]["image"]
    assert asset["href"] == "s3://bucket/item-1/image.tif"
    assert asset["alternate"]["s3"]["href"] == "file:///source/item-1/image.tif"


def test_items_remove_alternate_command_does_not_materialize_stream(monkeypatch) -> None:
    restored = _run_item_stream_command(
        monkeypatch,
        _item_table(alternate=True),
        ["items", "remove-alternate", "--alternate-key", "s3"],
    )

    asset = restored.to_pylist()[0]["assets"]["image"]
    assert asset["href"] == "file:///source/item-1/image.tif"
    assert "alternate" not in asset


def test_items_add_alternate_command_does_not_materialize_stream(tmp_path, monkeypatch) -> None:
    lock_path = tmp_path / "source.assets.lock.arrow"
    _write_stream_file(lock_path, _asset_lock_table(asset_key="image", key="item-1/image.tif"))

    restored = _run_item_stream_command(
        monkeypatch,
        _item_table(),
        [
            "items",
            "add-alternate",
            "--asset-lock",
            str(lock_path),
            "--alternate-key",
            "mirror",
            "--alternate-name",
            "Mirror copy",
        ],
    )

    asset = restored.to_pylist()[0]["assets"]["image"]
    assert asset["alternate"]["mirror"]["href"] == "s3://bucket/item-1/image.tif"
    assert asset["alternate"]["mirror"]["alternate:name"] == "Mirror copy"


def test_items_enrich_command_does_not_materialize_stream(tmp_path, monkeypatch) -> None:
    lock_path = tmp_path / "source.assets.lock.arrow"
    _write_stream_file(lock_path, _asset_lock_table(asset_key="image", key="item-1/image.tif"))

    restored = _run_item_stream_command(
        monkeypatch,
        _item_table(),
        [
            "items",
            "enrich",
            "--asset-lock",
            str(lock_path),
            "--alternate-key",
            "mirror",
        ],
    )

    asset = restored.to_pylist()[0]["assets"]["image"]
    assert asset["file:size"] == 12
    assert asset["alternate"]["mirror"]["href"] == "s3://bucket/item-1/image.tif"
