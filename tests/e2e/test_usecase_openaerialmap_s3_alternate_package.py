# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import pyarrow as pa
import pytest

from stacpkg.arrow_io import read_parquet, write_parquet
from stacpkg.assets import derive_asset_lock
from stacpkg.dataset import build_package
from stacpkg.locators import href_from_location
from stacpkg.object_store import validate_assets
from stacpkg.report import package_inspect_markdown
from tests.data.openaerialmap_data import OPENAERIALMAP_S3, OPENAERIALMAP_SELECTION_IDS

OPENAERIALMAP_ITEM_COUNT = len(OPENAERIALMAP_SELECTION_IDS)
OPENAERIALMAP_ASSET_KEYS = ("thumbnail", "visual")
OPENAERIALMAP_S3_BUCKET = "oin-hotosm-temp"
UNSIGNED_S3_ENV = {
    "AWS_SKIP_SIGNATURE": "true",
    "AWS_EC2_METADATA_DISABLED": "true",
    "AWS_DEFAULT_REGION": "us-east-1",
}
PUBLIC_S3_ENDPOINT_ENV = (
    "AWS_ENDPOINT_URL",
    "AWS_ENDPOINT",
    "STACPKG_S3_ENDPOINTS_JSON",
    "STACPKG_S3_ENDPOINT_OIN_HOTOSM_TEMP",
)


def _href(row: dict[str, object]) -> str:
    value = href_from_location(row)
    assert isinstance(value, str)
    return value


def _s3_key(href: str) -> str:
    parsed = urlparse(href)
    assert parsed.scheme == "s3"
    assert parsed.netloc == OPENAERIALMAP_S3_BUCKET
    return parsed.path.lstrip("/")


def _selected_s3_items() -> pa.Table:
    source = read_parquet(OPENAERIALMAP_S3)
    selected_ids = set(OPENAERIALMAP_SELECTION_IDS)
    rows = [row for row in source.to_pylist() if row["id"] in selected_ids]
    if len(rows) != OPENAERIALMAP_ITEM_COUNT:
        raise ValueError("expected deterministic OpenAerialMap S3 items")
    return pa.Table.from_pylist(rows, schema=source.schema)


