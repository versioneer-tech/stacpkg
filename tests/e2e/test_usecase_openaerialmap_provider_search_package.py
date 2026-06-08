# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq
import pytest

from stacpkg.arrow_io import read_parquet, write_parquet
from stacpkg.assets import derive_asset_lock
from stacpkg.dataset import build_package
from stacpkg.geoparquet import write_items_geoparquet
from stacpkg.items import filter_items
from stacpkg.locators import href_from_location
from stacpkg.report import package_inspect_data
from stacpkg.schemas import ASSET_LOCK_COLUMNS
from tests.unit.openaerialmap_fixture import localized_openaerialmap_items

OPENAERIALMAP_PROVIDER = "ODM"
OPENAERIALMAP_SOURCE_ITEM_COUNT = 3
EXPECTED_ITEM_IDS = {"677978ba947872000179059c"}
EXPECTED_ASSET_KEYS = {"thumbnail"}


def _href(row: dict[str, object]) -> str:
    value = href_from_location(row)
    assert isinstance(value, str)
    return value


@pytest.mark.e2e
def test_openaerialmap_provider_subset_to_geoparquet_package_and_asset_lock(
    tmp_path: Path,
) -> None:
    items_geoparquet = tmp_path / "openaerialmap-provider.items.parquet"
    metadata_assets_lock = tmp_path / "openaerialmap-provider.metadata.assets.lock.parquet"
    object_assets_lock = tmp_path / "openaerialmap-provider.object.assets.lock.parquet"
    package_dir = tmp_path / "openaerialmap-provider.pkg"

    source = localized_openaerialmap_items(
        tmp_path,
        item_count=OPENAERIALMAP_SOURCE_ITEM_COUNT,
    )
    provider_items = filter_items(source, providers={OPENAERIALMAP_PROVIDER})
    assert provider_items.num_rows == len(EXPECTED_ITEM_IDS)

    # NOTEBOOK: ## Filter The Materialized OpenAerialMap Provider Selection
    # CLI: stacpkg items from-parquet openaerialmap-2025.items.parquet --providers ODM
    #      | stacpkg items to-parquet openaerialmap-provider.items.parquet
    write_items_geoparquet(provider_items, items_geoparquet)

    items = pq.read_table(items_geoparquet)
    assert items.num_rows == len(EXPECTED_ITEM_IDS)
    assert items.schema.metadata[b"stac_geoparquet:version"] == b"1.0.0"
    assert {row["id"] for row in items.to_pylist()} == EXPECTED_ITEM_IDS
    assert {row["collection"] for row in items.to_pylist()} == {"openaerialmap"}
    # NOTEBOOK_TABLE: items.select(["id", "collection", "title"])

    # NOTEBOOK_OUTPUT: matched OpenAerialMap items: 1
    # NOTEBOOK_OUTPUT: STAC GeoParquet rows: 1
    # NOTEBOOK: ## Derive a metadata-reuse asset lock
    # CLI: stacpkg items from-parquet openaerialmap-provider.items.parquet
    #      | stacpkg asset-lock derive --no-probe-metadata
    #      | stacpkg asset-lock to-parquet openaerialmap-provider.metadata.assets.lock.parquet
    write_parquet(
        derive_asset_lock(read_parquet(items_geoparquet), probe_metadata=False),
        metadata_assets_lock,
    )
    # NOTEBOOK_TABLE: read_parquet(metadata_assets_lock) | item_id,asset_key,store_type,store_container,key

    # NOTEBOOK: ## Derive a lock with local object metadata
    # CLI: stacpkg items from-parquet openaerialmap-provider.items.parquet
    #      | stacpkg asset-lock derive
    #      | stacpkg asset-lock to-parquet openaerialmap-provider.object.assets.lock.parquet
    write_parquet(
        derive_asset_lock(
            read_parquet(items_geoparquet),
            probe_metadata=True,
        ),
        object_assets_lock,
    )
    # NOTEBOOK_TABLE: read_parquet(object_assets_lock).to_pylist() | item_id,asset_key,size_bytes,etag,last_modified,store_type,store_container,key

    # NOTEBOOK: ## Build a self-contained stacpkg package with asset bytes
    # CLI: stacpkg items from-parquet openaerialmap-provider.items.parquet
    #      | stacpkg build openaerialmap-provider.pkg
    #      --asset-lock openaerialmap-provider.object.assets.lock.arrow
    #      --include-assets
    build_package(
        items_geoparquet,
        package_dir,
        asset_lock=read_parquet(object_assets_lock),
        include_assets=True,
    )

    locked_assets_table = read_parquet(package_dir / "assets.lock.parquet")
    locked_assets = locked_assets_table.to_pylist()
    package_files = package_inspect_data(package_dir)["files"]
    asset_entries = [entry for entry in package_files if str(entry["path"]).startswith("assets/")]
    # NOTEBOOK_TABLE: read_parquet(package_dir / "items.parquet").select(["id", "collection", "title"])
    # NOTEBOOK_TABLE: asset_entries | path,mediaType,size
    # NOTEBOOK_TABLE: locked_assets | item_id,asset_key,size_bytes,store_type,store_container,key
    assert len(locked_assets) == len(EXPECTED_ITEM_IDS) * len(EXPECTED_ASSET_KEYS)
    assert len(asset_entries) == len(locked_assets)
    assert {row["item_id"] for row in locked_assets} == EXPECTED_ITEM_IDS
    assert {row["asset_key"] for row in locked_assets} == EXPECTED_ASSET_KEYS
    assert {entry["path"] for entry in asset_entries} == {_href(row) for row in locked_assets}
    assert locked_assets_table.schema.names == list(ASSET_LOCK_COLUMNS)

    source_locked_assets = read_parquet(object_assets_lock).to_pylist()
    assert all(row["size_bytes"] > 0 for row in source_locked_assets)
    assert read_parquet(object_assets_lock).schema.names == list(ASSET_LOCK_COLUMNS)
    for row in locked_assets:
        href = _href(row)
        packaged_asset = package_dir / href
        assert href.startswith("assets/")
        assert packaged_asset.exists()
        assert packaged_asset.stat().st_size == row["size_bytes"]
    # NOTEBOOK_OUTPUT: package asset lock rows: 1
    # NOTEBOOK_OUTPUT: package asset files: 1
