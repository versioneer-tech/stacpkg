# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from stacpkg.arrow_io import read_parquet, write_parquet
from stacpkg.assets import derive_asset_lock, plan_copy_assets
from stacpkg.items import filter_items
from stacpkg.locators import href_from_location
from stacpkg.object_store import copy_assets, validate_assets
from stacpkg.projection import project_item_assets
from stacpkg.schemas import ASSET_LOCK_COLUMNS
from tests.e2e.helpers import (
    S3STORE1_BUCKET,
    S3STORE2_BUCKET,
    create_bucket,
    endpoint_env,
    head_size,
)
from tests.unit.openaerialmap_fixture import localized_openaerialmap_items

LOGGER = logging.getLogger(__name__)
OPENAERIALMAP_PROVIDER = "ODM"
OPENAERIALMAP_SOURCE_ITEM_COUNT = 3
OPENAERIALMAP_RELOCATED_ITEM_COUNT = 1
OPENAERIALMAP_ASSET_KEYS = ("thumbnail",)


def _s3_key(href: object, *, bucket: str) -> str:
    assert isinstance(href, str)
    parsed = urlparse(href)
    assert parsed.scheme == "s3"
    assert parsed.netloc == bucket
    return parsed.path.lstrip("/")


def _href(row: dict[str, object]) -> str:
    value = href_from_location(row)
    assert isinstance(value, str)
    return value


def _source_items(tmp_path: Path):
    items = localized_openaerialmap_items(
        tmp_path,
        item_count=OPENAERIALMAP_SOURCE_ITEM_COUNT,
    )
    selected = filter_items(items, providers={OPENAERIALMAP_PROVIDER})
    assert selected.num_rows == OPENAERIALMAP_RELOCATED_ITEM_COUNT
    return selected


def _asset_lock(items):
    return derive_asset_lock(items)


def _assert_copied_assets(
    lock_path: Path,
    *,
    endpoint: str,
    env: dict[str, str],
    bucket: str,
) -> None:
    table = read_parquet(lock_path)
    rows = table.to_pylist()
    assert table.schema.names == list(ASSET_LOCK_COLUMNS)
    assert len(rows) == OPENAERIALMAP_RELOCATED_ITEM_COUNT * len(OPENAERIALMAP_ASSET_KEYS)
    assert {row["asset_key"] for row in rows} == set(OPENAERIALMAP_ASSET_KEYS)
    assert all(_href(row).startswith(f"s3://{bucket}/") for row in rows)

    for row in rows:
        assert (
            head_size(endpoint, env, bucket, _s3_key(_href(row), bucket=bucket))
            == row["size_bytes"]
        )


def _assert_projected_primary_hrefs(items_path: Path, *, bucket: str, endpoint: str) -> None:
    rows = read_parquet(items_path).to_pylist()
    assert len(rows) == OPENAERIALMAP_RELOCATED_ITEM_COUNT
    for row in rows:
        assert row["collection"] == "openaerialmap"
        for asset_key in OPENAERIALMAP_ASSET_KEYS:
            asset = row["assets"][asset_key]
            assert asset["href"].startswith(f"s3://{bucket}/")
            assert asset["store_endpoint_url"] == endpoint
            assert asset["alternate"]["original"]["href"].startswith("file://")


