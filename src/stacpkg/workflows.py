# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pyarrow as pa

from stacpkg.arrow_io import read_parquet, write_parquet
from stacpkg.assets import (
    _asset_index,
    _item_index,
    _declared_checksum,
    _check_destinations,
    asset_lock_table,
    derive_asset_lock,
    plan_copy_assets,
)
from stacpkg.checksums import file_checksum_algorithm
from stacpkg.dataset import (
    ASSET_LOCK_PACKAGE_PATH,
    ITEMS_PACKAGE_PATH,
    _sha256,
    read_items_input,
)
from stacpkg.enrich import enrich_items
from stacpkg.geoparquet import _normalize_collections, items_table_to_geoparquet_table
from stacpkg.locators import href_from_location, location_from_href
from stacpkg.object_store import (
    DEFAULT_COPY_CHUNK_SIZE_BYTES,
    DEFAULT_COPY_MAX_WORKERS,
    DEFAULT_COPY_MEMORY_LIMIT_BYTES,
    DEFAULT_COPY_PUT_MAX_CONCURRENCY,
    copy_assets,
    validate_assets,
)
from stacpkg.projection import project_item_assets
from stacpkg.sources import resolve_href, resolve_item_assets, select_item_assets
from stacpkg.schemas import ASSET_LOCK_FACT_COLUMNS


def relocate_package(
    items: pa.Table | str | Path,
    output_dir: str | Path,
    *,
    target: str,
    source_base: str | Path | None = None,
    source_alternate: str | None = None,
    target_endpoint_url: str | None = None,
    asset_keys: set[str] | None = None,
    include_metadata_assets: bool = False,
    collections=None,
    overwrite: bool = False,
    max_workers: int = DEFAULT_COPY_MAX_WORKERS,
    memory_limit_bytes: int = DEFAULT_COPY_MEMORY_LIMIT_BYTES,
    chunk_size_bytes: int = DEFAULT_COPY_CHUNK_SIZE_BYTES,
    put_max_concurrency: int = DEFAULT_COPY_PUT_MAX_CONCURRENCY,
) -> dict[str, object]:
    """Copy selected Item assets, check destination bytes, and write a package."""
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise ValueError("relocation output directory must not exist")
    document_href = None
    if not isinstance(items, pa.Table):
        document_href = Path(items).resolve()
        items = read_items_input(items)
    if asset_keys:
        items = select_item_assets(items, asset_keys)
    resolved = resolve_item_assets(items, source_base=source_base, document_href=document_href)
    declared = _item_index(resolved)
    collections = _normalize_collections(collections)
    items_table_to_geoparquet_table(resolved, collections=collections)
    source = derive_asset_lock(
        resolved,
        probe_metadata=False,
        source_alternate=source_alternate,
        asset_keys=asset_keys,
        include_metadata_assets=include_metadata_assets,
    )
    if not source.num_rows:
        raise ValueError("no assets selected for relocation")
    source_rows = source.to_pylist()
    for row in source_rows:
        row["file_checksum"] = _declared_checksum(declared[(row["item_id"], row["asset_key"])])
    source = asset_lock_table(source_rows)
    planned = plan_copy_assets(source, target=target, target_endpoint_url=target_endpoint_url)
    _check_destinations(planned)
    for row in planned.to_pylist():
        if row.get("store_type") == "file" and Path(row["key"]).resolve().is_relative_to(
            output_dir.resolve()
        ):
            raise ValueError(
                "relocation target must be outside the package directory; use build --include-assets for packaged files"
            )
    copied = copy_assets(
        source,
        planned,
        checksum="calculate-always",
        overwrite=overwrite,
        max_workers=max_workers,
        memory_limit_bytes=memory_limit_bytes,
        chunk_size_bytes=chunk_size_bytes,
        put_max_concurrency=put_max_concurrency,
    )
    projected = project_item_assets(resolved, copied, strategy="set-href")
    output_items = items_table_to_geoparquet_table(
        enrich_items(projected, copied),
        collections=collections,
    )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="xyz-", dir=output_dir.parent) as temp:
        stage = Path(temp) / "package"
        stage.mkdir()
        write_parquet(output_items, stage / ITEMS_PACKAGE_PATH)
        write_parquet(copied, stage / ASSET_LOCK_PACKAGE_PATH)
        stage.rename(output_dir)
    return {"package": str(output_dir), "assets": copied.num_rows, "checksum": "sha256"}


