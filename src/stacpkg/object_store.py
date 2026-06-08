# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import logging
import os
import posixpath
import re
import threading
from collections.abc import Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, TypeVar
from urllib.parse import urlparse

import obstore as obs
import pyarrow as pa
from obstore.store import HTTPStore, from_url

from stacpkg.assets import asset_lock_table
from stacpkg.locators import (
    href_from_location,
    normalize_store_type,
)

LOGGER = logging.getLogger(__name__)
PATH_SAFE_RE = re.compile(r"[^A-Za-z0-9._=-]+")
DEFAULT_COPY_MAX_WORKERS = 4
DEFAULT_COPY_MEMORY_LIMIT_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_COPY_CHUNK_SIZE_BYTES = 8 * 1024 * 1024
DEFAULT_COPY_PUT_MAX_CONCURRENCY = 1
_T = TypeVar("_T")


def _run_async(coro: Coroutine[Any, Any, _T]) -> _T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        running_loop = False
    else:
        running_loop = True

    if not running_loop:
        return asyncio.run(coro)

    result: _T | None = None
    error: BaseException | None = None

    def run_in_thread() -> None:
        nonlocal error, result
        try:
            result = asyncio.run(coro)
        except BaseException as exc:  # pragma: no cover - preserves original exception type
            error = exc

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()
    thread.join()
    if error is not None:
        raise error
    return result  # type: ignore[return-value]


