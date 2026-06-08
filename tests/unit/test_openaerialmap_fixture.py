# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pyarrow.compute as pc

from stacpkg.arrow_io import read_parquet
from tests.data.openaerialmap_data import (
    OPENAERIALMAP,
    OPENAERIALMAP_HTTPS_ONLY,
    OPENAERIALMAP_HTTPS_ONLY_ASSETS_LOCK,
    OPENAERIALMAP_ORIGINAL_ASSETS_LOCK,
    OPENAERIALMAP_S3,
    OPENAERIALMAP_S3_ASSETS_LOCK,
)


def _asset_keys(table) -> list[str]:
    return [field.name for field in table.schema.field("assets").type]


def _asset_field(table, asset_key: str, field_name: str):
    return table.column("assets").combine_chunks().field(asset_key).field(field_name)


def _alternate_href(table, asset_key: str, alternate_key: str):
    return _asset_field(table, asset_key, "alternate").field(alternate_key).field("href")


def _asset_field_names(table, asset_key: str) -> list[str]:
    return [field.name for field in table.schema.field("assets").type.field(asset_key).type]


def test_openaerialmap_s3_fixture_preserves_source_items() -> None:
    source = read_parquet(OPENAERIALMAP)
    s3_only = read_parquet(OPENAERIALMAP_S3)

    assert s3_only.num_rows == source.num_rows
    assert s3_only.schema.names == source.schema.names
    source_without_assets = source.drop_columns(["assets"])
    s3_without_assets = s3_only.drop_columns(["assets"]).cast(source_without_assets.schema)
    assert s3_without_assets.equals(source_without_assets)


def test_openaerialmap_s3_fixture_uses_only_primary_s3_asset_hrefs() -> None:
    source = read_parquet(OPENAERIALMAP)
    s3_only = read_parquet(OPENAERIALMAP_S3)

    for asset_key in _asset_keys(source):
        source_s3_href = (
            source.column("assets")
            .combine_chunks()
            .field(asset_key)
            .field("alternate")
            .field("s3")
            .field("href")
        )
        s3_href = _asset_field(s3_only, asset_key, "href")
        s3_asset_fields = _asset_field_names(s3_only, asset_key)

        assert s3_href.equals(source_s3_href)
        assert pc.all(pc.starts_with(s3_href, "s3://")).as_py()
        assert "alternate" not in s3_asset_fields


def test_openaerialmap_https_only_fixture_preserves_source_items() -> None:
    source = read_parquet(OPENAERIALMAP)
    https_only = read_parquet(OPENAERIALMAP_HTTPS_ONLY)

    assert https_only.num_rows == source.num_rows
    assert https_only.schema.names == source.schema.names
    source_without_assets = source.drop_columns(["assets"])
    https_without_assets = https_only.drop_columns(["assets"]).cast(source_without_assets.schema)
    assert https_without_assets.equals(source_without_assets)


def test_openaerialmap_original_fixture_keeps_https_primary_and_s3_alternates() -> None:
    source = read_parquet(OPENAERIALMAP)

    for asset_key in _asset_keys(source):
        source_href = _asset_field(source, asset_key, "href")
        source_s3_href = _alternate_href(source, asset_key, "s3")

        assert pc.all(pc.match_substring_regex(source_href, r"^https://")).as_py()
        assert pc.all(pc.starts_with(source_s3_href, "s3://")).as_py()


def test_openaerialmap_https_only_fixture_uses_only_primary_https_asset_hrefs() -> None:
    source = read_parquet(OPENAERIALMAP)
    https_only = read_parquet(OPENAERIALMAP_HTTPS_ONLY)

    for asset_key in _asset_keys(source):
        source_href = _asset_field(source, asset_key, "href")
        fixture_href = _asset_field(https_only, asset_key, "href")
        fixture_fields = _asset_field_names(https_only, asset_key)

        assert fixture_href.equals(source_href)
        assert pc.all(pc.match_substring_regex(fixture_href, r"^https://")).as_py()
        assert "alternate" not in fixture_fields


def test_openaerialmap_fixture_asset_locks_record_expected_store_metadata() -> None:
    expected_asset_keys = {"thumbnail", "visual"}
    expected_rows = read_parquet(OPENAERIALMAP).num_rows * len(expected_asset_keys)
    original = read_parquet(OPENAERIALMAP_ORIGINAL_ASSETS_LOCK)
    s3_only = read_parquet(OPENAERIALMAP_S3_ASSETS_LOCK)
    https_only = read_parquet(OPENAERIALMAP_HTTPS_ONLY_ASSETS_LOCK)

    for lock in (original, s3_only, https_only):
        rows = lock.to_pylist()
        assert lock.num_rows == expected_rows
        assert {row["asset_key"] for row in rows} == expected_asset_keys
        assert all(row["asset_key"] != "metadata" for row in rows)
        assert sum(row["etag"] is not None for row in rows) == lock.num_rows
        assert sum(row["last_modified"] is not None for row in rows) == lock.num_rows
        assert sum(row["size_bytes"] is not None for row in rows) == lock.num_rows

    assert {row["store_type"] for row in original.to_pylist()} == {"https"}
    assert {row["store_type"] for row in https_only.to_pylist()} == {"https"}
    assert {row["store_type"] for row in s3_only.to_pylist()} == {"s3"}
    assert {row["store_endpoint_url"] for row in s3_only.to_pylist()} == {
        "https://s3.us-east-1.amazonaws.com"
    }