@contextmanager
def _unsigned_public_s3_requests():
    names = tuple(UNSIGNED_S3_ENV) + PUBLIC_S3_ENDPOINT_ENV
    previous = {name: os.environ.get(name) for name in names}
    try:
        for name in PUBLIC_S3_ENDPOINT_ENV:
            os.environ.pop(name, None)
        os.environ.update(UNSIGNED_S3_ENV)
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@pytest.mark.e2e
def test_openaerialmap_package_uses_public_s3_alternate_hrefs_with_unsigned_requests(
    tmp_path: Path,
) -> None:
    source_items = tmp_path / "openaerialmap-s3-reachable.items.parquet"
    metadata_assets_lock = tmp_path / "openaerialmap-s3.metadata.assets.lock.parquet"
    object_assets_lock = tmp_path / "openaerialmap-s3.object.assets.lock.parquet"
    package_dir = tmp_path / "openaerialmap-s3.pkg"
    inspect_report = tmp_path / "inspect.md"

    # NOTEBOOK: ## Sample OpenAerialMap Items And Promote S3 Alternates
    # NOTEBOOK: The source fixture stores HTTP/HTTPS asset hrefs plus
    # NOTEBOOK: `alternate.s3.href` values. The S3-only fixture is prepared with
    # NOTEBOOK: `stacpkg items promote-alternate --alternate-key s3`
    # NOTEBOOK: so this e2e starts from primary public S3 asset hrefs.
    selected_items = _selected_s3_items()
    write_parquet(selected_items, source_items)
    s3_items = read_parquet(source_items)
    selected_rows = s3_items.select(["id", "collection", "datetime", "title"])
    assert selected_rows.num_rows == OPENAERIALMAP_ITEM_COUNT
    # NOTEBOOK_TABLE: selected_rows

    metadata_assets = derive_asset_lock(s3_items, probe_metadata=False)
    write_parquet(metadata_assets, metadata_assets_lock)
    metadata_rows = metadata_assets.to_pylist()
    s3_asset_examples = [
        {
            "item_id": row["item_id"],
            "asset_key": row["asset_key"],
            "s3_href": _href(row),
            "aws_cli": (
                "aws s3api head-object "
                f"--bucket {OPENAERIALMAP_S3_BUCKET} --key {_s3_key(_href(row))} "
                "--no-sign-request"
            ),
        }
        for row in metadata_rows[:3]
    ]
    assert len(s3_asset_examples) == 3
    # NOTEBOOK_TABLE: s3_asset_examples | item_id,asset_key,s3_href,aws_cli
    assert s3_items.num_rows == OPENAERIALMAP_ITEM_COUNT
    assert len(metadata_rows) == OPENAERIALMAP_ITEM_COUNT * len(OPENAERIALMAP_ASSET_KEYS)
    assert {row["asset_key"] for row in metadata_rows} == set(OPENAERIALMAP_ASSET_KEYS)
    assert all(_href(row).startswith(f"s3://{OPENAERIALMAP_S3_BUCKET}/") for row in metadata_rows)

    # NOTEBOOK_OUTPUT: sampled OpenAerialMap items: 3
    # NOTEBOOK_OUTPUT: promoted asset hrefs: alternate.s3.href
    # NOTEBOOK_OUTPUT: unsigned AWS CLI equivalent: aws s3api head-object --no-sign-request
    # NOTEBOOK: ## Lock And Validate Public S3 Object Metadata
    # NOTEBOOK: The test runs this with unsigned public S3 access.
    # CLI: stacpkg items from-parquet openaerialmap-s3-reachable.items.parquet
    #      | stacpkg asset-lock derive
    #      | stacpkg asset-lock to-parquet openaerialmap-s3.object.assets.lock.parquet
    with _unsigned_public_s3_requests():
        write_parquet(
            derive_asset_lock(
                s3_items,
                probe_metadata=True,
                keep_going=False,
                max_workers=4,
            ),
            object_assets_lock,
        )
        validation_results = validate_assets(
            read_parquet(object_assets_lock),
        )

    object_rows = read_parquet(object_assets_lock).to_pylist()
    validation_summary = [
        {"name": "locked public S3 assets", "count": len(object_rows)},
        {
            "name": "valid assets",
            "count": sum(1 for result in validation_results if result["valid"]),
        },
        {
            "name": "S3 buckets",
            "count": len({row["store_container"] for row in object_rows}),
        },
    ]
    assert validation_summary[1]["count"] == len(object_rows)
    # NOTEBOOK_TABLE: object_rows | item_id,asset_key,store_type,store_container,key,size_bytes,etag,last_modified
    # NOTEBOOK_TABLE: validation_summary | name,count
    assert len(object_rows) == OPENAERIALMAP_ITEM_COUNT * len(OPENAERIALMAP_ASSET_KEYS)
    assert {row["store_type"] for row in object_rows} == {"s3"}
    assert {row["store_container"] for row in object_rows} == {OPENAERIALMAP_S3_BUCKET}
    assert all(row["size_bytes"] > 0 for row in object_rows)
    assert all(row["etag"] for row in object_rows)
    assert all(result["valid"] for result in validation_results)

    # NOTEBOOK_OUTPUT: object metadata lock rows: 6
    # NOTEBOOK_OUTPUT: validation: all public S3 alternates match object metadata with unsigned requests
    # NOTEBOOK: ## Build A Package From The S3-Backed Selection
    # CLI: stacpkg items from-parquet openaerialmap-s3-reachable.items.parquet
    #      | stacpkg build openaerialmap-s3.pkg
    #      --asset-lock openaerialmap-s3.object.assets.lock.arrow
    build_package(
        source_items,
        package_dir,
        asset_lock=read_parquet(object_assets_lock),
    )
    inspect_report.write_text(package_inspect_markdown(package_dir), encoding="utf-8")

    package_assets = read_parquet(package_dir / "assets.lock.parquet").to_pylist()
    package_summary = [
        {"name": "package items", "value": read_parquet(package_dir / "items.parquet").num_rows},
        {"name": "package assets", "value": len(package_assets)},
    ]
    assert package_summary == [
        {"name": "package items", "value": OPENAERIALMAP_ITEM_COUNT},
        {
            "name": "package assets",
            "value": OPENAERIALMAP_ITEM_COUNT * len(OPENAERIALMAP_ASSET_KEYS),
        },
    ]
    # NOTEBOOK_TABLE: package_summary | name,value
    # NOTEBOOK_TABLE: package_assets | item_id,asset_key,store_type,store_container,key,size_bytes
    assert not (package_dir / "manifest.json").exists()
    assert read_parquet(package_dir / "items.parquet").num_rows == OPENAERIALMAP_ITEM_COUNT
    assert len(package_assets) == OPENAERIALMAP_ITEM_COUNT * len(OPENAERIALMAP_ASSET_KEYS)
    assert {row["store_type"] for row in package_assets} == {"s3"}
    assert f"- Items: {OPENAERIALMAP_ITEM_COUNT}" in inspect_report.read_text(encoding="utf-8")
    assert (
        f"- Assets: {OPENAERIALMAP_ITEM_COUNT * len(OPENAERIALMAP_ASSET_KEYS)}"
        in inspect_report.read_text(encoding="utf-8")
    )