@dataclass(frozen=True)
class CopyRuntime:
    max_workers: int = DEFAULT_COPY_MAX_WORKERS
    memory_limit_bytes: int = DEFAULT_COPY_MEMORY_LIMIT_BYTES
    chunk_size_bytes: int = DEFAULT_COPY_CHUNK_SIZE_BYTES
    put_max_concurrency: int = DEFAULT_COPY_PUT_MAX_CONCURRENCY

    def __post_init__(self) -> None:
        if self.max_workers < 1:
            raise ValueError("copy max workers must be at least 1")
        if self.memory_limit_bytes < 1:
            raise ValueError("copy memory limit must be at least 1 byte")
        if self.chunk_size_bytes < 1:
            raise ValueError("copy chunk size must be at least 1 byte")
        if self.put_max_concurrency < 1:
            raise ValueError("copy put max concurrency must be at least 1")
        if self.per_copy_memory_bytes > self.memory_limit_bytes:
            raise ValueError(
                "copy memory limit is smaller than one streaming copy budget "
                f"({self.memory_limit_bytes} < {self.per_copy_memory_bytes})"
            )

    @property
    def per_copy_memory_bytes(self) -> int:
        return self.chunk_size_bytes * (self.put_max_concurrency + 1)

    @property
    def effective_workers(self) -> int:
        memory_limited_workers = max(1, self.memory_limit_bytes // self.per_copy_memory_bytes)
        return min(self.max_workers, memory_limited_workers)


class CopyMemoryBudget:
    def __init__(self, *, limit_bytes: int, reservation_bytes: int) -> None:
        self.limit_bytes = limit_bytes
        self.reservation_bytes = reservation_bytes
        self.current_reserved_bytes = 0
        self.peak_reserved_bytes = 0
        self._condition = asyncio.Condition()

    @asynccontextmanager
    async def reserve(self):
        async with self._condition:
            while self.current_reserved_bytes + self.reservation_bytes > self.limit_bytes:
                await self._condition.wait()
            self.current_reserved_bytes += self.reservation_bytes
            self.peak_reserved_bytes = max(
                self.peak_reserved_bytes,
                self.current_reserved_bytes,
            )
        try:
            yield
        finally:
            async with self._condition:
                self.current_reserved_bytes -= self.reservation_bytes
                self._condition.notify_all()


def _store_url_and_path(href: str) -> tuple[str, str]:
    parsed = urlparse(href)
    scheme = parsed.scheme

    if not scheme:
        return "file:///", href
    if scheme == "file":
        return "file:///", parsed.path
    if scheme in {"s3", "s3a", "gs", "gcs", "az", "abfs", "abfss"}:
        store_scheme = "gs" if scheme == "gcs" else scheme
        return f"{store_scheme}://{parsed.netloc}", parsed.path.lstrip("/")
    if scheme in {"http", "https"}:
        if parsed.query:
            return href, ""
        return f"{scheme}://{parsed.netloc}", parsed.path.lstrip("/")

    raise ValueError(f"unsupported object-store href scheme: {scheme}")


def _log_location(value: object) -> object:
    if not isinstance(value, str):
        return value
    parsed = urlparse(value)
    if parsed.query:
        return parsed._replace(query="<redacted>").geturl()
    return value


_S3_BUCKET_CONFIG_ENV_FIELDS = {
    "access_key_id": "ACCESS_KEY_ID",
    "secret_access_key": "SECRET_ACCESS_KEY",
    "session_token": "SESSION_TOKEN",
    "region": "REGION",
    "default_region": "DEFAULT_REGION",
}


def _bucket_config_env_name(bucket: str, field: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "_", bucket).strip("_").upper()
    return f"STACPKG_S3_{field}_{token}"


def _bucket_endpoint_env_name(bucket: str) -> str:
    return _bucket_config_env_name(bucket, "ENDPOINT")


def _configured_s3_endpoint(bucket: str | None) -> str | None:
    if bucket:
        bucket_endpoint = os.environ.get(_bucket_endpoint_env_name(bucket))
        if bucket_endpoint:
            return bucket_endpoint

        endpoints_json = os.environ.get("STACPKG_S3_ENDPOINTS_JSON")
        if endpoints_json:
            try:
                endpoints = json.loads(endpoints_json)
            except json.JSONDecodeError as error:
                raise ValueError("STACPKG_S3_ENDPOINTS_JSON must be a JSON object") from error
            if not isinstance(endpoints, dict):
                raise ValueError("STACPKG_S3_ENDPOINTS_JSON must be a JSON object")

            endpoint = endpoints.get(bucket)
            if endpoint is not None:
                if not isinstance(endpoint, str) or not endpoint:
                    raise ValueError(
                        f"S3 endpoint for bucket {bucket!r} must be a non-empty string"
                    )
                return endpoint

    return os.environ.get("AWS_ENDPOINT_URL") or os.environ.get("AWS_ENDPOINT")


def _configured_s3_credentials(bucket: str | None) -> dict[str, str]:
    if not bucket:
        return {}

    config = {
        config_key: value
        for config_key, env_field in _S3_BUCKET_CONFIG_ENV_FIELDS.items()
        if (value := os.environ.get(_bucket_config_env_name(bucket, env_field)))
    }
    if ("access_key_id" in config) != ("secret_access_key" in config):
        raise ValueError(
            f"bucket-scoped S3 credentials for {bucket!r} require both "
            f"{_bucket_config_env_name(bucket, 'ACCESS_KEY_ID')} and "
            f"{_bucket_config_env_name(bucket, 'SECRET_ACCESS_KEY')}"
        )
    return config


def _configured_s3_config(bucket: str | None) -> dict[str, str] | None:
    config = _configured_s3_credentials(bucket)
    endpoint = _configured_s3_endpoint(bucket)
    if endpoint:
        config = {**config, "endpoint": endpoint}
    return config or None


def _endpoint_url(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    endpoint = value.strip().rstrip("/")
    if not urlparse(endpoint).scheme:
        endpoint = f"https://{endpoint}"
    return endpoint


def _row_endpoint_url(row: dict[str, object] | None) -> str | None:
    return _endpoint_url(row.get("store_endpoint_url")) if row else None


def _store_from_url(url: str, *, endpoint_url: str | None = None):
    parsed = urlparse(url)
    if parsed.scheme == "file":
        return from_url(url, mkdir=True)

    config = None
    client_options = None
    endpoint = None
    if parsed.scheme in {"s3", "s3a"}:
        config = _configured_s3_config(parsed.netloc)
        endpoint = _endpoint_url(endpoint_url)
        if endpoint:
            config = {**(config or {}), "endpoint": endpoint}
        elif config:
            endpoint = _endpoint_url(config.get("endpoint"))
            if endpoint:
                config = {**config, "endpoint": endpoint}
    allow_http = os.environ.get("STACPKG_OBSTORE_ALLOW_HTTP")
    if endpoint:
        LOGGER.debug("using configured S3 endpoint: bucket=%s endpoint=%s", parsed.netloc, endpoint)
    if (endpoint and endpoint.startswith("http://")) or (
        allow_http and allow_http.lower() in {"1", "true", "yes", "on"}
    ):
        client_options = {"allow_http": True}

    if parsed.scheme in {"http", "https"}:
        return HTTPStore.from_url(url, client_options=client_options)

    return from_url(url, config=config, client_options=client_options)


def _suffix(href: str | None) -> str:
    if not href:
        return ""
    path = urlparse(href).path
    return PurePosixPath(path).suffix


def _key_from_row(row: dict[str, object]) -> str | None:
    key = row.get("key")
    if isinstance(key, str) and key:
        return key.lstrip("/")
    href = href_from_location(row)
    if not href:
        return None
    parsed = urlparse(href)
    key = parsed.path.lstrip("/")
    return key or None


def _component(value: object, default: str) -> str:
    text = str(value or default).strip()
    text = PATH_SAFE_RE.sub("_", text).strip("._/")
    return text or default


def target_path(row: dict[str, object], *, layout: str) -> str:
    if layout == "source-key":
        key = _key_from_row(row)
        if key:
            return str(key).lstrip("/")

    item_id = _component(row.get("item_id"), "item")
    asset_key = _component(row.get("asset_key"), "asset")
    href = href_from_location(row)
    suffix = _suffix(href)
    if suffix and not asset_key.endswith(suffix):
        asset_key = f"{asset_key}{suffix}"
    return posixpath.join(item_id, asset_key)


def _meta_fields(meta: dict[str, object] | None) -> dict[str, object]:
    meta = meta or {}
    fields = {
        "size_bytes": _meta_lookup(meta, "size", "content_length", "content-length"),
        "etag": _meta_lookup(meta, "e_tag", "etag", "ETag"),
        "last_modified": _meta_lookup(meta, "last_modified", "last-modified", "LastModified"),
    }
    return {key: _stringify_meta(value) for key, value in fields.items() if value is not None}


def _stringify_meta(value: object) -> object:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _missing_required_fields(row: dict[str, object]) -> list[str]:
    missing = [field for field in ("item_id", "asset_key") if not row.get(field)]
    store_type = normalize_store_type(row.get("store_type"))
    if store_type is None:
        missing.append("store_type")
        return missing

    key = row.get("key")
    container = row.get("store_container")
    if store_type == "file":
        if not isinstance(key, str) or not key:
            missing.append("key")
    elif store_type in {"s3", "gs", "az"}:
        if not isinstance(container, str) or not container:
            missing.append("store_container")
        if not isinstance(key, str) or not key:
            missing.append("key")
    elif store_type in {"http", "https"}:
        if not isinstance(container, str) or not container:
            missing.append("store_container")
        if not isinstance(key, str):
            missing.append("key")
    return missing


def _meta_token(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _meta_lookup(meta: dict[str, object], *names: str) -> object | None:
    for name in names:
        if name in meta:
            return meta[name]

    index = {_meta_token(key): value for key, value in meta.items()}
    for name in names:
        token = _meta_token(name)
        if token in index:
            return index[token]
    return None


def _selected(
    row: dict[str, object],
    *,
    item_ids: set[str] | None,
    asset_keys: set[str] | None,
) -> bool:
    if item_ids and row.get("item_id") not in item_ids:
        return False
    if asset_keys and row.get("asset_key") not in asset_keys:
        return False
    return True


async def _head_href(href: str, *, endpoint_url: str | None = None) -> dict[str, object]:
    store_url, path = _store_url_and_path(href)
    store = _store_from_url(store_url, endpoint_url=endpoint_url)
    return await obs.head_async(store, path)


async def _head_asset(href: str, endpoint_url: str | None) -> dict[str, object]:
    if endpoint_url:
        return await _head_href(href, endpoint_url=endpoint_url)
    return await _head_href(href)


async def _stat_row(
    source_row: dict[str, object],
    *,
    keep_going: bool,
) -> dict[str, object]:
    row = dict(source_row)
    href = href_from_location(row)
    if not isinstance(href, str) or not href:
        if not keep_going:
            raise ValueError("asset location is empty")
        LOGGER.debug("stat row skipped: asset location is empty")
        return row
    endpoint_url = _row_endpoint_url(row)

    try:
        meta = await _head_asset(href, endpoint_url)
        row.update(_meta_fields(meta))
        return row
    except Exception:
        if not keep_going:
            raise
        LOGGER.debug(
            "stat row failed but keep-going is enabled: href=%s",
            _log_location(href),
            exc_info=True,
        )
        return row


def stat_assets(
    assets: pa.Table,
    *,
    item_ids: set[str] | None = None,
    asset_keys: set[str] | None = None,
    keep_going: bool = False,
    max_workers: int = DEFAULT_COPY_MAX_WORKERS,
) -> pa.Table:
    return _run_async(
        _stat_assets_async(
            assets,
            item_ids=item_ids,
            asset_keys=asset_keys,
            keep_going=keep_going,
            max_workers=max_workers,
        )
    )


async def _stat_assets_async(
    assets: pa.Table,
    *,
    item_ids: set[str] | None,
    asset_keys: set[str] | None,
    keep_going: bool,
    max_workers: int,
) -> pa.Table:
    semaphore = asyncio.Semaphore(max(1, max_workers))

    async def guarded_stat(row: dict[str, object]) -> dict[str, object]:
        if not _selected(row, item_ids=item_ids, asset_keys=asset_keys):
            return dict(row)
        async with semaphore:
            return await _stat_row(
                row,
                keep_going=keep_going,
            )

    rows = await asyncio.gather(*(guarded_stat(row) for row in assets.to_pylist()))
    return asset_lock_table(rows)


async def _validate_row(
    source_row: dict[str, object],
    *,
    keep_going: bool,
) -> dict[str, object]:
    row = dict(source_row)
    missing = _missing_required_fields(row)
    if missing:
        return _validation_result(row, valid=False, errors=[f"missing fields: {','.join(missing)}"])

    href = href_from_location(row)
    if not isinstance(href, str) or not href:
        return _validation_result(row, valid=False, errors=["asset location is empty"])
    endpoint_url = _row_endpoint_url(row)

    try:
        meta = await _head_asset(href, endpoint_url)
        actual = _meta_fields(meta)
        errors: list[str] = []
        expected_size = row.get("size_bytes")
        actual_size = actual.get("size_bytes")
        if expected_size is not None and actual_size is not None and expected_size != actual_size:
            errors.append(f"size mismatch: expected {expected_size}, actual {actual_size}")

        expected_etag = row.get("etag")
        actual_etag = actual.get("etag")
        if expected_etag and actual_etag and expected_etag != actual_etag:
            errors.append(f"etag mismatch: expected {expected_etag}, actual {actual_etag}")

        expected_last_modified = row.get("last_modified")
        actual_last_modified = actual.get("last_modified")
        if (
            expected_last_modified
            and actual_last_modified
            and expected_last_modified != actual_last_modified
        ):
            errors.append(
                "last_modified mismatch: "
                f"expected {expected_last_modified}, actual {actual_last_modified}"
            )

        return _validation_result(row, valid=not errors, errors=errors)
    except Exception as error:
        if not keep_going:
            raise
        return _validation_result(row, valid=False, errors=[str(error)])


def _validation_result(
    row: dict[str, object],
    *,
    valid: bool,
    errors: list[str],
) -> dict[str, object]:
    result = {
        "item_id": row.get("item_id"),
        "asset_key": row.get("asset_key"),
        "store_type": normalize_store_type(row.get("store_type")),
        "store_container": row.get("store_container"),
        "key": row.get("key"),
        "valid": valid,
        "errors": errors,
    }
    if row.get("store_endpoint_url"):
        result["store_endpoint_url"] = _row_endpoint_url(row)
    return result


def validate_assets(
    assets: pa.Table,
    *,
    item_ids: set[str] | None = None,
    asset_keys: set[str] | None = None,
    keep_going: bool = False,
    max_workers: int = DEFAULT_COPY_MAX_WORKERS,
) -> list[dict[str, object]]:
    return _run_async(
        _validate_assets_async(
            assets,
            item_ids=item_ids,
            asset_keys=asset_keys,
            keep_going=keep_going,
            max_workers=max_workers,
        )
    )


async def _validate_assets_async(
    assets: pa.Table,
    *,
    item_ids: set[str] | None,
    asset_keys: set[str] | None,
    keep_going: bool,
    max_workers: int,
) -> list[dict[str, object]]:
    semaphore = asyncio.Semaphore(max(1, max_workers))

    async def guarded_validate(row: dict[str, object]) -> dict[str, object]:
        async with semaphore:
            return await _validate_row(
                row,
                keep_going=keep_going,
            )

    rows = [
        row
        for row in assets.to_pylist()
        if _selected(row, item_ids=item_ids, asset_keys=asset_keys)
    ]
    return await asyncio.gather(*(guarded_validate(row) for row in rows))


def _index(assets: pa.Table) -> dict[tuple[object, object], dict[str, object]]:
    return {(row.get("item_id"), row.get("asset_key")): row for row in assets.to_pylist()}


async def _copy_row(
    target_row: dict[str, object],
    source_index: dict[tuple[object, object], dict[str, object]],
    *,
    runtime: CopyRuntime,
    budget: CopyMemoryBudget,
    overwrite: bool,
    keep_going: bool,
) -> dict[str, object]:
    row = dict(target_row)
    key = (row.get("item_id"), row.get("asset_key"))
    source_row = source_index.get(key)
    source_href = href_from_location(source_row) if source_row else None
    target_href = href_from_location(row)
    LOGGER.debug(
        "copy row starting: item_id=%s asset_key=%s source=%s target=%s",
        row.get("item_id"),
        row.get("asset_key"),
        _log_location(source_href),
        _log_location(target_href),
    )
    if not isinstance(source_href, str) or not source_href:
        LOGGER.debug("copy row skipped: source location is empty for key=%s", key)
        return row
    if not isinstance(target_href, str) or not target_href:
        LOGGER.debug("copy row skipped: target location is empty for key=%s", key)
        return row
    if source_href == target_href:
        LOGGER.debug("copy row skipped: source and target locations are identical for key=%s", key)
        return row

    try:
        source_url, source_path = _store_url_and_path(source_href)
        target_url, target_path_value = _store_url_and_path(target_href)
        LOGGER.debug(
            "copy row stores resolved: source_url=%s source_path=%s target_url=%s target_path=%s",
            _log_location(source_url),
            _log_location(source_path),
            _log_location(target_url),
            _log_location(target_path_value),
        )
        source_store = _store_from_url(
            source_url,
            endpoint_url=_row_endpoint_url(source_row),
        )
        target_store = _store_from_url(
            target_url,
            endpoint_url=_row_endpoint_url(row),
        )
        async with budget.reserve():
            LOGGER.debug(
                "copy row memory reserved: reservation_bytes=%s current_reserved_bytes=%s",
                budget.reservation_bytes,
                budget.current_reserved_bytes,
            )
            response = await obs.get_async(source_store, source_path)
            await obs.put_async(
                target_store,
                target_path_value,
                response.stream(min_chunk_size=runtime.chunk_size_bytes),
                mode="overwrite" if overwrite else "create",
                chunk_size=runtime.chunk_size_bytes,
                max_concurrency=runtime.put_max_concurrency,
            )
        target_meta = await obs.head_async(target_store, target_path_value)
        row.update(_meta_fields(target_meta))
        LOGGER.debug(
            "copy row completed: target=%s size=%s etag=%s",
            _log_location(target_href),
            target_meta.get("size"),
            target_meta.get("e_tag"),
        )
    except Exception:
        if not keep_going:
            LOGGER.exception(
                "copy row failed: source=%s target=%s",
                _log_location(source_href),
                _log_location(target_href),
            )
            raise
        LOGGER.warning(
            "copy row failed but keep-going is enabled: source=%s target=%s",
            _log_location(source_href),
            _log_location(target_href),
        )
    return row


def copy_assets(
    source_assets: pa.Table,
    target_assets: pa.Table,
    *,
    overwrite: bool = True,
    keep_going: bool = False,
    max_workers: int = DEFAULT_COPY_MAX_WORKERS,
    memory_limit_bytes: int = DEFAULT_COPY_MEMORY_LIMIT_BYTES,
    chunk_size_bytes: int = DEFAULT_COPY_CHUNK_SIZE_BYTES,
    put_max_concurrency: int = DEFAULT_COPY_PUT_MAX_CONCURRENCY,
) -> pa.Table:
    return _run_async(
        _copy_assets_async(
            source_assets,
            target_assets,
            overwrite=overwrite,
            keep_going=keep_going,
            max_workers=max_workers,
            memory_limit_bytes=memory_limit_bytes,
            chunk_size_bytes=chunk_size_bytes,
            put_max_concurrency=put_max_concurrency,
        )
    )


async def _copy_assets_async(
    source_assets: pa.Table,
    target_assets: pa.Table,
    *,
    overwrite: bool,
    keep_going: bool,
    max_workers: int,
    memory_limit_bytes: int,
    chunk_size_bytes: int,
    put_max_concurrency: int,
) -> pa.Table:
    runtime = CopyRuntime(
        max_workers=max_workers,
        memory_limit_bytes=memory_limit_bytes,
        chunk_size_bytes=chunk_size_bytes,
        put_max_concurrency=put_max_concurrency,
    )
    budget = CopyMemoryBudget(
        limit_bytes=runtime.memory_limit_bytes,
        reservation_bytes=runtime.per_copy_memory_bytes,
    )
    source_index = _index(source_assets)
    target_rows = target_assets.to_pylist()
    semaphore = asyncio.Semaphore(runtime.effective_workers)
    LOGGER.debug(
        "copy assets starting: source_rows=%s target_rows=%s max_workers=%s "
        "effective_workers=%s memory_limit_bytes=%s per_copy_memory_bytes=%s "
        "chunk_size_bytes=%s put_max_concurrency=%s overwrite=%s keep_going=%s",
        source_assets.num_rows,
        len(target_rows),
        runtime.max_workers,
        runtime.effective_workers,
        runtime.memory_limit_bytes,
        runtime.per_copy_memory_bytes,
        runtime.chunk_size_bytes,
        runtime.put_max_concurrency,
        overwrite,
        keep_going,
    )

    async def guarded_copy(target_row: dict[str, object]) -> dict[str, object]:
        async with semaphore:
            return await _copy_row(
                target_row,
                source_index,
                runtime=runtime,
                budget=budget,
                overwrite=overwrite,
                keep_going=keep_going,
            )

    rows = await asyncio.gather(*(guarded_copy(target_row) for target_row in target_rows))

    LOGGER.info(
        "copy assets completed: rows=%s peak_reserved_bytes=%s",
        len(rows),
        budget.peak_reserved_bytes,
    )
    return asset_lock_table(rows)
