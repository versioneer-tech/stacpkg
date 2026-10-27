# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urlparse

import pytest
import pyarrow as pa

from stacpkg.assets import (
    asset_lock_table,
    derive_asset_lock,
    plan_copy_assets,
    relocate_asset_locations,
)
from stacpkg.object_store import (
    _store_from_url,
    copy_assets,
    stat_assets,
    validate_assets,
)
from stacpkg.locators import href_from_location, location_from_href
from stacpkg.projection import project_item_assets, promote_alternate_asset_hrefs
from openaerialmap_fixture import LOCAL_OPENAERIALMAP_ASSET_KEYS, localized_openaerialmap_items

COPY_TEST_ASSET_KEYS = ("metadata", "thumbnail")


def _source_items(tmp_path, *, item_count: int = 1, copy_asset_keys=None):
    return localized_openaerialmap_items(
        tmp_path,
        item_count=item_count,
        copy_asset_keys=copy_asset_keys,
    )


def _source_assets(tmp_path, *, item_count: int = 1, copy_asset_keys=None):
    return derive_asset_lock(
        _source_items(
            tmp_path,
            item_count=item_count,
            copy_asset_keys=copy_asset_keys,
        ),
        include_metadata_assets=True,
    )


def _copy_test_assets(items):
    return derive_asset_lock(items, asset_keys=set(COPY_TEST_ASSET_KEYS))


def _source_paths(assets) -> dict[str, Path]:
    return {
        row["asset_key"]: Path(urlparse(_href(row)).path)
        for row in assets.to_pylist()
        if row["asset_key"] in LOCAL_OPENAERIALMAP_ASSET_KEYS
    }


def _href(row: dict[str, object]) -> str:
    value = href_from_location(row)
    assert isinstance(value, str)
    return value


def _set_href(row: dict[str, object], href: str) -> dict[str, object]:
    row.update(location_from_href(href))
    return row


def _first_item_id(items) -> str:
    return str(items.to_pylist()[0]["id"])


def test_metadata_checks_work_inside_a_notebook_event_loop(tmp_path):
    assets = _source_assets(tmp_path)

    async def notebook():
        return stat_assets(assets)

    observed = asyncio.run(notebook())
    assert observed.column("size_bytes").to_pylist() == assets.column("size_bytes").to_pylist()


def test_copy_preserves_bytes_with_a_small_buffer_budget(tmp_path):
    items = _source_items(tmp_path)
    destination = tmp_path / "mirror"
    assets = _copy_test_assets(items)
    planned_assets = plan_copy_assets(assets, target=destination.as_uri())

    copied = copy_assets(
        assets,
        planned_assets,
        max_workers=8,
        memory_limit_bytes=20 * 1024,
        chunk_size_bytes=4 * 1024,
    )

    rows = sorted(copied.to_pylist(), key=lambda row: row["asset_key"])
    row_by_key = {row["asset_key"]: row for row in rows}
    item_id = _first_item_id(items)
    source_paths = _source_paths(assets)
    assert (destination / item_id / "metadata.json").read_bytes() == source_paths[
        "metadata"
    ].read_bytes()
    assert (destination / item_id / "thumbnail.png").read_bytes() == source_paths[
        "thumbnail"
    ].read_bytes()
    assert {row["asset_key"] for row in rows} == set(COPY_TEST_ASSET_KEYS)
    assert _href(row_by_key["thumbnail"]).endswith(f"/{item_id}/thumbnail.png")


def test_copy_assets_rejects_memory_limit_below_streaming_budget(tmp_path):
    assets = _source_assets(tmp_path)
    planned_assets = plan_copy_assets(assets, target=(tmp_path / "mirror").as_uri())

    with pytest.raises(ValueError, match="memory limit"):
        copy_assets(
            assets,
            planned_assets,
            max_workers=2,
            memory_limit_bytes=4 * 1024,
            chunk_size_bytes=4 * 1024,
            put_max_concurrency=1,
        )