@pytest.mark.e2e
def test_openaerialmap_assets_can_be_relocated_to_kind_minio_and_projected(
    tmp_path: Path,
) -> None:
    env = endpoint_env()
    s3store1_endpoint = env["STACPKG_S3_ENDPOINT_STACPKG_E2E_S3STORE1"]
    s3store2_endpoint = env["STACPKG_S3_ENDPOINT_STACPKG_E2E_S3STORE2"]
    run_prefix = f"openaerialmap-runs/{uuid4().hex[:12]}"
    LOGGER.info(
        "starting OpenAerialMap asset relocation e2e: provider=%s run_prefix=%s",
        OPENAERIALMAP_PROVIDER,
        run_prefix,
    )

    create_bucket(s3store1_endpoint, env, S3STORE1_BUCKET)
    create_bucket(s3store2_endpoint, env, S3STORE2_BUCKET)

    source_items = tmp_path / "openaerialmap-provider.items.parquet"
    source_lock = tmp_path / "source.assets.lock.parquet"
    s3store1_plan = tmp_path / "s3store1.plan.assets.lock.parquet"
    s3store1_copied = tmp_path / "s3store1.asset-lock.parquet"
    s3store1_items = tmp_path / "s3store1.items.parquet"
    s3store2_plan = tmp_path / "s3store2.plan.assets.lock.parquet"
    s3store2_copied = tmp_path / "s3store2.asset-lock.parquet"

    # NOTEBOOK: ## Filter A Small OpenAerialMap Provider Subset
    # NOTEBOOK: The full 2025 fixture is intentionally larger than this
    # NOTEBOOK: handover test needs. Use `--providers` to keep a deterministic
    # NOTEBOOK: subset before deriving or relocating asset locks.
    # CLI: stacpkg items from-parquet openaerialmap-2025.items.parquet --providers ODM
    #      | stacpkg items to-parquet openaerialmap-provider.items.parquet
    selected_items = _source_items(tmp_path)
    write_parquet(selected_items, source_items)
    # NOTEBOOK_TABLE: read_parquet(source_items).select(["id", "collection", "title"])

    # NOTEBOOK: ## Lock The Provider Assets
    # CLI: stacpkg items from-parquet openaerialmap-provider.items.parquet
    #      | stacpkg asset-lock derive
    #      | stacpkg asset-lock to-parquet source.assets.lock.parquet
    write_parquet(_asset_lock(selected_items), source_lock)

    # NOTEBOOK: ## Relocate Assets Into Local Kind MinIO
    # CLI: stacpkg asset-lock from-parquet source.assets.lock.parquet
    #      | stacpkg asset-lock relocate
    #      --store-type s3 --store-container s3store1 --key controlled-relocation/
    #      --store-endpoint-url http://127.0.0.1:19000
    #      | stacpkg asset-lock to-parquet s3store1.asset-lock.parquet
    write_parquet(
        plan_copy_assets(
            read_parquet(source_lock),
            target=f"s3://{S3STORE1_BUCKET}/{run_prefix}/controlled-relocation/",
            target_endpoint_url=s3store1_endpoint,
        ),
        s3store1_plan,
    )
    write_parquet(
        copy_assets(
            read_parquet(source_lock),
            read_parquet(s3store1_plan),
            max_workers=2,
            memory_limit_bytes=32 * 1024 * 1024,
            chunk_size_bytes=8 * 1024 * 1024,
            put_max_concurrency=1,
        ),
        s3store1_copied,
    )
    copied_validation = validate_assets(read_parquet(s3store1_copied))
    # NOTEBOOK_TABLE: read_parquet(s3store1_copied).to_pylist() | item_id,asset_key,store_type,store_container,key,size_bytes
    # NOTEBOOK_TABLE: copied_validation | item_id,asset_key,valid,errors

    # NOTEBOOK: ## Promote Relocated MinIO Hrefs Back Into STAC Items
    # CLI: stacpkg items from-parquet openaerialmap-provider.items.parquet
    #      | stacpkg items add-alternate
    #      --asset-lock s3store1.asset-lock.arrow
    #      --alternate-key original --alternate-name s3store1
    #      | stacpkg items promote-alternate
    #      --alternate-key original --mode switch
    #      | stacpkg items to-parquet s3store1.items.parquet
    write_parquet(
        project_item_assets(
            read_parquet(source_items),
            read_parquet(s3store1_copied),
            strategy="set-href",
        ),
        s3store1_items,
    )
    relocated_lock = derive_asset_lock(
        read_parquet(s3store1_items),
        probe_metadata=True,
    )
    relocated_validation = validate_assets(relocated_lock)
    # NOTEBOOK_TABLE: read_parquet(s3store1_items).select(["id", "collection", "title"])
    # NOTEBOOK_TABLE: relocated_lock.to_pylist() | item_id,asset_key,store_type,store_container,key,size_bytes
    # NOTEBOOK_TABLE: relocated_validation | item_id,asset_key,valid,errors

    # NOTEBOOK: ## Relocate Assets To A Recipient MinIO Store
    # CLI: stacpkg asset-lock from-parquet s3store1.asset-lock.parquet
    #      | stacpkg asset-lock relocate
    #      --store-type s3 --store-container s3store2 --key recipient-relocation/
    #      --store-endpoint-url http://127.0.0.1:19010
    #      | stacpkg asset-lock to-parquet s3store2.asset-lock.parquet
    write_parquet(
        plan_copy_assets(
            read_parquet(s3store1_copied),
            target=f"s3://{S3STORE2_BUCKET}/{run_prefix}/recipient-relocation/",
            target_endpoint_url=s3store2_endpoint,
        ),
        s3store2_plan,
    )
    write_parquet(
        copy_assets(
            read_parquet(s3store1_copied),
            read_parquet(s3store2_plan),
            max_workers=2,
            memory_limit_bytes=32 * 1024 * 1024,
            chunk_size_bytes=8 * 1024 * 1024,
            put_max_concurrency=1,
        ),
        s3store2_copied,
    )

    assert all(result["valid"] for result in copied_validation)
    assert all(result["valid"] for result in relocated_validation)
    _assert_copied_assets(
        s3store1_copied,
        endpoint=s3store1_endpoint,
        env=env,
        bucket=S3STORE1_BUCKET,
    )
    _assert_copied_assets(
        s3store2_copied,
        endpoint=s3store2_endpoint,
        env=env,
        bucket=S3STORE2_BUCKET,
    )
    _assert_projected_primary_hrefs(
        s3store1_items,
        bucket=S3STORE1_BUCKET,
        endpoint=s3store1_endpoint,
    )
    # NOTEBOOK_OUTPUT: selected OpenAerialMap provider items: 1
    # NOTEBOOK_OUTPUT: relocated assets validate in local MinIO
    # NOTEBOOK_OUTPUT: projected STAC items hrefs point at local MinIO
    LOGGER.info("completed OpenAerialMap asset relocation e2e: run_prefix=%s", run_prefix)
