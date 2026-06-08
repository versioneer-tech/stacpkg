# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from stacpkg.arrow_io import read_parquet, write_parquet
from stacpkg.assets import asset_lock_table, derive_asset_lock
from stacpkg.locators import href_from_location, location_from_href
from stacpkg.object_store import copy_assets
from stacpkg.schemas import ASSET_LOCK_COLUMNS
from tests.e2e.helpers import create_bucket, head_size, put_object
from tests.data.openaerialmap_data import OPENAERIALMAP_ITEM_COUNT
from tests.unit.openaerialmap_fixture import localized_openaerialmap_items

LOGGER = logging.getLogger(__name__)
OPENAERIALMAP_ASSET_KEYS = ("thumbnail",)
OPENAERIALMAP_FIXTURE_NAME = f"openaerialmap-first-{OPENAERIALMAP_ITEM_COUNT}"
SIZE_SUFFIXES = {
    "B": 1,
    "KiB": 1024,
    "MiB": 1024**2,
    "GiB": 1024**3,
}


def _s3_store_env() -> dict[str, str]:
    endpoint = os.environ.get("STACPKG_E2E_S3STORE1_ENDPOINT") or os.environ.get(
        "STACPKG_TEST_S3STORE_ENDPOINT"
    )
    if not endpoint:
        pytest.skip(
            "set STACPKG_E2E_S3STORE1_ENDPOINT or STACPKG_TEST_S3STORE_ENDPOINT "
            "to run S3 store copy performance e2e tests"
        )
    if not os.environ.get("AWS_ACCESS_KEY_ID") or not os.environ.get("AWS_SECRET_ACCESS_KEY"):
        pytest.skip(
            "set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY to run S3 store performance tests"
        )

    env = os.environ.copy()
    env.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    env.setdefault("AWS_EC2_METADATA_DISABLED", "true")
    env.setdefault("AWS_VIRTUAL_HOSTED_STYLE_REQUEST", "false")
    env["AWS_ENDPOINT_URL"] = endpoint
    env["STACPKG_OBSTORE_ALLOW_HTTP"] = "true"
    os.environ.update(
        {
            "AWS_ACCESS_KEY_ID": env["AWS_ACCESS_KEY_ID"],
            "AWS_SECRET_ACCESS_KEY": env["AWS_SECRET_ACCESS_KEY"],
            "AWS_DEFAULT_REGION": env["AWS_DEFAULT_REGION"],
            "AWS_EC2_METADATA_DISABLED": env["AWS_EC2_METADATA_DISABLED"],
            "AWS_VIRTUAL_HOSTED_STYLE_REQUEST": env["AWS_VIRTUAL_HOSTED_STYLE_REQUEST"],
            "AWS_ENDPOINT_URL": endpoint,
            "STACPKG_OBSTORE_ALLOW_HTTP": env["STACPKG_OBSTORE_ALLOW_HTTP"],
        }
    )
    LOGGER.info("configured S3 store performance endpoint: %s", endpoint)
    return env


def _create_bucket(env: dict[str, str], bucket: str) -> None:
    create_bucket(env["AWS_ENDPOINT_URL"], env, bucket)


def _safe_suffix(href: str) -> str:
    suffix = Path(urlparse(href).path).suffix
    return suffix or ".bin"


def _href(row: dict[str, object]) -> str:
    value = href_from_location(row)
    assert isinstance(value, str)
    return value


def _size_bytes(value: str) -> int:
    text = value.strip()
    for suffix, multiplier in sorted(
        SIZE_SUFFIXES.items(), key=lambda item: len(item[0]), reverse=True
    ):
        if text.endswith(suffix):
            return int(text[: -len(suffix)].strip()) * multiplier
    return int(text)


def _selected_fixture_rows(tmp_path: Path) -> list[dict[str, object]]:
    LOGGER.debug(
        "selecting first %s OpenAerialMap fixture item asset rows",
        OPENAERIALMAP_ITEM_COUNT,
    )
    rows: list[dict[str, object]] = []
    lock = derive_asset_lock(
        localized_openaerialmap_items(tmp_path, item_count=OPENAERIALMAP_ITEM_COUNT)
    )
    for row in lock.to_pylist():
        row = dict(row)
        row["fixture_name"] = OPENAERIALMAP_FIXTURE_NAME
        rows.append(row)
        LOGGER.debug(
            "selected fixture asset: fixture=%s item_id=%s asset_key=%s href=%s size=%s",
            row["fixture_name"],
            row.get("item_id"),
            row.get("asset_key"),
            _href(row),
            row.get("size_bytes"),
        )
    return rows


def _local_referenced_asset(row: dict[str, object]) -> Path:
    href = _href(row)
    parsed = urlparse(href)
    assert parsed.scheme == "file"
    path = Path(parsed.path)
    assert path.is_file()
    return path


