# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pyarrow as pa

from stacpkg.arrow_io import read_parquet, write_parquet

TEST_DATA = Path(__file__).resolve().parent
OPENAERIALMAP = TEST_DATA / "openaerialmap-central-europe-2025.original.items.parquet"
OPENAERIALMAP_S3 = TEST_DATA / "openaerialmap-central-europe-2025.s3-only.items.parquet"
OPENAERIALMAP_HTTPS_ONLY = TEST_DATA / "openaerialmap-central-europe-2025.https-only.items.parquet"
OPENAERIALMAP_ORIGINAL_ASSETS_LOCK = (
    TEST_DATA / "openaerialmap-central-europe-2025.original.assets.lock.parquet"
)
OPENAERIALMAP_S3_ASSETS_LOCK = (
    TEST_DATA / "openaerialmap-central-europe-2025.s3-only.assets.lock.parquet"
)
OPENAERIALMAP_HTTPS_ONLY_ASSETS_LOCK = (
    TEST_DATA / "openaerialmap-central-europe-2025.https-only.assets.lock.parquet"
)
OPENAERIALMAP_ASSET_DIR = TEST_DATA / "openaerialmap-assets"
OPENAERIALMAP_ITEM_COUNT = 3
OPENAERIALMAP_ASSET_KEYS = ("thumbnail", "metadata")
OPENAERIALMAP_ALL_ASSET_KEYS = (*OPENAERIALMAP_ASSET_KEYS, "visual")
OPENAERIALMAP_SELECTION_IDS = (
    "6782b6b6a07cc20001818cef",
    "67793f0b9478720001790586",
    "677978ba947872000179059c",
)


def openaerialmap_items(*, item_count: int = OPENAERIALMAP_ITEM_COUNT) -> pa.Table:
    source = read_parquet(OPENAERIALMAP)
    rows = []
    seen = set()
    selected = set(OPENAERIALMAP_SELECTION_IDS[:item_count])

    for row in source.to_pylist():
        item_id = str(row["id"])
        if item_id not in selected or item_id in seen:
            continue
        rows.append(row)
        seen.add(item_id)
        if len(rows) == item_count:
            break

    if len(rows) != item_count:
        raise ValueError(f"expected {item_count} deterministic OpenAerialMap items")
    return pa.Table.from_pylist(rows, schema=source.schema)


def write_openaerialmap_items(
    path: Path,
    *,
    item_count: int = OPENAERIALMAP_ITEM_COUNT,
) -> Path:
    write_parquet(openaerialmap_items(item_count=item_count), path)
    return path
