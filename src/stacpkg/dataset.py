# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import mimetypes
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import pyarrow as pa

from stacpkg.arrow_io import read_parquet, read_stream_path, write_parquet
from stacpkg.assets import (
    DEFAULT_PROBE_METADATA,
    _asset_index,
    _item_index,
    _declared_checksum,
    _check_destinations,
    _check_source_checksums,
    asset_lock_table,
    derive_asset_lock,
    plan_copy_assets,
)
from stacpkg.geoparquet import _normalize_collections, items_table_to_geoparquet_table
from stacpkg.items import filter_items
from stacpkg.locators import href_from_location, location_from_href
from stacpkg.stac_json import read_stac_json
from stacpkg.sources import portable_item_links, resolve_item_assets

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
    return Path(unquote(parsed.path) if parsed.scheme else href)


def _package_relative_path(package_root: Path, path: Path) -> str:
    root = package_root.resolve()
    target = path.resolve()
    if root != target and root not in target.parents:
        raise ValueError(f"packaged asset escapes package directory: {target}")
    return target.relative_to(root).as_posix()


def _include_asset_bytes(items: pa.Table, assets: pa.Table, output_dir: Path) -> pa.Table:
    from stacpkg.object_store import copy_assets

    _check_source_checksums(items, assets, calculate=True)
    declared = _item_index(items)
    source_rows = assets.to_pylist()
    for row in source_rows:
        row["file_checksum"] = row.get("file_checksum") or _declared_checksum(
            declared[(row["item_id"], row["asset_key"])]
        )
    assets = asset_lock_table(source_rows)
    asset_root = output_dir / "assets"
    target_assets = plan_copy_assets(
        assets,
        target=str(asset_root.resolve()),
        layout="item-asset",
    )
    _check_destinations(target_assets)
    copied_assets = copy_assets(assets, target_assets, checksum="calculate-always", overwrite=False)

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
    collections: Mapping[str, Mapping[str, Any]] | Iterable[Mapping[str, Any]] | None = None,
    checksum: str = "metadata",
    source_base: str | Path | None = None,
    source_alternate: str | None = None,
) -> dict[str, Any]:
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise ValueError("build output directory must not exist")

    skip = skip or []
    include_paths = [Path(path) for path in includes or []]
    names = {ITEMS_PACKAGE_PATH, ASSET_LOCK_PACKAGE_PATH}
    if include_assets:
        names.add("assets")
    for path in include_paths:
        if not path.exists():
            raise ValueError(f"included path does not exist: {path}")
        if not path.name or path.name in names:
            raise ValueError(f"duplicate package path: {path.name}")
        if path.is_dir() and output_dir.resolve().is_relative_to(path.resolve()):
            raise ValueError("included directory must not contain the package directory")
        names.add(path.name)
    if "assets-lock" in skip:
        raise ValueError("assets.lock.parquet is mandatory for package builds")
    item_table = items if isinstance(items, pa.Table) else read_items_input(items)
    document_href = Path(items).resolve() if not isinstance(items, pa.Table) else None
    if item_ids or providers:
        item_table = filter_items(item_table, item_ids=item_ids, providers=providers)
    item_table = resolve_item_assets(
        item_table,
        source_base=source_base,
        include_metadata_assets=include_metadata_assets,
        document_href=document_href,
    )
    original_items = item_table
    if source_alternate:
        item_table = resolve_item_assets(
            item_table,
            source_alternate=source_alternate,
            include_metadata_assets=include_metadata_assets,
        )
    declared = _item_index(item_table)
    collections = _normalize_collections(collections)
    item_output = items_table_to_geoparquet_table(item_table, collections=collections)
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
            checksum=checksum,
        )
    locked = _asset_index(assets.to_pylist())
    if locked.keys() - declared.keys():
        raise ValueError("asset lock contains assets outside the Item selection")
    if {identity for identity in declared if identity[1] != "metadata"} - locked.keys():
        raise ValueError("asset lock is missing Item assets")
    for identity, row in locked.items():
        asset = declared[identity]
        if href_from_location(row) != href_from_location(location_from_href(asset["href"])):
            raise ValueError("asset lock location differs from Item metadata")
        endpoint = (asset.get("store_endpoint_url") or "").rstrip("/")
        if (row.get("store_endpoint_url") or "").rstrip("/") != endpoint:
            raise ValueError("asset lock endpoint differs from Item metadata")
        if (
            asset.get("file:size") is not None
            and row.get("size_bytes") is not None
            and int(asset["file:size"]) != row["size_bytes"]
        ):
            raise ValueError("source size mismatch")
    _check_source_checksums(item_table, assets, calculate=checksum.startswith("calculate-"))

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="xyz-", dir=output_dir.parent) as temp:
        stage = Path(temp) / "package"
        stage.mkdir()
        if include_assets or source_alternate:
            from stacpkg.enrich import enrich_items
            from stacpkg.projection import project_item_assets

            if include_assets:
                assets = _include_asset_bytes(item_table, assets, stage)
            projected = project_item_assets(original_items, assets, strategy="set-href")
            if include_assets:
                projected = enrich_items(projected, assets)
                projected = portable_item_links(projected)
            item_output = items_table_to_geoparquet_table(projected, collections=collections)
        write_parquet(item_output, stage / ITEMS_PACKAGE_PATH)
        write_parquet(assets, stage / ASSET_LOCK_PACKAGE_PATH)
        for path in include_paths:
            _copy_include(path, stage)
        if output_dir.exists():
            raise ValueError("build output directory must not exist")
        stage.rename(output_dir)

    return {
        "package": str(output_dir),
        "files": package_file_entries(output_dir),
    }
