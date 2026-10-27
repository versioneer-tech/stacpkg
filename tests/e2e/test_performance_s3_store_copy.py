# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

import logging
import os
import time
from pathlib import Path
from uuid import uuid4

import obstore as obs
import pytest

from stacpkg.arrow_io import read_parquet
from stacpkg.assets import derive_asset_lock
from stacpkg.sources import select_item_assets
from stacpkg.workflows import relocate_package
from tests.data.openaerialmap_data import OPENAERIALMAP_ITEM_COUNT
from tests.e2e.helpers import (
    S3STORE1_BUCKET,
    S3STORE2_BUCKET,
    _s3_store,
    create_bucket,
    endpoint_env,
)
from tests.unit.openaerialmap_fixture import localized_openaerialmap_items


@pytest.mark.e2e
@pytest.mark.performance
def test_s3_copy_preserves_fixture_bytes_within_the_runtime_budget(tmp_path):
    env = endpoint_env()
    source_endpoint = env["STACPKG_E2E_S3STORE1_ENDPOINT"]
    target_endpoint = env["STACPKG_E2E_S3STORE2_ENDPOINT"]
    create_bucket(source_endpoint, env, S3STORE1_BUCKET)
    create_bucket(target_endpoint, env, S3STORE2_BUCKET)
    items = select_item_assets(
        localized_openaerialmap_items(tmp_path, item_count=OPENAERIALMAP_ITEM_COUNT),
        {"thumbnail"},
    )
    expected = {
        row["item_id"]: Path(row["key"]).read_bytes()
        for row in derive_asset_lock(items).to_pylist()
    }
    assert sum(map(len, expected.values())) >= 1_000_000
    prefix = f"xyz-{uuid4().hex}"
    provider = tmp_path / "provider.pkg"
    relocate_package(
        items,
        provider,
        target=f"s3://{S3STORE1_BUCKET}/{prefix}/",
        target_endpoint_url=source_endpoint,
    )
    recipient = tmp_path / "recipient.pkg"

    start = time.perf_counter()
    relocate_package(
        provider / "items.parquet",
        recipient,
        target=f"s3://{S3STORE2_BUCKET}/{prefix}/",
        target_endpoint_url=target_endpoint,
        memory_limit_bytes=64 * 1024**2,
    )
    elapsed = time.perf_counter() - start

    assert elapsed < float(os.environ.get("STACPKG_TEST_S3STORE_MAX_SECONDS", "120"))
    rows = read_parquet(recipient / "assets.lock.parquet").to_pylist()
    assert len(rows) == len(expected)
    store = _s3_store(target_endpoint, S3STORE2_BUCKET)
    for row in rows:
        assert obs.get(store, row["key"]).bytes().to_bytes() == expected[row["item_id"]]
    logging.getLogger(__name__).info(
        "Copied and checked %s bytes in %.2fs", sum(map(len, expected.values())), elapsed
    )
