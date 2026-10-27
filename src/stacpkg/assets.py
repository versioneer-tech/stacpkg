# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import BinaryIO
from collections.abc import Mapping
from urllib.parse import urlparse

import pyarrow as pa
import pyarrow.parquet as pq

from stacpkg.arrow_io import align_record_batch_to_schema
from stacpkg.items import filter_items
from stacpkg.checksums import (
    file_checksum_algorithm,
    normalize_checksum_strategy,
    normalize_file_checksum,
)
from stacpkg.sources import check_public_href, resolve_item_assets
from stacpkg.locators import (
    OBSTORE_STORE_TYPES,
    child_location,
    href_from_location,
    location_from_href,
    normalize_store_type,
)
from stacpkg.schemas import (
    ASSET_LOCK_COLUMNS,
    ASSET_LOCK_OPTIONAL_COLUMNS,
    ASSET_LOCK_SCHEMA_VERSION,
    SchemaKind,
    asset_lock_schema,
    with_schema_metadata,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_PROBE_METADATA = True
METADATA_ASSET_KEY = "metadata"


def _log_location(value: object) -> object:
    if not isinstance(value, str):
        return value
    parsed = urlparse(value)
    if parsed.query:
        return parsed._replace(query="<redacted>").geturl()
    return value


def asset_lock_table(rows: list[dict[str, object]]) -> pa.Table:
    rows = [_compact_asset_lock_row(row) for row in rows]
    table = pa.Table.from_pylist(rows, schema=asset_lock_schema())
    return table.replace_schema_metadata(
        with_schema_metadata(
            table.schema,
            SchemaKind.ASSET_LOCK,
            ASSET_LOCK_SCHEMA_VERSION,
        ).metadata
    )


def write_asset_lock_parquet_stream(source: BinaryIO, output_path: str | Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_schema = asset_lock_schema()
    with pa.ipc.open_stream(source) as reader:
        with pq.ParquetWriter(output_path, output_schema) as writer:
            for batch in reader:
                writer.write_batch(align_record_batch_to_schema(batch, output_schema))


def _compact_asset_lock_row(source: dict[str, object]) -> dict[str, object]:
    location = {
        "store_type": normalize_store_type(source.get("store_type")),
        "store_container": source.get("store_container"),
        "key": source.get("key"),
    }
    if not location["store_type"] and not location["key"]:
        location = location_from_href(source.get("href"))

    row = {
        "item_id": source.get("item_id"),
        "asset_key": source.get("asset_key"),
        "store_type": location.get("store_type"),
        "store_container": location.get("store_container"),
        "store_endpoint_url": _endpoint_url(source.get("store_endpoint_url")),
        "key": location.get("key"),
    }
    size = _size(source.get("size_bytes", source.get("size")))
    if size is not None:
        row["size_bytes"] = size

    for column in ASSET_LOCK_OPTIONAL_COLUMNS:
        if column == "size_bytes":
            continue
        value = source.get(column)
        if value is not None:
            if column == "file_checksum":
                value = normalize_file_checksum(value)
                if value is None:
                    raise ValueError("file_checksum must be a supported full-object Multihash")
            row[column] = value
    return row


def _item_assets(item: dict[str, object]) -> dict[str, dict[str, object]]:
    if "assets_json" in item:
        assets = json.loads(str(item.get("assets_json") or "{}"))
    else:
        assets = item.get("assets")
    if isinstance(assets, dict):
        return {
            key: asset
            for key, asset in assets.items()
            if isinstance(asset, dict) and asset.get("href")
        }
    return {}


def _asset_index(rows: list[dict]) -> dict[tuple, dict]:
    index = {}
    for row in rows:
        identity = (row.get("item_id"), row.get("asset_key"))
        if not all(identity) or identity in index:
            raise ValueError("asset identities must be present and unique")
        index[identity] = row
    return index


def _item_index(items: pa.Table) -> dict[tuple, dict]:
    rows, item_ids = [], set()
    for item in items.to_pylist():
        item_id = item.get("id")
        if not item_id or item_id in item_ids:
            raise ValueError("Item ids must be present and unique in a package")
        item_ids.add(item_id)
        for key, asset in _item_assets(item).items():
            rows.append({**asset, "item_id": item_id, "asset_key": key})
    return _asset_index(rows)


def _declared_checksum(asset: Mapping) -> str | None:
    value = asset.get("file:checksum")
    if value is None:
        return None
    checksum = normalize_file_checksum(value)
    if checksum is None:
        raise ValueError("STAC file:checksum must be a supported full-object Multihash")
    return checksum


def _check_destinations(assets: pa.Table) -> None:
    locations = [
        (href_from_location(row), row.get("store_endpoint_url")) for row in assets.to_pylist()
    ]
    if len(locations) != len(set(locations)):
        raise ValueError("asset names map to the same destination")


def _check_source_checksums(items: pa.Table, assets: pa.Table, *, calculate: bool) -> None:
    from stacpkg.object_store import validate_assets

    declared = _item_index(items)
    additional = []
    for row in assets.to_pylist():
        expected = _declared_checksum(declared[(row["item_id"], row["asset_key"])])
        observed = row.get("file_checksum")
        if not expected or not observed or expected == observed:
            continue
        if file_checksum_algorithm(expected) == file_checksum_algorithm(observed):
            raise ValueError("source checksum mismatch")
        if calculate:
            additional.append({**row, "file_checksum": expected})
    if additional:
        for result in validate_assets(asset_lock_table(additional), checksum="calculate-if-needed"):
            if not result["valid"]:
                raise ValueError("source checksum mismatch")


def _size(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _endpoint_url(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    endpoint = value.strip().rstrip("/")
    if not urlparse(endpoint).scheme:
        endpoint = f"https://{endpoint}"
    return check_public_href(endpoint)


def derive_asset_lock(
    items: pa.Table,
    *,
    probe_metadata: bool = DEFAULT_PROBE_METADATA,
    item_ids: set[str] | None = None,
    providers: set[str] | None = None,
    asset_keys: set[str] | None = None,
    include_metadata_assets: bool = False,
    keep_going: bool = False,
    max_workers: int | None = None,
    checksum: str = "metadata",
    source_base: str | Path | None = None,
    source_alternate: str | None = None,
) -> pa.Table:
    checksum = normalize_checksum_strategy(checksum)
    if item_ids or providers:
        items = filter_items(items, item_ids=item_ids, providers=providers)
    items = resolve_item_assets(
        items,
        source_base=source_base,
        source_alternate=source_alternate,
        asset_keys=asset_keys,
        include_metadata_assets=include_metadata_assets,
    )

    rows: list[dict[str, object]] = []
    for item in items.to_pylist():
        for asset_key, asset in _item_assets(item).items():
            if asset_keys and asset_key not in asset_keys:
                continue
            if asset_key == METADATA_ASSET_KEY and not include_metadata_assets and not asset_keys:
                continue
            href = asset.get("href")
            row: dict[str, object] = {
                "item_id": item["id"],
                "asset_key": asset_key,
                **location_from_href(href),
            }
            if asset.get("store_endpoint_url"):
                row["store_endpoint_url"] = asset.get("store_endpoint_url")
            size = _size(asset.get("file:size"))
            if size is not None:
                row["size_bytes"] = size
            rows.append(row)

    assets = asset_lock_table(rows)
    if not probe_metadata:
        return assets
    from stacpkg.object_store import stat_assets

    options = {"keep_going": keep_going}
    if max_workers is not None:
        options["max_workers"] = max_workers
    if checksum != "metadata":
        options["checksum"] = checksum
    observed = stat_assets(assets, **options)
    _check_source_checksums(items, observed, calculate=checksum.startswith("calculate-"))
    return observed


def map_asset_locations(
    asset_lock: pa.Table,
    *,
    target: str,
    source_prefix: str | None = None,
    layout: str = "item-asset",
    target_endpoint_url: str | None = None,
) -> pa.Table:
    check_public_href(target)
    target_location = location_from_href(target)
    if target_location.get("store_type") == "file":
        target_location["key"] = str(Path(str(target_location["key"])).resolve())
    target_endpoint_url = _endpoint_url(target_endpoint_url)
    if target_endpoint_url is not None:
        target_location["store_endpoint_url"] = target_endpoint_url
    return _map_asset_locations_to_location(
        asset_lock,
        target_location,
        source_prefix=source_prefix,
        layout=layout,
        log_target=target,
    )


def relocate_asset_locations(
    asset_lock: pa.Table,
    *,
    store_type: str,
    store_container: str | None = None,
    store_endpoint_url: str | None = None,
    key: str | None = None,
    source_prefix: str | None = None,
    layout: str = "item-asset",
) -> pa.Table:
    normalized_store_type = normalize_store_type(store_type)
    if normalized_store_type is None:
        expected = ", ".join(OBSTORE_STORE_TYPES)
        raise ValueError(f"unsupported store_type: {store_type}. Expected one of: {expected}")

    key = key or ""
    if normalized_store_type == "file" and not key:
        raise ValueError("key is required when store_type is file")
    if normalized_store_type in {"s3", "gs", "az", "http", "https"} and not store_container:
        raise ValueError(f"store_container is required when store_type is {normalized_store_type}")

    target_location = {
        "store_type": normalized_store_type,
        "store_container": store_container,
        "store_endpoint_url": _endpoint_url(store_endpoint_url),
        "key": key,
    }
    return _map_asset_locations_to_location(
        asset_lock,
        target_location,
        source_prefix=source_prefix,
        layout=layout,
        log_target=href_from_location(target_location),
    )


def _map_asset_locations_to_location(
    asset_lock: pa.Table,
    target_location: dict[str, object],
    *,
    source_prefix: str | None,
    layout: str,
    log_target: object,
) -> pa.Table:
    from stacpkg.object_store import target_path

    rows: list[dict[str, object]] = []
    mapped_count = 0
    for row in asset_lock.to_pylist():
        row = dict(row)
        if not _matches_href_prefix(href_from_location(row), source_prefix):
            rows.append(row)
            continue

        path = target_path(row, layout=layout)
        row.update(child_location(target_location, path))
        for field in ASSET_LOCK_COLUMNS:
            if field in {
                "item_id",
                "asset_key",
                "store_type",
                "store_container",
                "store_endpoint_url",
                "key",
                "size_bytes",
                "file_checksum",
            }:
                continue
            row.pop(field, None)
        rows.append(row)
        mapped_count += 1
    LOGGER.info(
        "map asset locations completed: input_rows=%s mapped_rows=%s from=%s target=%s layout=%s",
        asset_lock.num_rows,
        mapped_count,
        _log_location(source_prefix),
        _log_location(log_target),
        layout,
    )
    return asset_lock_table(rows)


def plan_copy_assets(
    asset_lock: pa.Table,
    *,
    target: str,
    source_prefix: str | None = None,
    layout: str = "item-asset",
    target_endpoint_url: str | None = None,
) -> pa.Table:
    return map_asset_locations(
        asset_lock,
        target=target,
        source_prefix=source_prefix,
        layout=layout,
        target_endpoint_url=target_endpoint_url,
    )


def _matches_href_prefix(href: object, prefix: str | None) -> bool:
    if prefix is None:
        return True
    if not isinstance(href, str) or not href:
        return False
    normalized = prefix.rstrip("/")
    return href == normalized or href.startswith(f"{normalized}/")
