# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from stacpkg.arrow_io import read_parquet, write_parquet
from stacpkg.assets import derive_asset_lock, plan_copy_assets
from stacpkg.dataset import build_package
from stacpkg.enrich import FILE_EXTENSION, enrich_items
from stacpkg.locators import href_from_location
from stacpkg.object_store import copy_assets, validate_assets
from stacpkg.projection import project_item_assets
from stacpkg.report import package_inspect_markdown
from tests.e2e.helpers import (
    S3STORE1_BUCKET,
    create_bucket,
    endpoint_env,
)
from tests.data.openaerialmap_data import OPENAERIALMAP_ITEM_COUNT
from tests.unit.openaerialmap_fixture import localized_openaerialmap_items

OPENAERIALMAP_ASSET_KEYS = ("thumbnail",)
OPENAERIALMAP_DEFAULT_LOCKED_ASSET_KEYS = ("thumbnail",)


def _href(row: dict[str, object]) -> str:
    value = href_from_location(row)
    assert isinstance(value, str)
    return value


@pytest.mark.e2e
def test_snapshot_selected_items_lock_validate_and_inspect_assets(tmp_path: Path) -> None:
    env = endpoint_env()
    s3store1_endpoint = env["STACPKG_S3_ENDPOINT_STACPKG_E2E_S3STORE1"]
    create_bucket(s3store1_endpoint, env, S3STORE1_BUCKET)
    fixture_subset = tmp_path / "openaerialmap.items.parquet"
    s3_items_path = tmp_path / "openaerialmap-s3-store-selection.items.parquet"
    s3_plan = tmp_path / "openaerialmap-s3.plan.assets.lock.parquet"
    s3_copied = tmp_path / "openaerialmap-s3.asset-lock.parquet"
    # NOTEBOOK: ## Create a small OpenAerialMap source selection
    # NOTEBOOK: The workflow uses tiny local OpenAerialMap thumbnail fixtures so
    # NOTEBOOK: rendered examples stay readable and reproducible.
    source_items = localized_openaerialmap_items(
        tmp_path,
        item_count=OPENAERIALMAP_ITEM_COUNT,
    )
    write_parquet(source_items, fixture_subset)
    # NOTEBOOK_TABLE: read_parquet(fixture_subset).select(["id", "collection", "title"])
    first_package_dir = tmp_path / "first.pkg"
    # NOTEBOOK: ## Build the first package from that subset
    # CLI: stacpkg items from-parquet openaerialmap.items.parquet
    #      | stacpkg build first.pkg/
    build_package(fixture_subset, first_package_dir)
    assert not (first_package_dir / "manifest.json").exists()
    assert read_parquet(first_package_dir / "items.parquet").num_rows == OPENAERIALMAP_ITEM_COUNT
    assert read_parquet(first_package_dir / "assets.lock.parquet").num_rows == (
        OPENAERIALMAP_ITEM_COUNT * len(OPENAERIALMAP_DEFAULT_LOCKED_ASSET_KEYS)
    )

    # NOTEBOOK: ## Relocate fixture assets into local MinIO
    # CLI: stacpkg items from-parquet openaerialmap.items.parquet
    #      | stacpkg asset-lock derive
    #      | stacpkg asset-lock to-parquet source.assets.lock.parquet
    source_lock = derive_asset_lock(source_items)
    # CLI: stacpkg asset-lock from-parquet source.assets.lock.parquet
    #      | stacpkg asset-lock relocate
    #      --store-type s3 --store-container s3store1 --key reproducible-inputs/
    #      --store-endpoint-url http://127.0.0.1:19000
    #      | stacpkg asset-lock to-parquet openaerialmap-s3.asset-lock.parquet
    write_parquet(
        plan_copy_assets(
            source_lock,
            target=f"s3://{S3STORE1_BUCKET}/reproducible-inputs/{uuid4().hex[:12]}/",
            target_endpoint_url=s3store1_endpoint,
        ),
        s3_plan,
    )
    write_parquet(copy_assets(source_lock, read_parquet(s3_plan)), s3_copied)
    # CLI: stacpkg items from-parquet openaerialmap.items.parquet
    #      | stacpkg items add-alternate
    #      --asset-lock openaerialmap-s3.asset-lock.arrow
    #      --alternate-key original --alternate-name s3
    #      | stacpkg items promote-alternate
    #      --alternate-key original --mode switch
    #      | stacpkg items to-parquet openaerialmap-s3-store-selection.items.parquet
    write_parquet(
        project_item_assets(source_items, read_parquet(s3_copied), strategy="set-href"),
        s3_items_path,
    )

    package_dir = tmp_path / "reproducible-inputs.pkg"
    asset_lock = tmp_path / "source.assets.lock.parquet"
    enriched_items = tmp_path / "enriched.items.parquet"
    inspect_report = tmp_path / "inspect.md"

    # NOTEBOOK: ## Lock relocated source assets with object metadata
    # CLI: stacpkg items from-parquet openaerialmap-s3-store-selection.items.parquet
    #      | stacpkg asset-lock derive
    #      | stacpkg asset-lock to-parquet source.assets.lock.parquet
    write_parquet(
        derive_asset_lock(
            read_parquet(s3_items_path),
            probe_metadata=True,
        ),
        asset_lock,
    )
    # NOTEBOOK_TABLE: read_parquet(asset_lock).to_pylist() | item_id,asset_key,store_type,store_container,key,size_bytes,etag,last_modified
    # NOTEBOOK: ## Build a reproducible package from the locked inputs
    # CLI: stacpkg items from-parquet openaerialmap-s3-store-selection.items.parquet
    #      | stacpkg build package/
    #      --asset-lock source.assets.lock.arrow
    build_package(
        s3_items_path,
        package_dir,
        asset_lock=read_parquet(asset_lock),
        probe_metadata=True,
    )
    assert not (package_dir / "manifest.json").exists()
    # NOTEBOOK: ## Validate, enrich, and inspect the package
    # CLI: stacpkg asset-lock from-parquet package/assets.lock.parquet
    #      | stacpkg asset-lock validate
    default_validation_results = validate_assets(read_parquet(package_dir / "assets.lock.parquet"))
    # NOTEBOOK_TABLE: default_validation_results | item_id,asset_key,valid,errors
    # CLI: stacpkg items from-parquet package/items.parquet
    #      | stacpkg items enrich --asset-lock package/assets.lock.arrow
    #      | stacpkg items to-parquet enriched.items.parquet
    write_parquet(
        enrich_items(
            read_parquet(package_dir / "items.parquet"),
            read_parquet(package_dir / "assets.lock.parquet"),
        ),
        enriched_items,
    )
    # CLI: stacpkg inspect package/ --format markdown
    inspect_report.write_text(package_inspect_markdown(package_dir), encoding="utf-8")

    package_items = read_parquet(package_dir / "items.parquet")
    package_assets = read_parquet(package_dir / "assets.lock.parquet")
    locked_assets = package_assets.to_pylist()
    enriched_rows = read_parquet(enriched_items).to_pylist()
    summary_rows = [
        {"name": "package items", "count": package_items.num_rows},
        {"name": "package assets", "count": package_assets.num_rows},
    ]
    # NOTEBOOK_TABLE: summary_rows | name,count

    assert summary_rows == [
        {"name": "package items", "count": OPENAERIALMAP_ITEM_COUNT},
        {
            "name": "package assets",
            "count": OPENAERIALMAP_ITEM_COUNT * len(OPENAERIALMAP_ASSET_KEYS),
        },
    ]
    assert package_items.num_rows == OPENAERIALMAP_ITEM_COUNT
    assert package_assets.num_rows == OPENAERIALMAP_ITEM_COUNT * len(OPENAERIALMAP_ASSET_KEYS)
    assert all(_href(row).startswith(f"s3://{S3STORE1_BUCKET}/") for row in locked_assets)
    assert all(row["size_bytes"] > 0 for row in locked_assets)
    assert {row["asset_key"] for row in locked_assets} == set(OPENAERIALMAP_ASSET_KEYS)
    assert all(result["valid"] for result in default_validation_results)
    for enriched_row in enriched_rows:
        assert FILE_EXTENSION in enriched_row["stac_extensions"]
        for asset_key in OPENAERIALMAP_ASSET_KEYS:
            asset = enriched_row["assets"][asset_key]
            assert asset["href"].startswith(f"s3://{S3STORE1_BUCKET}/")
            assert asset["file:size"] > 0
            assert "file:checksum" not in asset
    assert f"- Items: {OPENAERIALMAP_ITEM_COUNT}" in inspect_report.read_text(encoding="utf-8")
    assert (
        f"- Assets: {OPENAERIALMAP_ITEM_COUNT * len(OPENAERIALMAP_ASSET_KEYS)}"
        in inspect_report.read_text(encoding="utf-8")
    )
