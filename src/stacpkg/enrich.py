# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from urllib.parse import urlparse

import pyarrow as pa

from stacpkg.locators import href_from_location

FILE_EXTENSION = "https://stac-extensions.github.io/file/v2.1.0/schema.json"
ALTERNATE_ASSETS_EXTENSION = "https://stac-extensions.github.io/alternate-assets/v1.2.0/schema.json"
STORE_ENDPOINT_URL_FIELD = "store_endpoint_url"


def _field_with_type(field: pa.Field, field_type: pa.DataType) -> pa.Field:
    return pa.field(
        field.name,
        field_type,
        nullable=field.nullable,
        metadata=field.metadata,
    )


def _merge_struct_type(
    struct_type: pa.StructType,
    extra_fields: list[pa.Field],
) -> pa.StructType:
    fields = {field.name: field for field in struct_type}
    order = [field.name for field in struct_type]
    for extra in extra_fields:
        current = fields.get(extra.name)
        if current is None:
            fields[extra.name] = extra
            order.append(extra.name)
            continue
        if pa.types.is_struct(current.type) and pa.types.is_struct(extra.type):
            fields[extra.name] = _field_with_type(
                current,
                _merge_struct_type(current.type, list(extra.type)),
            )
    return pa.struct([fields[name] for name in order])


def _alternate_entry_field(alternate_key: str) -> pa.Field:
    return pa.field(
        alternate_key,
        pa.struct(
            [
                pa.field("href", pa.string()),
                pa.field(STORE_ENDPOINT_URL_FIELD, pa.string()),
            ]
        ),
    )


def _alternate_field(alternate_key: str) -> pa.Field:
    return pa.field("alternate", pa.struct([_alternate_entry_field(alternate_key)]))


def _enriched_items_schema(schema: pa.Schema, *, alternate_key: str | None) -> pa.Schema:
    if "assets_json" in schema.names:
        return schema
    assets_index = schema.get_field_index("assets")
    if assets_index == -1:
        return schema
    assets_field = schema.field(assets_index)
    if not pa.types.is_struct(assets_field.type):
        return schema

    extra_fields = [pa.field("file:size", pa.int64())]
    if alternate_key:
        extra_fields.append(_alternate_field(alternate_key))

    asset_fields: list[pa.Field] = []
    for asset_field in assets_field.type:
        if pa.types.is_struct(asset_field.type):
            asset_fields.append(
                _field_with_type(
                    asset_field,
                    _merge_struct_type(asset_field.type, extra_fields),
                )
            )
        else:
            asset_fields.append(asset_field)

    return schema.set(
        assets_index,
        _field_with_type(assets_field, pa.struct(asset_fields)),
    )


def _endpoint_url(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    endpoint = value.strip().rstrip("/")
    if not urlparse(endpoint).scheme:
        endpoint = f"https://{endpoint}"
    return endpoint


def _lock_index(asset_lock: pa.Table) -> dict[tuple[str, str], dict[str, object]]:
    index: dict[tuple[str, str], dict[str, object]] = {}
    for row in asset_lock.to_pylist():
        item_id = row.get("item_id")
        asset_key = row.get("asset_key")
        if isinstance(item_id, str) and isinstance(asset_key, str):
            index[(item_id, asset_key)] = row
    return index


def _add_extension(extensions: object, extension: str) -> list[str]:
    values = [value for value in extensions or [] if isinstance(value, str)]
    if extension not in values:
        values.append(extension)
    return values


def _enrich_asset(
    asset: Mapping[str, object],
    lock: dict[str, object],
    *,
    alternate_key: str | None,
) -> tuple[dict[str, object], bool, bool]:
    enriched = copy.deepcopy(dict(asset))
    used_file_extension = False
    used_alternate_extension = False

    enriched.pop("file:checksum", None)

    size = lock.get("size_bytes")
    if size is not None:
        enriched["file:size"] = int(size)
        used_file_extension = True

    href = href_from_location(lock)
    if alternate_key and isinstance(href, str) and href:
        alternate = enriched.setdefault("alternate", {})
        if isinstance(alternate, dict):
            alternate_asset: dict[str, object] = {"href": href}
            endpoint_url = _endpoint_url(lock.get(STORE_ENDPOINT_URL_FIELD))
            if endpoint_url:
                alternate_asset[STORE_ENDPOINT_URL_FIELD] = endpoint_url
            alternate[alternate_key] = alternate_asset
            used_alternate_extension = True

    return enriched, used_file_extension, used_alternate_extension


def _enrich_assets(
    item_id: str,
    assets: object,
    index: dict[tuple[str, str], dict[str, object]],
    *,
    alternate_key: str | None,
) -> tuple[object, bool, bool]:
    if not isinstance(assets, dict):
        return assets, False, False

    enriched_assets: dict[str, object] = {}
    used_file_extension = False
    used_alternate_extension = False
    for asset_key, asset in assets.items():
        lock = index.get((item_id, asset_key))
        if isinstance(asset, Mapping) and lock:
            enriched, file_used, alternate_used = _enrich_asset(
                asset,
                lock,
                alternate_key=alternate_key,
            )
            enriched_assets[asset_key] = enriched
            used_file_extension = used_file_extension or file_used
            used_alternate_extension = used_alternate_extension or alternate_used
        else:
            enriched_assets[asset_key] = asset

    return enriched_assets, used_file_extension, used_alternate_extension


def enrich_items(
    items: pa.Table,
    asset_lock: pa.Table,
    *,
    alternate_key: str | None = None,
) -> pa.Table:
    index = _lock_index(asset_lock)
    rows = []
    output_schema = _enriched_items_schema(items.schema, alternate_key=alternate_key)

    for source_row in items.to_pylist():
        row = dict(source_row)
        item_id = row.get("id")
        if not isinstance(item_id, str):
            rows.append(row)
            continue

        if "assets_json" in row:
            asset_data = json.loads(str(row.get("assets_json") or "{}"))
            enriched_assets, used_file_extension, used_alternate_extension = _enrich_assets(
                item_id,
                asset_data,
                index,
                alternate_key=alternate_key,
            )
            row["assets_json"] = json.dumps(
                enriched_assets,
                sort_keys=True,
                separators=(",", ":"),
            )
        else:
            enriched_assets, used_file_extension, used_alternate_extension = _enrich_assets(
                item_id,
                row.get("assets"),
                index,
                alternate_key=alternate_key,
            )
            row["assets"] = enriched_assets

        if used_file_extension:
            row["stac_extensions"] = _add_extension(row.get("stac_extensions"), FILE_EXTENSION)
        if used_alternate_extension:
            row["stac_extensions"] = _add_extension(
                row.get("stac_extensions"),
                ALTERNATE_ASSETS_EXTENSION,
            )
        rows.append(row)

    return pa.Table.from_pylist(rows, schema=output_schema)
