# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from urllib.parse import urlparse

import pyarrow as pa

from stacpkg.locators import href_from_location


def _lock_index(asset_lock: pa.Table) -> dict[tuple[str, str], dict[str, object]]:
    index: dict[tuple[str, str], dict[str, object]] = {}
    for row in asset_lock.to_pylist():
        item_id = row.get("item_id")
        asset_key = row.get("asset_key")
        href = href_from_location(row)
        if isinstance(item_id, str) and isinstance(asset_key, str) and isinstance(href, str):
            index[(item_id, asset_key)] = row
    return index


PROJECT_ASSET_STRATEGIES = (
    "set-href",
    "set-alternate",
    "set-href-from-alternate",
    "unset-alternate",
)
PROMOTE_ALTERNATE_MODES = ("replace", "switch")
_STRATEGIES_REQUIRING_ASSET_LOCK = {"set-href", "set-alternate"}
_STRATEGIES_REQUIRING_ALTERNATE_KEY = {
    "set-alternate",
    "set-href-from-alternate",
    "unset-alternate",
}
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


def _remove_struct_field(struct_type: pa.StructType, field_name: str) -> pa.StructType:
    return pa.struct([field for field in struct_type if field.name != field_name])


def _remove_alternate_field(
    asset_type: pa.StructType,
    *,
    alternate_key: str | None,
    remove_all: bool,
) -> pa.StructType:
    fields: list[pa.Field] = []
    for field in asset_type:
        if field.name != "alternate" or not pa.types.is_struct(field.type):
            fields.append(field)
            continue
        if remove_all:
            continue
        if alternate_key is None:
            fields.append(field)
            continue
        alternate_type = _remove_struct_field(field.type, alternate_key)
        if alternate_type.num_fields:
            fields.append(_field_with_type(field, alternate_type))
    return pa.struct(fields)


def _alternate_entry_field(alternate_key: str) -> pa.Field:
    return pa.field(
        alternate_key,
        pa.struct(
            [
                pa.field("href", pa.string()),
                pa.field(STORE_ENDPOINT_URL_FIELD, pa.string()),
                pa.field("alternate:name", pa.string()),
            ]
        ),
    )


def _alternate_field(*alternate_keys: str) -> pa.Field:
    return pa.field(
        "alternate",
        pa.struct([_alternate_entry_field(key) for key in alternate_keys if key]),
    )


def _asset_projection_fields(
    *,
    strategy: str,
    alternate_key: str | None,
) -> list[pa.Field]:
    fields: list[pa.Field] = []
    alternate_keys: list[str] = []

    if strategy == "set-href":
        fields.append(pa.field(STORE_ENDPOINT_URL_FIELD, pa.string()))
        alternate_keys.append("original")
    elif strategy == "set-alternate" and alternate_key:
        alternate_keys.append(alternate_key)
    elif strategy == "set-href-from-alternate":
        fields.extend(
            [
                pa.field(STORE_ENDPOINT_URL_FIELD, pa.string()),
                pa.field("alternate:name", pa.string()),
            ]
        )
        if alternate_key:
            alternate_keys.append(alternate_key)

    if alternate_keys:
        fields.append(_alternate_field(*alternate_keys))
    return fields