def test_stat_assets_records_object_metadata_and_validate_detects_tampering(tmp_path):
    assets = _source_assets(tmp_path, copy_asset_keys={"thumbnail"})
    source_paths = _source_paths(assets)

    asset_stats = stat_assets(assets)

    rows = {row["asset_key"]: row for row in asset_stats.to_pylist()}
    assert rows["thumbnail"]["size_bytes"] == source_paths["thumbnail"].stat().st_size
    assert rows["metadata"]["size_bytes"] == source_paths["metadata"].stat().st_size

    validated = validate_assets(asset_stats)
    assert all(row["valid"] for row in validated)

    source_paths["thumbnail"].write_bytes(b"changed rendered preview bytes")
    tamper_checked = validate_assets(
        asset_stats,
        asset_keys={"thumbnail"},
        keep_going=True,
    )

    assert len(tamper_checked) == 1
    assert tamper_checked[0]["asset_key"] == "thumbnail"
    assert tamper_checked[0]["valid"] is False
    assert any("size mismatch" in error for error in tamper_checked[0]["errors"])


def test_keep_going_keeps_a_missing_asset_in_the_lock(tmp_path):
    assets = _source_assets(tmp_path, copy_asset_keys={"thumbnail"})
    original = {row["asset_key"]: row for row in assets.to_pylist()}
    Path(original["thumbnail"]["key"]).unlink()

    observed = stat_assets(assets, keep_going=True)

    rows = {row["asset_key"]: row for row in observed.to_pylist()}
    assert rows.keys() == original.keys()
    assert rows["thumbnail"] == original["thumbnail"]
    assert rows["metadata"]["size_bytes"] > 0


def test_stat_assets_rejects_empty_location_without_keep_going():
    assets = asset_lock_table([{"item_id": "item-1", "asset_key": "data"}])

    with pytest.raises(ValueError, match="location is empty"):
        stat_assets(assets)


def test_validate_assets_detects_etag_mismatch(monkeypatch):
    assets = asset_lock_table(
        [
            {
                "item_id": "item-1",
                "asset_key": "data",
                "href": "s3://bucket/data.bin",
                "etag": '"old"',
            }
        ]
    )

    async def fake_head_href(_href: str) -> dict[str, object]:
        return {"e_tag": '"new"'}

    monkeypatch.setattr("stacpkg.object_store._head_href", fake_head_href)

    checked = validate_assets(assets)

    assert checked[0]["valid"] is False
    assert checked[0]["errors"] == ['etag mismatch: expected "old", actual "new"']


def test_offline_lock_derivation_does_not_need_the_source_files(tmp_path):
    items = _source_items(tmp_path, copy_asset_keys={"thumbnail", "metadata"})
    for asset in items.to_pylist()[0]["assets"].values():
        if (asset.get("href") or "").startswith("file://"):
            Path(urlparse(asset["href"]).path).unlink()

    lock = derive_asset_lock(items, probe_metadata=False, include_metadata_assets=True)

    assert {row["asset_key"] for row in lock.to_pylist()} == {"thumbnail", "metadata"}
    assert all(row["file_checksum"] is None for row in lock.to_pylist())


def test_derive_asset_lock_can_filter_items_and_asset_keys(tmp_path):
    items = _source_items(tmp_path, item_count=2)
    item_id = _first_item_id(items)

    assets = derive_asset_lock(
        items,
        item_ids={item_id},
        asset_keys={"thumbnail"},
    )

    rows = assets.to_pylist()
    assert len(rows) == 1
    assert rows[0]["item_id"] == item_id
    assert rows[0]["asset_key"] == "thumbnail"


