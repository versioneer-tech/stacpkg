# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

import pytest

from openaerialmap_fixture import localized_openaerialmap_items
from stacpkg.assets import asset_lock_table, derive_asset_lock


@pytest.mark.parametrize(
    ("options", "expected_keys"),
    [
        ({}, {"thumbnail"}),
        ({"include_metadata_assets": True}, {"thumbnail", "metadata"}),
        ({"asset_keys": {"metadata"}}, {"metadata"}),
    ],
    ids=["default", "include-metadata", "only-metadata"],
)
def test_lock_contains_only_the_selected_asset_keys(tmp_path, options, expected_keys):
    items = localized_openaerialmap_items(tmp_path)

    lock = derive_asset_lock(items, **options)

    assert {row["asset_key"] for row in lock.to_pylist()} == expected_keys


def test_lock_keeps_file_facts_and_discards_unrelated_storage_fields():
    checksum = "1220" + "00" * 32
    lock = asset_lock_table(
        [
            {
                "item_id": "scene",
                "asset_key": "image",
                "href": "s3://bucket/scene/image.tif",
                "size": 12,
                "file_checksum": checksum,
                "version_id": "storage-version",
            }
        ]
    )

    row = lock.to_pylist()[0]
    assert row["store_container"] == "bucket"
    assert row["key"] == "scene/image.tif"
    assert row["size_bytes"] == 12
    assert row["file_checksum"] == checksum
    assert row["etag"] is None
    assert "version_id" not in row
