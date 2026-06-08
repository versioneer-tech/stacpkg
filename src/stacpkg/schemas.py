# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum

import pyarrow as pa

SCHEMA_KIND_KEY = b"stacpkg:schema-kind"
SCHEMA_VERSION_KEY = b"stacpkg:schema-version"
ITEM_SCHEMA_VERSION = "v1"
ASSET_LOCK_SCHEMA_VERSION = "v1"


class SchemaKind(str, Enum):
    ITEMS = "items"
    ASSET_LOCK = "asset-lock"


def _with_metadata(schema: pa.Schema, kind: SchemaKind, version: str) -> pa.Schema:
    metadata = dict(schema.metadata or {})
    metadata[SCHEMA_KIND_KEY] = kind.value.encode()
    metadata[SCHEMA_VERSION_KEY] = version.encode()
    return schema.with_metadata(metadata)


def items_schema() -> pa.Schema:
    return _with_metadata(
        pa.schema(
            [
                pa.field("id", pa.string(), nullable=False),
                pa.field("collection", pa.string()),
                pa.field("geometry_json", pa.string()),
                pa.field("bbox", pa.list_(pa.float64())),
                pa.field("datetime", pa.string()),
                pa.field("stac_version", pa.string()),
                pa.field("stac_extensions", pa.list_(pa.string())),
                pa.field("links_json", pa.string()),
                pa.field("assets_json", pa.string()),
                pa.field("properties_json", pa.string()),
                pa.field("source_href", pa.string()),
            ]
        ),
        SchemaKind.ITEMS,
        ITEM_SCHEMA_VERSION,
    )


ASSET_LOCK_CORE_COLUMNS = (
    "item_id",
    "asset_key",
    "store_type",
    "store_container",
    "store_endpoint_url",
    "key",
)
ASSET_LOCK_FACT_COLUMNS = (
    "size_bytes",
    "etag",
    "last_modified",
)
ASSET_LOCK_OPTIONAL_COLUMNS = ASSET_LOCK_FACT_COLUMNS
ASSET_LOCK_COLUMNS = ASSET_LOCK_CORE_COLUMNS + ASSET_LOCK_OPTIONAL_COLUMNS
ASSET_LOCK_FIELDS = {
    "item_id": pa.field("item_id", pa.string(), nullable=False),
    "asset_key": pa.field("asset_key", pa.string(), nullable=False),
    "store_type": pa.field("store_type", pa.string()),
    "store_container": pa.field("store_container", pa.string()),
    "store_endpoint_url": pa.field("store_endpoint_url", pa.string()),
    "key": pa.field("key", pa.string()),
    "size_bytes": pa.field("size_bytes", pa.int64()),
    "etag": pa.field("etag", pa.string()),
    "last_modified": pa.field("last_modified", pa.string()),
}


def asset_lock_schema(columns: Iterable[str] | None = None) -> pa.Schema:
    names = tuple(columns or ASSET_LOCK_COLUMNS)
    return _with_metadata(
        pa.schema([ASSET_LOCK_FIELDS[name] for name in names]),
        SchemaKind.ASSET_LOCK,
        ASSET_LOCK_SCHEMA_VERSION,
    )


def with_schema_metadata(schema: pa.Schema, kind: SchemaKind, version: str) -> pa.Schema:
    return _with_metadata(schema, kind, version)
