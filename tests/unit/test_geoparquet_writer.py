# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from io import BytesIO

import pyarrow as pa
import pyarrow.parquet as pq

from stacpkg.arrow_io import write_stream
from stacpkg.assets import derive_asset_lock
from stacpkg.geoparquet import write_items_geoparquet, write_items_geoparquet_stream
from stacpkg.locators import href_from_location
from stacpkg.schemas import ASSET_LOCK_COLUMNS
from stacpkg.stac_json import read_stac_json
from openaerialmap_fixture import (
    LOCAL_OPENAERIALMAP_ASSET_KEYS,
    write_localized_openaerialmap_item_collection_json,
)


def _href(row: dict[str, object]) -> str:
    value = href_from_location(row)
    assert isinstance(value, str)
    return value


def test_write_items_library_call_preserves_openaerialmap_fields_and_local_asset_metadata(
    tmp_path,
):
    source = write_localized_openaerialmap_item_collection_json(
        tmp_path,
        tmp_path / "openaerialmap-local.itemcollection.json",
    )
    output = tmp_path / "openaerialmap.items.parquet"

    write_items_geoparquet(read_stac_json(source), output)

    table = pq.read_table(output)
    schema = table.schema
    row = table.to_pylist()[0]
    metadata = row["assets"]["metadata"]
    thumbnail = row["assets"]["thumbnail"]

    assert table.num_rows == 1
    assert schema.metadata[b"stac_geoparquet:version"] == b"1.0.0"
    assert b"stac-geoparquet" in schema.metadata
    assert b"geo" in schema.metadata
    assert schema.field("geometry").metadata[b"ARROW:extension:name"] == b"geoarrow.wkb"
    assert pa.types.is_timestamp(schema.field("created").type)
    assert row["collection"] == "openaerialmap"
    assert row["oam:platform_type"]
    assert row["gsd"] > 0
    assert thumbnail["href"].startswith("file://")
    assert thumbnail["file:size"] > 0
    assert thumbnail["file:checksum"].startswith("1220")
    assert thumbnail["type"] == "image/png"
    assert metadata["type"] == "application/json"


def test_created_stac_geoparquet_can_be_locked_as_assets(tmp_path):
    source = write_localized_openaerialmap_item_collection_json(
        tmp_path,
        tmp_path / "openaerialmap-local.itemcollection.json",
    )
    output = tmp_path / "openaerialmap.items.parquet"

    write_items_geoparquet(read_stac_json(source), output)
    lock = derive_asset_lock(pq.read_table(output), include_metadata_assets=True)

    rows = sorted(lock.to_pylist(), key=lambda row: row["asset_key"])
    assert lock.num_rows == len(LOCAL_OPENAERIALMAP_ASSET_KEYS)
    assert lock.schema.names == list(ASSET_LOCK_COLUMNS)
    assert rows[0]["item_id"]
    assert {row["asset_key"] for row in rows} == set(LOCAL_OPENAERIALMAP_ASSET_KEYS)
    assert all(_href(row).startswith("file://") for row in rows)


def test_streaming_item_geoparquet_writer_preserves_expanded_batches(tmp_path):
    source = write_localized_openaerialmap_item_collection_json(
        tmp_path,
        tmp_path / "openaerialmap-local.itemcollection.json",
    )
    expanded = write_items_geoparquet(read_stac_json(source), tmp_path / "expanded.items.parquet")
    stream = BytesIO()
    write_stream(expanded, stream)
    stream.seek(0)

    output = tmp_path / "restored.items.parquet"
    write_items_geoparquet_stream(stream, output)

    restored = pq.read_table(output)
    assert restored.schema.metadata[b"stac_geoparquet:version"] == b"1.0.0"
    assert restored.schema.field("geometry").metadata[b"ARROW:extension:name"] == b"geoarrow.wkb"
    assert restored.to_pylist() == expanded.to_pylist()


def test_streaming_item_geoparquet_writer_uses_record_batch_writer(tmp_path, monkeypatch):
    table = pa.table(
        {
            "id": ["item-1", "item-2"],
            "geometry": [b"\x01\x01", b"\x01\x02"],
            "links": [[], []],
        }
    )
    stream = BytesIO()
    with pa.ipc.new_stream(stream, table.schema) as writer:
        writer.write_batch(table.slice(0, 1).to_batches()[0])
        writer.write_batch(table.slice(1, 1).to_batches()[0])
    stream.seek(0)
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
            assert batch.schema.metadata[b"stac_geoparquet:version"] == b"1.0.0"
            assert batch.schema.field("geometry").metadata[b"ARROW:extension:name"] == (
                b"geoarrow.wkb"
            )
            written_batch_sizes.append(batch.num_rows)

        def write_table(self, table: pa.Table) -> None:
            raise AssertionError("stream was materialized as a table")

    monkeypatch.setattr("stacpkg.geoparquet.pq.ParquetWriter", RecordingParquetWriter)

    write_items_geoparquet_stream(stream, tmp_path / "streamed.items.parquet")

    assert written_batch_sizes == [1, 1]