def test_s3_store_uses_bucket_scoped_credentials(monkeypatch):
    monkeypatch.setenv("STACPKG_S3_ENDPOINT_SOURCE_BUCKET", "http://127.0.0.1:19000")
    monkeypatch.setenv("STACPKG_S3_ACCESS_KEY_ID_SOURCE_BUCKET", "source-access")
    monkeypatch.setenv("STACPKG_S3_SECRET_ACCESS_KEY_SOURCE_BUCKET", "source-secret")
    monkeypatch.setenv("STACPKG_S3_SESSION_TOKEN_SOURCE_BUCKET", "source-token")
    monkeypatch.setenv("STACPKG_S3_REGION_SOURCE_BUCKET", "eu-central-1")
    monkeypatch.setenv("STACPKG_S3_ENDPOINT_TARGET_BUCKET", "http://127.0.0.1:19010")
    monkeypatch.setenv("STACPKG_S3_ACCESS_KEY_ID_TARGET_BUCKET", "target-access")
    monkeypatch.setenv("STACPKG_S3_SECRET_ACCESS_KEY_TARGET_BUCKET", "target-secret")

    source_store = _store_from_url("s3://source-bucket")
    target_store = _store_from_url("s3://target-bucket")

    assert source_store.config["endpoint"] == "http://127.0.0.1:19000"
    assert source_store.config["access_key_id"] == "source-access"
    assert source_store.config["secret_access_key"] == "source-secret"
    assert source_store.config["session_token"] == "source-token"
    assert source_store.config["region"] == "eu-central-1"
    assert target_store.config["endpoint"] == "http://127.0.0.1:19010"
    assert target_store.config["access_key_id"] == "target-access"
    assert target_store.config["secret_access_key"] == "target-secret"


def test_s3_store_row_endpoint_overrides_environment_endpoint(monkeypatch):
    monkeypatch.setenv("STACPKG_S3_ENDPOINT_SOURCE_BUCKET", "http://127.0.0.1:19000")

    store = _store_from_url(
        "s3://source-bucket",
        endpoint_url="http://127.0.0.1:19020",
    )

    assert store.config["endpoint"] == "http://127.0.0.1:19020"


def test_s3_store_rejects_partial_bucket_scoped_credentials(monkeypatch):
    monkeypatch.setenv("STACPKG_S3_ACCESS_KEY_ID_SOURCE_BUCKET", "source-access")

    with pytest.raises(ValueError, match="STACPKG_S3_SECRET_ACCESS_KEY_SOURCE_BUCKET"):
        _store_from_url("s3://source-bucket")


def test_plan_copy_assets_records_target_endpoint_url_for_s3() -> None:
    assets = asset_lock_table(
        [
            {
                "item_id": "item-1",
                "asset_key": "visual",
                "href": "https://example.com/source/visual.tif",
                "size_bytes": 123,
                "etag": '"source"',
            }
        ]
    )

    planned = plan_copy_assets(
        assets,
        target="s3://bucket/prefix/",
        target_endpoint_url="https://s3.amazonaws.com",
    )

    row = planned.to_pylist()[0]
    assert row["store_type"] == "s3"
    assert row["store_container"] == "bucket"
    assert row["key"] == "prefix/item-1/visual.tif"
    assert row["store_endpoint_url"] == "https://s3.amazonaws.com"
    assert row["size_bytes"] == 123
    assert row["etag"] is None


def test_relocate_asset_locations_uses_store_column_destination() -> None:
    assets = asset_lock_table(
        [
            {
                "item_id": "item-1",
                "asset_key": "visual",
                "href": "https://example.com/source/visual.tif",
                "size_bytes": 123,
                "etag": '"source"',
            }
        ]
    )

    relocated = relocate_asset_locations(
        assets,
        store_type="s3",
        store_container="bucket",
        store_endpoint_url="s3.amazonaws.com",
        key="prefix/",
        layout="source-key",
    )

    row = relocated.to_pylist()[0]
    assert row["store_type"] == "s3"
    assert row["store_container"] == "bucket"
    assert row["store_endpoint_url"] == "https://s3.amazonaws.com"
    assert row["key"] == "prefix/source/visual.tif"
    assert row["size_bytes"] == 123
    assert row["etag"] is None


def test_project_item_assets_writes_lock_endpoint_into_alternate() -> None:
    items = pa.Table.from_pylist(
        [
            {
                "id": "item-1",
                "assets": {"data": {"href": "https://example.com/source/data.bin"}},
            }
        ]
    )
    target_assets = asset_lock_table(
        [
            {
                "item_id": "item-1",
                "asset_key": "data",
                "href": "s3://bucket/data.bin",
                "store_endpoint_url": "http://127.0.0.1:9000/",
            }
        ]
    )

    projected = project_item_assets(
        items,
        target_assets,
        strategy="set-alternate",
        alternate_key="s3",
    )

    alternate = projected.to_pylist()[0]["assets"]["data"]["alternate"]["s3"]
    assert alternate["href"] == "s3://bucket/data.bin"
    assert alternate["store_endpoint_url"] == "http://127.0.0.1:9000"