def validate_package(
    package_dir: str | Path,
    *,
    checksum: str = "calculate-if-needed",
    max_workers: int = DEFAULT_COPY_MAX_WORKERS,
) -> Iterator[dict[str, object]]:
    """Yield checks and a summary; replace the lock only after complete success."""
    package_dir = Path(package_dir).resolve()
    if max_workers < 1:
        raise ValueError("max_workers must be at least 1")
    items_path = package_dir / ITEMS_PACKAGE_PATH
    lock_path = package_dir / ASSET_LOCK_PACKAGE_PATH
    before_items = _sha256(items_path)
    before_lock = _sha256(lock_path) if lock_path.exists() else None

    def check_unchanged():
        if (
            _sha256(items_path) != before_items
            or (_sha256(lock_path) if lock_path.exists() else None) != before_lock
        ):
            raise ValueError("package changed during validation; checked facts were not saved")

    items = resolve_item_assets(read_parquet(items_path), source_base=items_path)
    declared = _item_index(items)
    required = {identity for identity in declared if identity[1] != "metadata"}
    locked = (
        read_parquet(lock_path)
        if lock_path.exists()
        else derive_asset_lock(items, probe_metadata=False)
    )
    original = _asset_index(locked.to_pylist())
    expected = {identity: dict(row) for identity, row in original.items()}
    if not expected.keys() <= declared.keys():
        raise ValueError("asset lock contains assets outside the Item selection")
    if required - expected.keys():
        raise ValueError("asset lock is missing Item assets")
    if not expected:
        raise ValueError("no assets selected for validation")
    declarations = {}
    for identity, row in expected.items():
        asset = declared[identity]
        href = href_from_location(row)
        if resolve_href(href, items_path) != asset["href"]:
            raise ValueError("asset lock location differs from Item metadata")
        if row.get("store_endpoint_url") != asset.get("store_endpoint_url"):
            raise ValueError("asset lock endpoint differs from Item metadata")
        declared_checksum = _declared_checksum(asset)
        if (
            declared_checksum
            and row.get("file_checksum")
            and declared_checksum != row["file_checksum"]
        ):
            if file_checksum_algorithm(declared_checksum) == file_checksum_algorithm(
                row["file_checksum"]
            ):
                raise ValueError("asset lock checksum differs from Item metadata")
            declarations[identity] = declared_checksum
        declared_size = asset.get("file:size")
        if (
            declared_size is not None
            and row.get("size_bytes") is not None
            and int(declared_size) != row["size_bytes"]
        ):
            raise ValueError("asset lock size differs from Item metadata")
        row["file_checksum"] = row.get("file_checksum") or declared_checksum
        if row.get("size_bytes") is None:
            row["size_bytes"] = declared_size

    operational = {
        identity: {**row, **location_from_href(resolve_href(href_from_location(row), items_path))}
        for identity, row in expected.items()
    }
    rows = list(operational.values())
    passed = failed = baseline = 0
    methods = set()
    checked = []
    for offset in range(0, len(rows), max_workers):
        batch = asset_lock_table(rows[offset : offset + max_workers])
        for result in validate_assets(
            batch, checksum=checksum, keep_going=True, max_workers=max_workers
        ):
            identity = (result["item_id"], result["asset_key"])
            if result["valid"] and identity in declarations:
                declaration_row = {**operational[identity], "file_checksum": declarations[identity]}
                declaration_check = validate_assets(
                    asset_lock_table([declaration_row]),
                    checksum=checksum,
                    keep_going=True,
                )[0]
                result["declaration_check"] = {
                    name: declaration_check.get(name)
                    for name in (
                        "valid",
                        "method",
                        "expected_checksum",
                        "observed_checksum",
                        "errors",
                    )
                }
                methods.add(declaration_check["method"])
                if not declaration_check["valid"]:
                    result["valid"] = False
                    result["errors"].extend(
                        "Item declaration: " + error for error in declaration_check["errors"]
                    )
            result["type"] = "asset"
            passed += int(result["valid"])
            failed += int(not result["valid"])
            baseline += int(result.get("comparison") == "baseline")
            methods.add(result["method"])
            saved = dict(original[identity])
            if result["valid"]:
                saved.update(
                    {name: result["observed"].get(name) for name in ASSET_LOCK_FACT_COLUMNS}
                )
            checked.append(saved)
            yield result
    check_unchanged()
    if not failed:
        descriptor, name = tempfile.mkstemp(prefix="xyz-", suffix=".parquet", dir=package_dir)
        os.close(descriptor)
        temporary = Path(name)
        try:
            write_parquet(asset_lock_table(checked), temporary)
            check_unchanged()
            temporary.replace(lock_path)
        finally:
            temporary.unlink(missing_ok=True)
    yield {
        "type": "summary",
        "valid": not failed,
        "checked": passed + failed,
        "passed": passed,
        "failed": failed,
        "baseline": baseline,
        "excluded": len(declared) - len(expected),
        "methods": sorted(methods),
        "checksum": checksum,
        "lock_written": not failed,
        "items_digest": "sha256:" + before_items,
    }
