# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

import json
from io import BytesIO, StringIO
from types import SimpleNamespace

import pyarrow as pa
import pytest

from stacpkg import cli
from stacpkg.arrow_io import read_parquet, read_stream, write_parquet, write_stream
from stacpkg.assets import asset_lock_table
from stacpkg.geoparquet import stac_items_to_geoparquet_table
from openaerialmap_fixture import write_localized_openaerialmap_item_collection_json


class Output(StringIO):
    def __init__(self, terminal=False):
        super().__init__()
        self.buffer = BytesIO()
        self.terminal = terminal

    def isatty(self):
        return self.terminal


def _run(monkeypatch, arguments, table=None, *, terminal=False):
    if table is not None:
        source = BytesIO()
        write_stream(table, source)
        source.seek(0)
        monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(buffer=source))
    output = Output(terminal)
    monkeypatch.setattr(cli.sys, "stdout", output)
    assert cli.main(arguments) == 0
    output.buffer.seek(0)
    return output


def _stac_items():
    return [
        {
            "type": "Feature",
            "stac_version": "1.1.0",
            "id": item_id,
            "collection": collection,
            "geometry": {"type": "Point", "coordinates": [16, 48]},
            "bbox": [16, 48, 16, 48],
            "properties": {
                "datetime": "2026-01-01T00:00:00Z",
                "providers": [{"name": provider}],
            },
            "links": [],
            "assets": {"image": {"href": f"file:///source/{item_id}/image.tif"}},
        }
        for item_id, collection, provider in [
            ("scene-1", "aerial", "Provider A"),
            ("scene-2", "satellite", "Provider A"),
            ("scene-3", "aerial", "Provider B"),
        ]
    ]


def _items_with_alternate():
    item = _stac_items()[0]
    item["assets"]["image"]["alternate:name"] = "publisher"
    item["assets"]["image"]["alternate"] = {
        "mirror": {"href": "s3://bucket/scene-1/image.tif"},
        "archive": {"href": "s3://archive/scene-1/image.tif"},
    }
    return stac_items_to_geoparquet_table([item])


def _lock():
    return asset_lock_table(
        [
            {
                "item_id": "scene-1",
                "asset_key": "image",
                "href": "s3://bucket/scene-1/image.tif",
                "size_bytes": 12,
            }
        ]
    )


@pytest.mark.parametrize("format", ["json", "ndjson", "parquet"])
@pytest.mark.parametrize(
    ("options", "expected_ids"),
    [
        (["--collections", "aerial"], ["scene-1", "scene-3"]),
        (["--providers", "Provider A"], ["scene-1", "scene-2"]),
        (["--item-ids", "scene-3"], ["scene-3"]),
    ],
    ids=["collection", "provider", "item-id"],
)
def test_input_readers_keep_only_matching_items(
    tmp_path, monkeypatch, format, options, expected_ids
):
    source = tmp_path / f"selection.{format}"
    items = _stac_items()
    if format == "json":
        source.write_text(json.dumps({"type": "FeatureCollection", "features": items}))
    elif format == "ndjson":
        source.write_text("\n".join(json.dumps(item) for item in items))
    else:
        write_parquet(stac_items_to_geoparquet_table(items), source)

    output = _run(monkeypatch, ["items", f"from-{format}", str(source), *options])
    restored = read_stream(output.buffer)

    assert restored.column("id").to_pylist() == expected_ids
    assert "assets" in restored.schema.names
    assert b"geo" in restored.schema.metadata


def test_ndjson_reader_preserves_all_items_across_batches(tmp_path, monkeypatch):
    source = tmp_path / "selection.ndjson"
    source.write_text("\n".join(json.dumps(item) for item in _stac_items()))

    output = _run(monkeypatch, ["items", "from-ndjson", str(source), "--batch-size", "1"])
    batches = list(pa.ipc.open_stream(output.buffer))

    assert [batch.num_rows for batch in batches] == [1, 1, 1]
    assert [batch.column("id")[0].as_py() for batch in batches] == ["scene-1", "scene-2", "scene-3"]


@pytest.mark.parametrize("input_format", ["json", "ndjson"])
def test_cli_readers_keep_link_request_values_through_parquet(tmp_path, monkeypatch, input_format):
    source = write_localized_openaerialmap_item_collection_json(
        tmp_path, tmp_path / "selection.json", item_count=2
    )
    items = json.loads(source.read_text())["features"]
    expected = []
    for item in items:
        link = {
            "rel": "via",
            "href": "https://publisher.test/search",
            "method": "POST",
            "body": {"ids": [item["id"]]},
            "headers": {"Accept": "application/geo+json"},
            "custom:trace": item["id"],
        }
        item["links"] = [link]
        expected.append([{**link, "type": None, "title": None}])
    if input_format == "json":
        source.write_text(json.dumps({"type": "FeatureCollection", "features": items}))
    else:
        source = tmp_path / "selection.ndjson"
        source.write_text("\n".join(json.dumps(item) for item in items))
    options = ["--batch-size", "1"] if input_format == "ndjson" else []
    output = _run(monkeypatch, ["items", f"from-{input_format}", str(source), *options])
    table = read_stream(output.buffer)
    assert table.column("links").to_pylist() == expected

    path = tmp_path / "items.parquet"
    _run(monkeypatch, ["items", "to-parquet", str(path)], table)
    output = _run(monkeypatch, ["items", "from-parquet", str(path), "--batch-size", "1"])

    assert read_stream(output.buffer).column("links").to_pylist() == expected