def _seed_source_bucket(
    env: dict[str, str],
    source_bucket: str,
    target_bucket: str,
    lock_dir: Path,
    fixture_rows: list[dict[str, object]],
) -> tuple[Path, Path, int, dict[str, int]]:
    source_rows: list[dict[str, object]] = []
    target_rows: list[dict[str, object]] = []
    object_sizes: dict[str, int] = {}

    for row in fixture_rows:
        local_asset = _local_referenced_asset(row)
        key = (
            f"source/{row['fixture_name']}/"
            f"{row['item_id']}/{row['asset_key']}{_safe_suffix(_href(row))}"
        )
        target_key = f"copied/{key}"

        put_object(
            env["AWS_ENDPOINT_URL"],
            env,
            source_bucket,
            key,
            local_asset,
        )
        LOGGER.debug(
            "seeded source object: bucket=%s key=%s size=%s",
            source_bucket,
            key,
            local_asset.stat().st_size,
        )

        source_href = f"s3://{source_bucket}/{key}"
        target_href = f"s3://{target_bucket}/{target_key}"

        source_row = dict(row)
        source_row.update(
            {
                **location_from_href(source_href),
                "size_bytes": local_asset.stat().st_size,
            }
        )
        target_row = dict(row)
        target_row.update(
            {
                **location_from_href(target_href),
                "size_bytes": local_asset.stat().st_size,
            }
        )
        source_rows.append(source_row)
        target_rows.append(target_row)
        object_sizes[target_key] = local_asset.stat().st_size

    total_bytes = sum(object_sizes.values())
    source_lock = lock_dir / "source.assets.lock.parquet"
    target_lock = lock_dir / "target.assets.lock.parquet"
    write_parquet(asset_lock_table(source_rows), source_lock)
    write_parquet(asset_lock_table(target_rows), target_lock)
    LOGGER.info(
        "seeded source and target locks: source_lock=%s target_lock=%s rows=%s total_bytes=%s",
        source_lock,
        target_lock,
        len(source_rows),
        total_bytes,
    )
    return source_lock, target_lock, total_bytes, object_sizes


@pytest.mark.e2e
@pytest.mark.performance
def test_assets_copy_uses_s3_store_for_referenced_fixture_assets_with_runtime_budget(tmp_path):
    env = _s3_store_env()
    suffix = uuid4().hex[:12]
    source_bucket = f"stacpkg-source-{suffix}"
    target_bucket = f"stacpkg-target-{suffix}"
    output_lock = tmp_path / "asset-lock.parquet"
    fixture_rows = _selected_fixture_rows(tmp_path)
    LOGGER.info(
        "starting S3 store copy performance e2e: source_bucket=%s target_bucket=%s tmp_path=%s",
        source_bucket,
        target_bucket,
        tmp_path,
    )

    _create_bucket(env, source_bucket)
    _create_bucket(env, target_bucket)
    source_lock, target_lock, total_bytes, object_sizes = _seed_source_bucket(
        env,
        source_bucket,
        target_bucket,
        tmp_path,
        fixture_rows,
    )

    minimum_bytes = int(os.environ.get("STACPKG_TEST_S3STORE_MIN_BYTES", "1000000"))
    max_seconds = float(os.environ.get("STACPKG_TEST_S3STORE_MAX_SECONDS", "120"))
    max_workers = os.environ.get("STACPKG_TEST_S3STORE_MAX_WORKERS", "4")
    memory_limit = os.environ.get("STACPKG_TEST_S3STORE_MEMORY_LIMIT", "512MiB")
    chunk_size = os.environ.get("STACPKG_TEST_S3STORE_CHUNK_SIZE", "8MiB")
    LOGGER.info(
        "copy performance parameters: total_bytes=%s minimum_bytes=%s max_seconds=%s "
        "max_workers=%s memory_limit=%s chunk_size=%s",
        total_bytes,
        minimum_bytes,
        max_seconds,
        max_workers,
        memory_limit,
        chunk_size,
    )
    assert total_bytes >= minimum_bytes

    LOGGER.info("running stacpkg copy library call for performance e2e")
    start = time.perf_counter()
    # CLI: stacpkg asset-lock from-parquet source.assets.lock.parquet
    #      | stacpkg asset-lock relocate --destination-lock target.assets.lock.parquet --max-workers 4 --memory-limit-bytes 512MiB --chunk-size-bytes 8MiB
    #      | stacpkg asset-lock to-parquet asset-lock.parquet
    write_parquet(
        copy_assets(
            read_parquet(source_lock),
            read_parquet(target_lock),
            max_workers=int(max_workers),
            memory_limit_bytes=_size_bytes(memory_limit),
            chunk_size_bytes=_size_bytes(chunk_size),
            put_max_concurrency=1,
        ),
        output_lock,
    )
    elapsed = time.perf_counter() - start
    LOGGER.info("stacpkg copy library call completed in %.2fs output=%s", elapsed, output_lock)

    copied_table = read_parquet(output_lock)
    copied = copied_table.to_pylist()
    LOGGER.info(
        "copied lock summary: rows=%s total_row_bytes=%s",
        len(copied),
        sum(row["size_bytes"] or 0 for row in copied),
    )
    assert elapsed < max_seconds
    assert all(_href(row).startswith(f"s3://{target_bucket}/") for row in copied)
    assert copied_table.schema.names == list(ASSET_LOCK_COLUMNS)
    assert sum(row["size_bytes"] or 0 for row in copied) >= total_bytes
    assert "copy_max_workers" not in copied_table.schema.names

    for key, expected_size in object_sizes.items():
        LOGGER.debug(
            "verifying copied object: bucket=%s key=%s expected_size=%s",
            target_bucket,
            key,
            expected_size,
        )
        assert head_size(env["AWS_ENDPOINT_URL"], env, target_bucket, key) == expected_size
    LOGGER.info("completed S3 store copy performance e2e: elapsed=%.2fs", elapsed)
