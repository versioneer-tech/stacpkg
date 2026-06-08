# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import posixpath
from pathlib import Path
from urllib.parse import urlparse

OBSTORE_STORE_TYPES = ("file", "s3", "gs", "az", "http", "https")
_STORE_TYPE_ALIASES = {
    "local": "file",
    "file": "file",
    "s3": "s3",
    "s3a": "s3",
    "gs": "gs",
    "gcs": "gs",
    "az": "az",
    "abfs": "az",
    "abfss": "az",
    "http": "http",
    "https": "https",
}


def normalize_store_type(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return _STORE_TYPE_ALIASES.get(value.strip().lower())


def location_from_href(href: object) -> dict[str, object]:
    if not isinstance(href, str) or not href:
        return {"store_type": None, "store_container": None, "key": None}

    parsed = urlparse(href)
    if not parsed.scheme:
        return {"store_type": "file", "store_container": None, "key": href}
    if parsed.scheme == "file":
        return {"store_type": "file", "store_container": None, "key": parsed.path}
    if parsed.scheme in {"s3", "s3a", "gs", "gcs", "az", "abfs", "abfss"}:
        return {
            "store_type": normalize_store_type(parsed.scheme),
            "store_container": parsed.netloc or None,
            "key": parsed.path.lstrip("/"),
        }
    if parsed.scheme in {"http", "https"}:
        if parsed.query:
            return {
                "store_type": parsed.scheme,
                "store_container": href,
                "key": "",
            }
        return {
            "store_type": parsed.scheme,
            "store_container": f"{parsed.scheme}://{parsed.netloc}",
            "key": parsed.path.lstrip("/"),
        }
    return {"store_type": parsed.scheme, "store_container": parsed.netloc or None, "key": href}


def href_from_location(row: dict[str, object]) -> str | None:
    legacy_href = row.get("href")
    store_type = normalize_store_type(row.get("store_type"))
    key = row.get("key")
    container = row.get("store_container")

    if store_type is None:
        return legacy_href if isinstance(legacy_href, str) and legacy_href else None
    if not isinstance(key, str):
        return None
    if store_type == "file":
        if not key:
            return None
        if key.startswith("/"):
            return Path(key).as_uri()
        return key
    if store_type in {"s3", "gs", "az"}:
        if not isinstance(container, str) or not container:
            return None
        return f"{store_type}://{container}/{key.lstrip('/')}"
    if store_type in {"http", "https"}:
        if not isinstance(container, str) or not container:
            return None
        if not key:
            return container
        return f"{container.rstrip('/')}/{key.lstrip('/')}"
    return legacy_href if isinstance(legacy_href, str) and legacy_href else None


def child_location(base: dict[str, object], path: str) -> dict[str, object]:
    key = base.get("key")
    base_key = key if isinstance(key, str) else ""
    return {
        "store_type": base.get("store_type"),
        "store_container": base.get("store_container"),
        "store_endpoint_url": base.get("store_endpoint_url"),
        "key": posixpath.join(base_key.rstrip("/"), path.lstrip("/")) if base_key else path,
    }