@pytest.mark.parametrize("field", ["property", "asset", "link"])
def test_ndjson_rejects_new_fields_after_the_stream_schema_is_set(tmp_path, monkeypatch, field):
    items = _stac_items()[:2]
    if field == "property":
        items[1]["properties"]["processing:version"] = "xyz"
    elif field == "asset":
        items[1]["assets"]["thumbnail"] = {"href": "file:///source/thumbnail.png"}
    else:
        items[1]["links"] = [
            {"rel": "via", "href": "https://publisher.test/search", "method": "POST"}
        ]
    source = tmp_path / "selection.ndjson"
    source.write_text("\n".join(json.dumps(item) for item in items))

    with pytest.raises(ValueError, match="NDJSON schema changed"):
        _run(monkeypatch, ["items", "from-ndjson", str(source), "--batch-size", "1"])


@pytest.mark.parametrize("format", ["json", "ndjson"])
def test_json_readers_keep_properties_that_are_absent_from_the_first_item(
    tmp_path, monkeypatch, format
):
    items = _stac_items()[:2]
    items[1]["properties"]["processing:version"] = "xyz"
    source = tmp_path / f"selection.{format}"
    document = (
        json.dumps({"type": "FeatureCollection", "features": items})
        if format == "json"
        else "\n".join(json.dumps(item) for item in items)
    )
    source.write_text(document)

    output = _run(monkeypatch, ["items", f"from-{format}", str(source)])

    assert read_stream(output.buffer).column("processing:version").to_pylist() == [None, "xyz"]


def test_lock_parquet_reader_uses_the_requested_batch_size(tmp_path, monkeypatch):
    table = pa.concat_tables([_lock(), _lock(), _lock()])
    source = tmp_path / "lock.parquet"
    write_parquet(table, source)

    output = _run(monkeypatch, ["asset-lock", "from-parquet", str(source), "--batch-size", "1"])
    batches = list(pa.ipc.open_stream(output.buffer))

    assert [batch.num_rows for batch in batches] == [1, 1, 1]
    assert pa.Table.from_batches(batches).equals(table)


def test_terminal_output_is_a_short_text_preview(tmp_path, monkeypatch):
    source = tmp_path / "lock.parquet"
    write_parquet(pa.concat_tables([_lock()] * 25), source)

    output = _run(monkeypatch, ["asset-lock", "from-parquet", str(source)], terminal=True)

    assert output.buffer.getvalue() == b""
    assert "scene-1" in output.getvalue()
    assert "showing first 20" in output.getvalue()


def test_lock_writer_fills_unknown_facts_without_changing_known_values(tmp_path, monkeypatch):
    table = pa.table({"item_id": ["scene-1"], "asset_key": ["image"], "size_bytes": [12]})
    output = tmp_path / "lock.parquet"

    _run(monkeypatch, ["asset-lock", "to-parquet", str(output)], table)
    row = read_parquet(output).to_pylist()[0]

    assert row["item_id"] == "scene-1"
    assert row["size_bytes"] == 12
    assert row["file_checksum"] is None
    assert row["etag"] is None


def test_items_writer_preserves_asset_metadata(tmp_path, monkeypatch):
    items = _items_with_alternate()
    output = tmp_path / "items.parquet"

    _run(monkeypatch, ["items", "to-parquet", str(output)], items)

    assert read_parquet(output).to_pylist() == items.to_pylist()


def test_switching_an_alternate_keeps_the_previous_primary(monkeypatch):
    output = _run(
        monkeypatch,
        ["items", "promote-alternate", "--alternate-key", "mirror", "--mode", "switch"],
        _items_with_alternate(),
    )

    asset = read_stream(output.buffer).to_pylist()[0]["assets"]["image"]
    assert asset["href"] == "s3://bucket/scene-1/image.tif"
    assert asset["alternate"]["mirror"]["href"] == "file:///source/scene-1/image.tif"
    assert asset["alternate"]["mirror"]["alternate:name"] == "publisher"
    assert asset["alternate"]["archive"]["href"] == "s3://archive/scene-1/image.tif"


def test_removing_an_alternate_keeps_the_primary(monkeypatch):
    output = _run(
        monkeypatch,
        ["items", "remove-alternate", "--alternate-key", "mirror"],
        _items_with_alternate(),
    )

    asset = read_stream(output.buffer).to_pylist()[0]["assets"]["image"]
    assert asset["href"] == "file:///source/scene-1/image.tif"
    assert "mirror" not in asset["alternate"]
    assert asset["alternate"]["archive"]["href"] == "s3://archive/scene-1/image.tif"


@pytest.mark.parametrize("command", ["add-alternate", "enrich"])
def test_lock_facts_can_supply_an_alternate(tmp_path, monkeypatch, command):
    lock_file = tmp_path / "lock.arrow"
    with lock_file.open("wb") as output:
        write_stream(_lock(), output)
    items = stac_items_to_geoparquet_table([_stac_items()[0]])

    output = _run(
        monkeypatch,
        ["items", command, "--asset-lock", str(lock_file), "--alternate-key", "mirror"],
        items,
    )

    asset = read_stream(output.buffer).to_pylist()[0]["assets"]["image"]
    assert asset["href"] == "file:///source/scene-1/image.tif"
    assert asset["alternate"]["mirror"]["href"] == "s3://bucket/scene-1/image.tif"
    if command == "enrich":
        assert asset["file:size"] == 12
