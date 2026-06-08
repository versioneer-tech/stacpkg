# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import mimetypes
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pyarrow as pa

from stacpkg.arrow_io import read_parquet, read_stream_path, write_parquet
from stacpkg.assets import (
    DEFAULT_PROBE_METADATA,
    asset_lock_table,
    derive_asset_lock,
    plan_copy_assets,
)
from stacpkg.geoparquet import items_table_to_geoparquet_table
from stacpkg.items import filter_items
from stacpkg.locators import href_from_location
from stacpkg.stac_json import read_stac_json

ITEMS_PACKAGE_PATH = "items.parquet"
ASSET_LOCK_PACKAGE_PATH = "assets.lock.parquet"


def read_items_input(path: str | Path) -> pa.Table:
    path = Path(path)
    if path.suffix.lower() in {".json", ".geojson"}:
        return read_stac_json(path)
    if path.suffix.lower() == ".parquet":
        return read_parquet(path)
    raise ValueError(f"unsupported input format: {path}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _media_type(path: Path) -> str:
    if path.name.endswith(".parquet"):
        return "application/vnd.apache.parquet"
    if path.suffix == ".json":
        return "application/json"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _file_entry(path: Path, *, package_root: Path) -> dict[str, Any]:
    relative_path = path.relative_to(package_root).as_posix()
    return {
        "path": relative_path,
        "mediaType": _media_type(path),
        "size": path.stat().st_size,
        "digest": f"sha256:{_sha256(path)}",
    }


def package_file_entries(package_dir: str | Path) -> list[dict[str, Any]]:
    package_dir = Path(package_dir)
    return [
        _file_entry(path, package_root=package_dir)
        for path in sorted(child for child in package_dir.rglob("*") if child.is_file())
    ]


def _copy_include(include: Path, output_dir: Path) -> Path:
    destination = output_dir / include.name
    if include.is_dir():
        shutil.copytree(include, destination, dirs_exist_ok=True)
        return destination
    shutil.copy2(include, destination)
    return destination


def _file_href_path(href: object) -> Path:
    if not isinstance(href, str) or not href:
        raise ValueError("packaged asset href is empty")
    parsed = urlparse(href)
    if parsed.scheme and parsed.scheme != "file":
        raise ValueError(f"packaged asset href is not a file href: {href}")
    return Path(parsed.path if parsed.scheme else href)


def _package_relative_path(package_root: Path, path: Path) -> str:
    root = package_root.resolve()
    target = path.resolve()
    if root != target and root not in target.parents:
        raise ValueError(f"packaged asset escapes package directory: {target}")
    return target.relative_to(root).as_posix()


def _include_asset_bytes(assets: pa.Table, output_dir: Path) -> pa.Table:
    from stacpkg.object_store import copy_assets

    asset_root = output_dir / "assets"
    target_assets = plan_copy_assets(
        assets,
        target=str(asset_root.resolve()),
        layout="item-asset",
    )
    copied_assets = copy_assets(assets, target_assets)

    rows: list[dict[str, object]] = []
    for row in copied_assets.to_pylist():
        row = dict(row)
        packaged_path = _file_href_path(href_from_location(row))
        row["store_type"] = "file"
        row["store_container"] = None
        row["key"] = _package_relative_path(output_dir, packaged_path)
        if row.get("size_bytes") is None and packaged_path.exists():
            row["size_bytes"] = packaged_path.stat().st_size
        rows.append(row)
    return asset_lock_table(rows)


def build_package(
    items: str | Path | pa.Table,
    output_dir: str | Path,
    *,
    asset_lock: str | Path | pa.Table | None = None,
    includes: list[str | Path] | None = None,
    include_assets: bool = False,
    skip: list[str] | None = None,
    probe_metadata: bool = DEFAULT_PROBE_METADATA,
    include_metadata_assets: bool = False,
    item_ids: set[str] | None = None,
    providers: set[str] | None = None,
) -> dict[str, Any]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    skip = skip or []
    includes = includes or []
    if "assets-lock" in skip:
        raise ValueError("assets.lock.parquet is mandatory for package builds")
    item_table = items if isinstance(items, pa.Table) else read_items_input(items)
    if item_ids or providers:
        item_table = filter_items(item_table, item_ids=item_ids, providers=providers)
    item_output = items_table_to_geoparquet_table(item_table)

    write_parquet(item_output, output_dir / ITEMS_PACKAGE_PATH)

    if asset_lock is not None:
        asset_table = (
            asset_lock if isinstance(asset_lock, pa.Table) else read_stream_path(asset_lock)
        )
        asset_rows = asset_table.to_pylist()
        if item_ids or providers:
            selected_item_ids = {str(row["id"]) for row in item_table.to_pylist()}
            asset_rows = [row for row in asset_rows if row.get("item_id") in selected_item_ids]
        assets = asset_lock_table(asset_rows)
    else:
        assets = derive_asset_lock(
            item_table,
            probe_metadata=probe_metadata,
            include_metadata_assets=include_metadata_assets,
        )
    if include_assets:
        assets = _include_asset_bytes(assets, output_dir)
    write_parquet(assets, output_dir / ASSET_LOCK_PACKAGE_PATH)

    for include in includes:
        _copy_include(Path(include), output_dir)

    return {
        "package": str(output_dir),
        "files": package_file_entries(output_dir),
    }