def test_promote_s3_alternate_infers_endpoint_from_matching_http_primary() -> None:
    items = pa.Table.from_pylist(
        [
            {
                "id": "item-1",
                "assets": {
                    "data": {
                        "href": "https://bucket.s3.us-east-1.amazonaws.com/path/data.bin",
                        "alternate": {
                            "s3": {
                                "href": "s3://bucket/path/data.bin",
                                "alternate:name": "S3",
                            }
                        },
                    }
                },
            }
        ]
    )

    promoted = promote_alternate_asset_hrefs(
        items,
        alternate_key="s3",
        drop_alternates=True,
    )

    asset = promoted.to_pylist()[0]["assets"]["data"]
    assert asset["href"] == "s3://bucket/path/data.bin"
    assert asset["store_endpoint_url"] == "https://s3.us-east-1.amazonaws.com"
    assert "alternate" not in asset

    locked = derive_asset_lock(promoted, probe_metadata=False)
    row = locked.to_pylist()[0]
    assert row["store_type"] == "s3"
    assert row["store_container"] == "bucket"
    assert row["store_endpoint_url"] == "https://s3.us-east-1.amazonaws.com"


def test_copy_plan_leaves_assets_outside_the_source_prefix_unchanged(tmp_path):
    assets = derive_asset_lock(_source_items(tmp_path), include_metadata_assets=True)
    source_base = (tmp_path / "source").as_uri()
    source_other_base = (tmp_path / "source-other").as_uri()
    target_base = (tmp_path / "target-products").as_uri()
    rows = []
    for row in assets.to_pylist():
        row = dict(row)
        if row["asset_key"] == "thumbnail":
            _set_href(row, f"{source_base}/thumbnail.png")
        else:
            _set_href(row, f"{source_other_base}/{row['asset_key']}.bin")
        rows.append(row)

    planned = plan_copy_assets(
        asset_lock_table(rows),
        source_prefix=source_base,
        target=target_base,
    )

    planned_rows = {row["asset_key"]: row for row in planned.to_pylist()}
    assert _href(planned_rows["thumbnail"]).startswith(f"{target_base}/")
    assert _href(planned_rows["metadata"]) == f"{source_other_base}/metadata.bin"


def test_copy_assets_skips_rows_that_already_point_to_source_href(tmp_path):
    assets = derive_asset_lock(_source_items(tmp_path))

    copied = copy_assets(assets, assets)

    rows = copied.to_pylist()
    assert {_href(row) for row in rows} == {_href(row) for row in assets.to_pylist()}


def test_project_item_assets_matches_asset_locks_by_item_and_asset_key(tmp_path):
    items = _source_items(tmp_path, item_count=2)
    destination = tmp_path / "mirror"
    planned_assets = plan_copy_assets(
        derive_asset_lock(items, include_metadata_assets=True),
        target=destination.as_uri(),
    )

    projected = project_item_assets(
        items,
        planned_assets,
        strategy="set-alternate",
        alternate_key="mirrored",
    )

    row_by_identity = {
        (row["item_id"], row["asset_key"]): row for row in planned_assets.to_pylist()
    }
    for item in projected.to_pylist():
        item_id = item["id"]
        for asset_key in LOCAL_OPENAERIALMAP_ASSET_KEYS:
            assert item["assets"][asset_key]["alternate"]["mirrored"]["href"] == _href(
                row_by_identity[(item_id, asset_key)]
            )


def test_project_item_assets_can_promote_alternate_href_and_drop_alternates(tmp_path):
    items = _source_items(tmp_path)
    with_alternate = project_item_assets(
        items,
        plan_copy_assets(
            derive_asset_lock(items),
            target="s3://bucket/openaerialmap-copy/",
        ),
        strategy="set-alternate",
        alternate_key="s3",
    )

    projected = project_item_assets(
        with_alternate,
        strategy="set-href-from-alternate",
        alternate_key="s3",
        drop_alternates=True,
    )

    asset = projected.to_pylist()[0]["assets"]["thumbnail"]
    assert asset["href"].startswith("s3://bucket/openaerialmap-copy/")
    assert asset["alternate:name"] == "s3"
    assert "alternate" not in asset
