# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
from uuid import uuid4

import obstore as obs
import pyarrow as pa
import pytest

from stacpkg.arrow_io import read_parquet
from stacpkg.assets import derive_asset_lock
from stacpkg.sources import select_item_assets
from stacpkg.workflows import relocate_package, validate_package
from tests.e2e.helpers import (
    S3STORE1_BUCKET,
    S3STORE2_BUCKET,
    _s3_store,
    create_bucket,
    endpoint_env,
    put_object,
)
from tests.unit.openaerialmap_fixture import localized_openaerialmap_items

pytestmark = pytest.mark.e2e


@pytest.fixture
def stores():
    env = endpoint_env()
    for number, bucket in ((1, S3STORE1_BUCKET), (2, S3STORE2_BUCKET)):
        create_bucket(env[f"STACPKG_E2E_S3STORE{number}_ENDPOINT"], env, bucket)
    return env


def _remote_items(tmp_path, env, *, missing=False):
    items = select_item_assets(localized_openaerialmap_items(tmp_path), {"thumbnail"})
    rows = items.to_pylist()
    asset = rows[0]["assets"]["thumbnail"]
    key = f"xyz-{uuid4().hex}/thumbnail.png"
    endpoint = env["STACPKG_E2E_S3STORE1_ENDPOINT"]
    if not missing:
        source = Path(derive_asset_lock(items)["key"][0].as_py())
        put_object(endpoint, env, S3STORE1_BUCKET, key, source)
    asset.update(href=f"s3://{S3STORE1_BUCKET}/{key}", store_endpoint_url=endpoint)
    return pa.Table.from_pylist(rows)


@pytest.mark.parametrize("direction", ["local-to-s3", "s3-to-local", "s3-to-s3"])
def test_relocation_delivers_the_same_bytes(tmp_path, stores, direction):
    local = select_item_assets(localized_openaerialmap_items(tmp_path), {"thumbnail"})
    original = Path(derive_asset_lock(local)["key"][0].as_py()).read_bytes()
    items = local if direction == "local-to-s3" else _remote_items(tmp_path, stores)
    remote_target = direction != "s3-to-local"
    endpoint = stores["STACPKG_E2E_S3STORE2_ENDPOINT"] if remote_target else None
    target = (
        f"s3://{S3STORE2_BUCKET}/xyz-{uuid4().hex}/"
        if remote_target
        else str(tmp_path / "copied-assets")
    )
    package = tmp_path / "delivered.pkg"

    relocate_package(items, package, target=target, target_endpoint_url=endpoint)

    row = read_parquet(package / "assets.lock.parquet").to_pylist()[0]
    delivered = (
        obs.get(_s3_store(endpoint, S3STORE2_BUCKET), row["key"]).bytes().to_bytes()
        if remote_target
        else Path(row["key"]).read_bytes()
    )
    assert delivered == original
    assert list(validate_package(package))[-1]["valid"] is True


@pytest.mark.parametrize("failure", ["missing-source", "wrong-checksum"])
def test_failed_s3_copy_does_not_publish_a_package(tmp_path, stores, failure):
    items = _remote_items(tmp_path, stores, missing=failure == "missing-source")
    if failure == "wrong-checksum":
        rows = items.to_pylist()
        rows[0]["assets"]["thumbnail"]["file:checksum"] = "1220" + "00" * 32
        items = pa.Table.from_pylist(rows)
    package = tmp_path / "failed.pkg"

    error = FileNotFoundError if failure == "missing-source" else ValueError
    with pytest.raises(error, match=None if failure == "missing-source" else "checksum mismatch"):
        relocate_package(items, package, target=str(tmp_path / "copied-assets"))

    assert not package.exists()
