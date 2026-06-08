# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from stacpkg.arrow_io import read_parquet, write_parquet
from stacpkg.assets import asset_lock_table, derive_asset_lock, plan_copy_assets
from stacpkg.dataset import build_package
from stacpkg.items import filter_items
from stacpkg.locators import href_from_location
from stacpkg.oci import pull_package, push_package
from stacpkg.object_store import copy_assets
from stacpkg.projection import project_item_assets
from stacpkg.report import package_inspect_data
from stacpkg.schemas import ASSET_LOCK_COLUMNS
from tests.e2e.helpers import (
    S3STORE1_BUCKET,
    S3STORE2_BUCKET,
    create_bucket,
    endpoint_env,
    head_size,
    registry_target,
)
from tests.unit.openaerialmap_fixture import localized_openaerialmap_items

LOGGER = logging.getLogger(__name__)
OPENAERIALMAP_PROVIDER = "ODM"
OPENAERIALMAP_SOURCE_ITEM_COUNT = 3
OPENAERIALMAP_ITEM_COUNT = 1
OPENAERIALMAP_ASSET_KEYS = ("thumbnail",)
OPENAERIALMAP_DEFAULT_LOCKED_ASSET_KEYS = ("thumbnail",)


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


def _write_source_items(tmp_path: Path, output: Path) -> Path:
    items = localized_openaerialmap_items(
        tmp_path,
        item_count=OPENAERIALMAP_SOURCE_ITEM_COUNT,
    )
    selected = filter_items(items, providers={OPENAERIALMAP_PROVIDER})
    assert selected.num_rows == OPENAERIALMAP_ITEM_COUNT
    write_parquet(selected, output)
    return output


def _write_source_asset_lock(items_path: Path, output: Path) -> None:
    assets = derive_asset_lock(read_parquet(items_path))
    write_parquet(asset_lock_table(assets.to_pylist()), output)
    LOGGER.debug(
        "wrote OpenAerialMap source asset lock: rows=%s output=%s",
        assets.num_rows,
        output,
    )


def _assert_package(
    package_dir: Path,
    *,
    expected_asset_count: int = OPENAERIALMAP_ITEM_COUNT * len(OPENAERIALMAP_ASSET_KEYS),
) -> None:
    items = read_parquet(package_dir / "items.parquet")
    assets = read_parquet(package_dir / "assets.lock.parquet")
    LOGGER.debug(
        "package summary: package=%s items=%s assets=%s",
        package_dir,
        items.num_rows,
        assets.num_rows,
    )
    assert not (package_dir / "manifest.json").exists()
    assert items.num_rows == OPENAERIALMAP_ITEM_COUNT
    assert assets.num_rows == expected_asset_count
    assert {"items.parquet", "assets.lock.parquet"}.issubset(
        {entry["path"] for entry in package_inspect_data(package_dir)["files"]}
    )


def _asset_map(items_path: Path, *, item_index: int = 0) -> dict[str, dict[str, object]]:
    row = read_parquet(items_path).to_pylist()[item_index]
    assets = row.get("assets")
    if isinstance(assets, dict):
        return assets
    assets_json = row.get("assets_json")
    if isinstance(assets_json, str):
        return json.loads(assets_json)
    raise AssertionError(f"item row does not contain assets: {row.keys()}")


def _assert_alternate_target(items_path: Path, *, alternate_key: str, bucket: str) -> None:
    assets = _asset_map(items_path)
    LOGGER.debug(
        "checking alternate target: items=%s alternate_key=%s bucket=%s asset_keys=%s",
        items_path,
        alternate_key,
        bucket,
        sorted(assets),
    )
    for asset_key in OPENAERIALMAP_ASSET_KEYS:
        asset = assets[asset_key]
        href = asset["href"]
        alternate_href = asset["alternate"][alternate_key]["href"]
        assert isinstance(href, str)
        assert href.startswith("file://")
        assert alternate_href.startswith(f"s3://{bucket}/")


def _assert_copied_assets(
    lock_path: Path,
    *,
    endpoint: str,
    env: dict[str, str],
    bucket: str,
) -> None:
    table = read_parquet(lock_path)
    rows = table.to_pylist()
    assert len(rows) == OPENAERIALMAP_ITEM_COUNT * len(OPENAERIALMAP_ASSET_KEYS)
    assert all(_href(row).startswith(f"s3://{bucket}/") for row in rows)
    assert {row["asset_key"] for row in rows} == set(OPENAERIALMAP_ASSET_KEYS)
    assert table.schema.names == list(ASSET_LOCK_COLUMNS)
    for row in rows:
        assert (
            head_size(endpoint, env, bucket, _s3_key(_href(row), bucket=bucket))
            == row["size_bytes"]
        )