def _projected_items_schema(
    schema: pa.Schema,
    *,
    strategy: str,
    alternate_key: str | None,
    drop_alternates: bool,
    promotion_mode: str,
) -> pa.Schema:
    if "assets_json" in schema.names:
        return schema
    assets_index = schema.get_field_index("assets")
    if assets_index == -1:
        return schema
    assets_field = schema.field(assets_index)
    if not pa.types.is_struct(assets_field.type):
        return schema

    remove_all_alternates = (
        strategy == "set-href-from-alternate" and drop_alternates and promotion_mode == "replace"
    )
    remove_alternate_key = strategy == "unset-alternate"
    extra_fields = _asset_projection_fields(strategy=strategy, alternate_key=alternate_key)
    if remove_all_alternates:
        extra_fields = [field for field in extra_fields if field.name != "alternate"]
    if not extra_fields and not remove_all_alternates and not remove_alternate_key:
        return schema

    asset_fields: list[pa.Field] = []
    for asset_field in assets_field.type:
        if pa.types.is_struct(asset_field.type):
            asset_type = asset_field.type
            if remove_all_alternates or remove_alternate_key:
                asset_type = _remove_alternate_field(
                    asset_type,
                    alternate_key=alternate_key,
                    remove_all=remove_all_alternates,
                )
            if extra_fields:
                asset_type = _merge_struct_type(asset_type, extra_fields)
            asset_fields.append(
                _field_with_type(
                    asset_field,
                    asset_type,
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


def _s3_parts(href: object) -> tuple[str, str] | None:
    if not isinstance(href, str) or not href:
        return None
    parsed = urlparse(href)
    if parsed.scheme not in {"s3", "s3a"} or not parsed.netloc:
        return None
    return parsed.netloc, parsed.path.lstrip("/")


def _matching_http_endpoint_url(source_href: object, s3_href: object) -> str | None:
    s3_parts = _s3_parts(s3_href)
    if s3_parts is None or not isinstance(source_href, str):
        return None

    bucket, key = s3_parts
    parsed = urlparse(source_href)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    source_key = parsed.path.lstrip("/")
    host = parsed.netloc
    if host.startswith(f"{bucket}.") and source_key == key:
        return f"{parsed.scheme}://{host[len(bucket) + 1 :]}"

    bucket_prefix = f"{bucket}/"
    if source_key == key:
        return None
    if source_key.startswith(bucket_prefix) and source_key[len(bucket_prefix) :] == key:
        return f"{parsed.scheme}://{host}"
    return None


def _asset_reference(
    href: str,
    endpoint_url: object = None,
    *,
    alternate_name: object = None,
) -> dict[str, object]:
    reference: dict[str, object] = {"href": href}
    endpoint = _endpoint_url(endpoint_url)
    if endpoint:
        reference[STORE_ENDPOINT_URL_FIELD] = endpoint
    if isinstance(alternate_name, str) and alternate_name:
        reference["alternate:name"] = alternate_name
    return reference


def _set_primary_location(
    asset: dict[str, object],
    href: str,
    endpoint_url: object = None,
) -> None:
    asset["href"] = href
    endpoint = _endpoint_url(endpoint_url)
    if endpoint:
        asset[STORE_ENDPOINT_URL_FIELD] = endpoint
    else:
        asset.pop(STORE_ENDPOINT_URL_FIELD, None)


def _promote_alternate_asset_href(
    projected: dict[str, object],
    *,
    alternate_key: str,
    mode: str,
    drop_alternates: bool,
    switched_alternate_name: str,
) -> dict[str, object]:
    alternate = projected.get("alternate")
    if not isinstance(alternate, dict):
        return projected
    alternate_asset = alternate.get(alternate_key)
    if not isinstance(alternate_asset, Mapping):
        return projected
    alternate_href = alternate_asset.get("href")
    if not isinstance(alternate_href, str) or not alternate_href:
        return projected

    original_href = projected.get("href")
    original_alternate_name = projected.get("alternate:name")
    alternate_endpoint_url = _endpoint_url(
        alternate_asset.get(STORE_ENDPOINT_URL_FIELD)
    ) or _matching_http_endpoint_url(original_href, alternate_href)
    original_endpoint_url = _endpoint_url(projected.get(STORE_ENDPOINT_URL_FIELD))
    _set_primary_location(projected, alternate_href, alternate_endpoint_url)
    alternate_name = alternate_asset.get("alternate:name")
    projected["alternate:name"] = (
        alternate_name if isinstance(alternate_name, str) else alternate_key
    )

    if mode == "switch":
        if isinstance(original_href, str) and original_href:
            switched_alternate = _asset_reference(original_href, original_endpoint_url)
            if isinstance(original_alternate_name, str):
                switched_alternate["alternate:name"] = original_alternate_name
            elif switched_alternate_name:
                switched_alternate["alternate:name"] = switched_alternate_name
            alternate[alternate_key] = switched_alternate
        else:
            alternate.pop(alternate_key, None)
            if not alternate:
                projected.pop("alternate", None)
        return projected

    if drop_alternates:
        projected.pop("alternate", None)
    return projected


def _project_asset(
    asset: Mapping[str, object],
    *,
    strategy: str,
    target_href: str | None = None,
    target_endpoint_url: str | None = None,
    alternate_key: str | None = None,
    alternate_name: str | None = None,
    drop_alternates: bool = False,
    promotion_mode: str = "replace",
    switched_alternate_name: str = "original",
) -> dict[str, object]:
    projected = copy.deepcopy(dict(asset))

    if strategy == "set-href":
        if target_href is None:
            return projected
        original_href = projected.get("href")
        if isinstance(original_href, str) and original_href != target_href:
            alternate = projected.setdefault("alternate", {})
            if isinstance(alternate, dict):
                alternate.setdefault(
                    "original",
                    _asset_reference(
                        original_href,
                        projected.get(STORE_ENDPOINT_URL_FIELD),
                    ),
                )
        _set_primary_location(projected, target_href, target_endpoint_url)
        return projected

    if strategy == "set-href-from-alternate":
        if alternate_key is None:
            return projected
        return _promote_alternate_asset_href(
            projected,
            alternate_key=alternate_key,
            mode=promotion_mode,
            drop_alternates=drop_alternates,
            switched_alternate_name=switched_alternate_name,
        )

    if strategy == "set-alternate":
        if target_href is None or alternate_key is None:
            return projected
        alternate = projected.setdefault("alternate", {})
        if isinstance(alternate, dict):
            alternate[alternate_key] = _asset_reference(
                target_href,
                target_endpoint_url,
                alternate_name=alternate_name,
            )
        return projected

    if strategy == "unset-alternate":
        if alternate_key is None:
            return projected
        alternate = projected.get("alternate")
        if isinstance(alternate, dict):
            alternate.pop(alternate_key, None)
            if not alternate:
                projected.pop("alternate", None)
        return projected

    raise ValueError(
        f"unsupported asset projection strategy: {strategy}. "
        f"Expected one of: {', '.join(PROJECT_ASSET_STRATEGIES)}"
    )


def _project_assets(
    item_id: str,
    assets: object,
    index: dict[tuple[str, str], dict[str, object]] | None,
    *,
    strategy: str,
    alternate_key: str | None,
    alternate_name: str | None,
    drop_alternates: bool,
    promotion_mode: str,
    switched_alternate_name: str,
) -> object:
    if not isinstance(assets, dict):
        return assets

    projected: dict[str, object] = {}
    for asset_key, asset in assets.items():
        lock = index.get((item_id, asset_key)) if index is not None else None
        target_href = href_from_location(dict(lock)) if isinstance(lock, Mapping) else None
        target_endpoint_url = (
            lock.get(STORE_ENDPOINT_URL_FIELD) if isinstance(lock, Mapping) else None
        )
        if isinstance(asset, Mapping) and (
            strategy in {"set-href-from-alternate", "unset-alternate"}
            or isinstance(target_href, str)
        ):
            projected[asset_key] = _project_asset(
                asset,
                strategy=strategy,
                target_href=target_href if isinstance(target_href, str) else None,
                target_endpoint_url=_endpoint_url(target_endpoint_url),
                alternate_key=alternate_key,
                alternate_name=alternate_name,
                drop_alternates=drop_alternates,
                promotion_mode=promotion_mode,
                switched_alternate_name=switched_alternate_name,
            )
        else:
            projected[asset_key] = asset
    return projected


def project_item_assets(
    items: pa.Table,
    asset_lock: pa.Table | None = None,
    *,
    strategy: str,
    alternate_key: str | None = None,
    alternate_name: str | None = None,
    drop_alternates: bool = False,
    promotion_mode: str = "replace",
    switched_alternate_name: str = "original",
) -> pa.Table:
    if strategy not in PROJECT_ASSET_STRATEGIES:
        raise ValueError(
            f"unsupported asset projection strategy: {strategy}. "
            f"Expected one of: {', '.join(PROJECT_ASSET_STRATEGIES)}"
        )
    if strategy in _STRATEGIES_REQUIRING_ASSET_LOCK and asset_lock is None:
        raise ValueError(f"asset lock is required for strategy: {strategy}")
    if strategy not in _STRATEGIES_REQUIRING_ASSET_LOCK and asset_lock is not None:
        raise ValueError(f"asset lock is not used for strategy: {strategy}")
    if strategy in _STRATEGIES_REQUIRING_ALTERNATE_KEY and not alternate_key:
        raise ValueError(f"alternate key is required for strategy: {strategy}")
    if strategy not in _STRATEGIES_REQUIRING_ALTERNATE_KEY and alternate_key:
        raise ValueError(f"alternate key is not used for strategy: {strategy}")
    if drop_alternates and strategy != "set-href-from-alternate":
        raise ValueError("drop_alternates is only used for strategy: set-href-from-alternate")
    if promotion_mode not in PROMOTE_ALTERNATE_MODES:
        raise ValueError(
            f"unsupported alternate promotion mode: {promotion_mode}. "
            f"Expected one of: {', '.join(PROMOTE_ALTERNATE_MODES)}"
        )
    if promotion_mode != "replace" and strategy != "set-href-from-alternate":
        raise ValueError("promotion_mode is only used for strategy: set-href-from-alternate")
    if drop_alternates and promotion_mode != "replace":
        raise ValueError("drop_alternates is only used with replace alternate promotion")

    index = _lock_index(asset_lock) if asset_lock is not None else None
    rows = []
    output_schema = _projected_items_schema(
        items.schema,
        strategy=strategy,
        alternate_key=alternate_key,
        drop_alternates=drop_alternates,
        promotion_mode=promotion_mode,
    )

    for row in items.to_pylist():
        row = dict(row)
        item_id = row.get("id")
        if not isinstance(item_id, str):
            rows.append(row)
            continue

        if "assets_json" in row:
            asset_data = json.loads(str(row.get("assets_json") or "{}"))
            row["assets_json"] = json.dumps(
                _project_assets(
                    item_id,
                    asset_data,
                    index,
                    strategy=strategy,
                    alternate_key=alternate_key,
                    alternate_name=alternate_name,
                    drop_alternates=drop_alternates,
                    promotion_mode=promotion_mode,
                    switched_alternate_name=switched_alternate_name,
                ),
                sort_keys=True,
                separators=(",", ":"),
            )
        else:
            row["assets"] = _project_assets(
                item_id,
                row.get("assets"),
                index,
                strategy=strategy,
                alternate_key=alternate_key,
                alternate_name=alternate_name,
                drop_alternates=drop_alternates,
                promotion_mode=promotion_mode,
                switched_alternate_name=switched_alternate_name,
            )
        rows.append(row)

    return pa.Table.from_pylist(rows, schema=output_schema)


def promote_alternate_asset_hrefs(
    items: pa.Table,
    *,
    alternate_key: str,
    mode: str = "replace",
    drop_alternates: bool = False,
    switched_alternate_name: str = "original",
) -> pa.Table:
    return project_item_assets(
        items,
        strategy="set-href-from-alternate",
        alternate_key=alternate_key,
        drop_alternates=drop_alternates,
        promotion_mode=mode,
        switched_alternate_name=switched_alternate_name,
    )


def remove_alternate_asset_hrefs(
    items: pa.Table,
    *,
    alternate_key: str,
) -> pa.Table:
    return project_item_assets(
        items,
        strategy="unset-alternate",
        alternate_key=alternate_key,
    )


def add_alternate_asset_hrefs(
    items: pa.Table,
    asset_lock: pa.Table,
    *,
    alternate_key: str,
    alternate_name: str | None = None,
) -> pa.Table:
    return project_item_assets(
        items,
        asset_lock,
        strategy="set-alternate",
        alternate_key=alternate_key,
        alternate_name=alternate_name,
    )
