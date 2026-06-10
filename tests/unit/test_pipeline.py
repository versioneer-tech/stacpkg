# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pyarrow as pa

from stacpkg.arrow_io import read_parquet, read_stream, write_parquet, write_stream
from stacpkg.assets import asset_lock_table, derive_asset_lock
from stacpkg.dataset import build_package
from stacpkg.locators import href_from_location
from stacpkg.schemas import (
    ASSET_LOCK_COLUMNS,
    ASSET_LOCK_FIELDS,
    SCHEMA_KIND_KEY,
    SchemaKind,
)
from stacpkg.stac_json import read_stac_json
from openaerialmap_fixture import (
    LOCAL_OPENAERIALMAP_ASSET_KEYS,
    write_localized_openaerialmap_item_collection_json,
)

DEFAULT_OPENAERIALMAP_LOCKED_ASSET_KEYS = tuple(
    asset_key for asset_key in LOCAL_OPENAERIALMAP_ASSET_KEYS if asset_key != "metadata"
)


def _write_source(tmp_path):
    return write_localized_openaerialmap_item_collection_json(
        tmp_path,
        tmp_path / "openaerialmap-local.itemcollection.json",
    )


def _href(row: dict[str, object]) -> str:
    value = href_from_location(row)
    assert isinstance(value, str)
    return value


def test_read_json_creates_stac_items_table(tmp_path):
    source = _write_source(tmp_path)

    table = read_stac_json(source)

    assert table.schema.metadata[SCHEMA_KIND_KEY] == SchemaKind.ITEMS.value.encode()
    assert table.num_rows == 1
    row = table.to_pylist()[0]
    assert row["id"]
    assert row["collection"] == "openaerialmap"
    assert row["source_href"] == str(source)


def test_derive_asset_lock_creates_asset_lock_rows(tmp_path):
    source = _write_source(tmp_path)
    items = read_stac_json(source)

    lock = derive_asset_lock(items)

    assert lock.schema.metadata[SCHEMA_KIND_KEY] == SchemaKind.ASSET_LOCK.value.encode()
    rows = sorted(lock.to_pylist(), key=lambda row: row["asset_key"])
    assert lock.schema.names == list(ASSET_LOCK_COLUMNS)
    assert [
        (field.name, field.type, field.nullable)
        for field in lock.schema
        if field.name in ASSET_LOCK_FIELDS
    ] == [
        (
            ASSET_LOCK_FIELDS[name].name,
            ASSET_LOCK_FIELDS[name].type,
            ASSET_LOCK_FIELDS[name].nullable,
        )
        for name in ASSET_LOCK_COLUMNS
    ]
    assert [row["asset_key"] for row in rows] == sorted(DEFAULT_OPENAERIALMAP_LOCKED_ASSET_KEYS)
    assert _href(rows[0]).startswith("file://")
    assert "file_checksum" not in lock.schema.names


def test_derive_asset_lock_skips_metadata_assets_by_default(tmp_path):
    source = _write_source(tmp_path)
    items = read_stac_json(source)

    rows = derive_asset_lock(items).to_pylist()

    assert {row["asset_key"] for row in rows} == set(DEFAULT_OPENAERIALMAP_LOCKED_ASSET_KEYS)
    assert "metadata" not in {row["asset_key"] for row in rows}


def test_derive_asset_lock_can_include_metadata_assets(tmp_path):
    source = _write_source(tmp_path)
    items = read_stac_json(source)

    rows = derive_asset_lock(items, include_metadata_assets=True).to_pylist()

    assert {row["asset_key"] for row in rows} == set(LOCAL_OPENAERIALMAP_ASSET_KEYS)


def test_derive_asset_lock_explicit_metadata_filter_wins(tmp_path):
    source = _write_source(tmp_path)
    items = read_stac_json(source)

    rows = derive_asset_lock(items, asset_keys={"metadata"}).to_pylist()

    assert [row["asset_key"] for row in rows] == ["metadata"]


def test_asset_lock_table_writes_only_valid_columns():
    table = asset_lock_table(
        [
            {
                "item_id": "item-1",
                "asset_key": "thumbnail",
                "href": "s3://bucket/item-1/thumbnail.png",
                "size": 12,
                "file_checksum": f"1220{'0' * 64}",
                "etag": '"abc"',
                "version_id": "v1",
            }
        ]
    )

    assert table.schema.names == list(ASSET_LOCK_COLUMNS)
    assert table.to_pylist() == [
        {
            "item_id": "item-1",
            "asset_key": "thumbnail",
            "store_type": "s3",
            "store_container": "bucket",
            "store_endpoint_url": None,
            "key": "item-1/thumbnail.png",
            "size_bytes": 12,
            "etag": '"abc"',
            "last_modified": None,
        }
    ]


def test_arrow_stream_round_trip(tmp_path):
    source = _write_source(tmp_path)
    table = read_stac_json(source)

    sink = pa.BufferOutputStream()
    write_stream(table, sink)
    restored = read_stream(pa.BufferReader(sink.getvalue()))

    assert restored.schema == table.schema
    assert restored.to_pylist() == table.to_pylist()


def test_build_package_library_call(tmp_path):
    source = _write_source(tmp_path)

    output = tmp_path / "stacpkg.lock"
    package = build_package(source, output)
    items = read_parquet(output / "items.parquet")

    assert package["package"] == str(output)
    assert {entry["path"] for entry in package["files"]} == {
        "items.parquet",
        "assets.lock.parquet",
    }
    assert all(entry["digest"].startswith("sha256:") for entry in package["files"])
    assert not (output / "manifest.json").exists()
    assert items.num_rows == 1
    assert items.schema.metadata[b"stac_geoparquet:version"] == b"1.0.0"
    assets = read_parquet(output / "assets.lock.parquet")
    assert assets.num_rows == len(DEFAULT_OPENAERIALMAP_LOCKED_ASSET_KEYS)
    assert {row["asset_key"] for row in assets.to_pylist()} == set(
        DEFAULT_OPENAERIALMAP_LOCKED_ASSET_KEYS
    )


def test_library_arrow_pipeline(tmp_path):
    source = _write_source(tmp_path)
    lock_path = tmp_path / "source.assets.lock.parquet"

    # CLI example: stacpkg items from-json source.json | stacpkg asset-lock derive | stacpkg asset-lock to-parquet source.assets.lock.parquet
    write_parquet(derive_asset_lock(read_stac_json(source)), lock_path)

    lock = read_parquet(lock_path)
    assert lock.num_rows == len(DEFAULT_OPENAERIALMAP_LOCKED_ASSET_KEYS)
    assert lock.schema.names == list(ASSET_LOCK_COLUMNS)