@pytest.mark.e2e
def test_openaerialmap_package_can_be_handed_over_with_recipient_asset_storage_and_oci_roundtrip(
    tmp_path: Path,
) -> None:
    env = endpoint_env()
    s3store1_endpoint = env["STACPKG_S3_ENDPOINT_STACPKG_E2E_S3STORE1"]
    s3store2_endpoint = env["STACPKG_S3_ENDPOINT_STACPKG_E2E_S3STORE2"]
    run_prefix = f"openaerialmap-journeys/{uuid4().hex[:12]}"
    registry_ref = registry_target(
        "stacpkg/openaerialmap-recipient-package",
        run_prefix.replace("/", "-"),
    )
    LOGGER.info("starting OpenAerialMap full user journey e2e: run_prefix=%s", run_prefix)

    create_bucket(s3store1_endpoint, env, S3STORE1_BUCKET)
    create_bucket(s3store2_endpoint, env, S3STORE2_BUCKET)

    source_items = _write_source_items(
        tmp_path,
        tmp_path / "openaerialmap-provider.items.parquet",
    )

    LOGGER.info(
        "journey 1: package %s OpenAerialMap item filtered by provider=%s",
        OPENAERIALMAP_ITEM_COUNT,
        OPENAERIALMAP_PROVIDER,
    )
    source_package = tmp_path / "01-source-package"
    # NOTEBOOK: ## Package a provider-filtered OpenAerialMap item selection
    # CLI: stacpkg items from-parquet openaerialmap-provider.items.parquet
    #      | stacpkg build 01-source-package/
    build_package(source_items, source_package)
    _assert_package(
        source_package,
        expected_asset_count=OPENAERIALMAP_ITEM_COUNT
        * len(OPENAERIALMAP_DEFAULT_LOCKED_ASSET_KEYS),
    )
    # NOTEBOOK_TABLE: read_parquet(source_package / "items.parquet").select(["id", "collection", "title"])
    # NOTEBOOK_TABLE: package_inspect_data(source_package)["files"] | path,mediaType,size

    LOGGER.info(
        "journey 2: relocate OpenAerialMap assets into controlled S3 store 1 and add alternates"
    )
    source_lock = tmp_path / "source.assets.lock.parquet"
    s3store1_plan = tmp_path / "s3store1.plan.assets.lock.parquet"
    s3store1_copied = tmp_path / "s3store1.asset-lock.parquet"
    controlled_items = tmp_path / "controlled.items.parquet"
    controlled_package = tmp_path / "02-controlled-asset-relocation-package"

    _write_source_asset_lock(source_items, source_lock)
    # NOTEBOOK: ## Relocate OpenAerialMap Assets Into Controlled S3 Storage
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
            max_workers=4,
            memory_limit_bytes=64 * 1024 * 1024,
            chunk_size_bytes=8 * 1024 * 1024,
            put_max_concurrency=1,
        ),
        s3store1_copied,
    )
    # NOTEBOOK: ## Add the controlled relocation as item alternates and build the provider-side package
    # CLI: stacpkg items from-parquet openaerialmap-provider.items.parquet
    #      | stacpkg items add-alternate
    #      --asset-lock s3store1.asset-lock.arrow
    #      --alternate-key controlled --alternate-name controlled
    #      | stacpkg items to-parquet controlled.items.parquet
    write_parquet(
        project_item_assets(
            read_parquet(source_items),
            read_parquet(s3store1_copied),
            strategy="set-alternate",
            alternate_key="controlled",
        ),
        controlled_items,
    )
    # CLI: stacpkg items from-parquet controlled.items.parquet
    #      | stacpkg build 02-controlled-asset-relocation-package/
    #      --asset-lock s3store1.asset-lock.arrow
    build_package(
        controlled_items,
        controlled_package,
        asset_lock=read_parquet(s3store1_copied),
    )
    _assert_package(controlled_package)
    _assert_alternate_target(controlled_items, alternate_key="controlled", bucket=S3STORE1_BUCKET)
    _assert_copied_assets(
        s3store1_copied,
        endpoint=s3store1_endpoint,
        env=env,
        bucket=S3STORE1_BUCKET,
    )

    LOGGER.info("journey 3: relocate controlled package assets to S3 store 2 for another party")
    s3store2_plan = tmp_path / "s3store2.plan.assets.lock.parquet"
    s3store2_copied = tmp_path / "s3store2.asset-lock.parquet"
    recipient_items = tmp_path / "recipient.items.parquet"
    recipient_package = tmp_path / "03-recipient-package"
    recipient_readme = tmp_path / "README.md"
    recipient_readme_text = (
        "# OpenAerialMap Recipient Handover\n\n"
        "This package contains the selected OpenAerialMap items table, recipient "
        "asset lock, and destination locations for the relocated assets.\n"
    )

    # NOTEBOOK: ## Relocate Controlled Assets To The Recipient S3 Store
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
            max_workers=4,
            memory_limit_bytes=64 * 1024 * 1024,
            chunk_size_bytes=8 * 1024 * 1024,
            put_max_concurrency=1,
        ),
        s3store2_copied,
    )
    # CLI: stacpkg items from-parquet controlled.items.parquet
    #      | stacpkg items add-alternate
    #      --asset-lock s3store2.asset-lock.arrow
    #      --alternate-key controlled --alternate-name controlled
    #      | stacpkg items to-parquet recipient.items.parquet
    write_parquet(
        project_item_assets(
            read_parquet(controlled_items),
            read_parquet(s3store2_copied),
            strategy="set-alternate",
            alternate_key="controlled",
        ),
        recipient_items,
    )
    # NOTEBOOK: ## Build the recipient package with handover notes
    recipient_readme.write_text(recipient_readme_text, encoding="utf-8")
    # CLI: stacpkg items from-parquet recipient.items.parquet
    #      | stacpkg build 03-recipient-package/
    #      --asset-lock s3store2.asset-lock.arrow
    #      --includes README.md
    build_package(
        recipient_items,
        recipient_package,
        asset_lock=read_parquet(s3store2_copied),
        includes=[recipient_readme],
    )
    _assert_package(recipient_package)
    _assert_alternate_target(recipient_items, alternate_key="controlled", bucket=S3STORE2_BUCKET)
    _assert_copied_assets(
        s3store2_copied,
        endpoint=s3store2_endpoint,
        env=env,
        bucket=S3STORE2_BUCKET,
    )
    assert (recipient_package / "README.md").read_text(encoding="utf-8") == recipient_readme_text

    LOGGER.info("journey 4: publish and pull recipient package through local registry")
    pulled_package = tmp_path / "04-pulled-recipient-package"

    # NOTEBOOK: ## Publish and pull the recipient package through OCI
    # CLI: stacpkg push 03-recipient-package/ localhost:15000/stacpkg/openaerialmap-recipient-package:v1 --plain-http --insecure
    push_package(
        recipient_package,
        registry_ref,
        plain_http=True,
        insecure=True,
    )
    # CLI: stacpkg pull localhost:15000/stacpkg/openaerialmap-recipient-package:v1 --output-dir 04-pulled-recipient-package/ --plain-http --insecure
    pull_package(
        registry_ref,
        pulled_package,
        plain_http=True,
        insecure=True,
    )
    _assert_package(pulled_package)
    assert not (pulled_package / "manifest.json").exists()
    assert read_parquet(pulled_package / "items.parquet").num_rows == OPENAERIALMAP_ITEM_COUNT
    assert read_parquet(pulled_package / "assets.lock.parquet").num_rows == (
        OPENAERIALMAP_ITEM_COUNT * len(OPENAERIALMAP_ASSET_KEYS)
    )
    assert (pulled_package / "README.md").read_text(encoding="utf-8") == recipient_readme_text
    roundtrip_summary = [
        {"fact": "source package items", "value": OPENAERIALMAP_ITEM_COUNT},
        {
            "fact": "relocated assets per package stage",
            "value": OPENAERIALMAP_ITEM_COUNT * len(OPENAERIALMAP_ASSET_KEYS),
        },
        {"fact": "included README round-tripped", "value": True},
        {"fact": "OCI package pulled", "value": True},
    ]
    # NOTEBOOK_TABLE: roundtrip_summary | fact,value
    assert roundtrip_summary == [
        {"fact": "source package items", "value": OPENAERIALMAP_ITEM_COUNT},
        {
            "fact": "relocated assets per package stage",
            "value": OPENAERIALMAP_ITEM_COUNT * len(OPENAERIALMAP_ASSET_KEYS),
        },
        {"fact": "included README round-tripped", "value": True},
        {"fact": "OCI package pulled", "value": True},
    ]

    LOGGER.info("completed OpenAerialMap full user journey e2e: run_prefix=%s", run_prefix)
