# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import copy
import functools
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import pyarrow as pa

from stacpkg.arrow_io import write_parquet
from stacpkg.checksums import multihash_from_hex_digest
from tests.data.openaerialmap_data import (
    OPENAERIALMAP_ASSET_DIR,
    OPENAERIALMAP_ASSET_KEYS,
    openaerialmap_items,
)

LOCAL_OPENAERIALMAP_ASSET_KEYS = OPENAERIALMAP_ASSET_KEYS
FILE_EXTENSION = "https://stac-extensions.github.io/file/v2.1.0/schema.json"


def _suffix(asset: dict[str, object]) -> str:
    href = asset.get("href")
    if not isinstance(href, str):
        return ".bin"
    parsed = urlparse(href)
    suffix = Path(parsed.path).suffix
    return suffix or ".bin"


def _asset_file(item_id: str, asset_key: str, source_asset: dict[str, object]) -> Path:
    path = OPENAERIALMAP_ASSET_DIR / item_id / f"{asset_key}{_suffix(source_asset)}"
    if not path.is_file():
        raise FileNotFoundError(f"missing downloaded OpenAerialMap asset fixture: {path}")
    return path


@functools.cache
def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    checksum = multihash_from_hex_digest("sha256", digest.hexdigest())
    assert checksum is not None
    return checksum


def _copy_asset(tmp_path: Path, item_id: str, asset_key: str, source_path: Path) -> Path:
    path = tmp_path / "openaerialmap-local-assets" / item_id / source_path.name
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, path)
    return path


def _localize_asset(
    tmp_path: Path,
    *,
    item_id: str,
    asset_key: str,
    source_asset: dict[str, object],
    add_file_info: bool,
    copy_asset_keys: set[str],
) -> dict[str, object]:
    asset = copy.deepcopy(source_asset)
    path = _asset_file(item_id, asset_key, asset)
    if asset_key in copy_asset_keys:
        path = _copy_asset(tmp_path, item_id, asset_key, path)
    asset["href"] = path.as_uri()
    asset["alternate:name"] = "local"
    if add_file_info:
        asset["file:size"] = path.stat().st_size
        asset["file:checksum"] = _checksum(path)
    return asset


def _asset_key_set(asset_keys: Iterable[str] | None) -> set[str]:
    return set(asset_keys or ())


def localized_openaerialmap_items(
    tmp_path: Path,
    *,
    item_count: int = 1,
    copy_asset_keys: Iterable[str] | None = None,
) -> pa.Table:
    """Return OpenAerialMap fixture items with small linked assets backed by files."""
    source = openaerialmap_items(item_count=item_count)
    copy_asset_keys = _asset_key_set(copy_asset_keys)
    rows = []

    for source_row in source.to_pylist():
        row = copy.deepcopy(source_row)
        item_id = str(row["id"])
        assets = {}

        for asset_key, asset_value in row["assets"].items():
            if not isinstance(asset_value, dict) or not asset_value.get("href"):
                assets[asset_key] = asset_value
                continue
            asset = dict(asset_value)
            if asset_key in LOCAL_OPENAERIALMAP_ASSET_KEYS:
                asset = _localize_asset(
                    tmp_path,
                    item_id=item_id,
                    asset_key=asset_key,
                    source_asset=asset,
                    add_file_info=False,
                    copy_asset_keys=copy_asset_keys,
                )
            else:
                asset["href"] = None
            assets[asset_key] = asset

        row["assets"] = assets
        rows.append(row)

    return pa.Table.from_pylist(rows, schema=source.schema)


def _bbox_list(bbox: object) -> list[float]:
    if isinstance(bbox, dict):
        return [
            float(bbox["xmin"]),
            float(bbox["ymin"]),
            float(bbox["xmax"]),
            float(bbox["ymax"]),
        ]
    if isinstance(bbox, list):
        return [float(part) for part in bbox]
    raise TypeError(f"unsupported OpenAerialMap bbox: {bbox!r}")


def _bbox_polygon(bbox: list[float]) -> dict[str, object]:
    xmin, ymin, xmax, ymax = bbox
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [xmin, ymin],
                [xmax, ymin],
                [xmax, ymax],
                [xmin, ymax],
                [xmin, ymin],
            ]
        ],
    }


def _json_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    return value


def localized_openaerialmap_item_collection(
    tmp_path: Path,
    *,
    item_count: int = 1,
    copy_asset_keys: Iterable[str] | None = None,
) -> dict[str, object]:
    """Return OpenAerialMap fixture Items as JSON with local linked assets."""
    source = openaerialmap_items(item_count=item_count)
    copy_asset_keys = _asset_key_set(copy_asset_keys)
    features = []

    for source_row in source.to_pylist():
        item_id = str(source_row["id"])
        bbox = _bbox_list(source_row["bbox"])
        stac_extensions = list(source_row.get("stac_extensions") or [])
        if FILE_EXTENSION not in stac_extensions:
            stac_extensions.append(FILE_EXTENSION)

        assets = {}
        for asset_key in LOCAL_OPENAERIALMAP_ASSET_KEYS:
            assets[asset_key] = _localize_asset(
                tmp_path,
                item_id=item_id,
                asset_key=asset_key,
                source_asset=source_row["assets"][asset_key],
                add_file_info=True,
                copy_asset_keys=copy_asset_keys,
            )

        properties = {
            key: _json_value(value)
            for key, value in source_row.items()
            if key
            not in {
                "assets",
                "bbox",
                "collection",
                "geometry",
                "id",
                "links",
                "providers",
                "stac_extensions",
                "stac_version",
            }
        }

        features.append(
            {
                "type": "Feature",
                "stac_version": source_row["stac_version"],
                "stac_extensions": stac_extensions,
                "id": item_id,
                "collection": source_row["collection"],
                "bbox": bbox,
                "geometry": _bbox_polygon(bbox),
                "properties": properties,
                "links": source_row.get("links") or [],
                "assets": assets,
            }
        )

    return {"type": "FeatureCollection", "features": features}


def write_localized_openaerialmap_item_collection_json(
    tmp_path: Path,
    output: Path,
    *,
    item_count: int = 1,
    copy_asset_keys: Iterable[str] | None = None,
) -> Path:
    output.write_text(
        json.dumps(
            localized_openaerialmap_item_collection(
                tmp_path,
                item_count=item_count,
                copy_asset_keys=copy_asset_keys,
            ),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return output


def write_localized_openaerialmap_items_parquet(
    tmp_path: Path,
    output: Path,
    *,
    item_count: int = 1,
    copy_asset_keys: Iterable[str] | None = None,
) -> Path:
    write_parquet(
        localized_openaerialmap_items(
            tmp_path,
            item_count=item_count,
            copy_asset_keys=copy_asset_keys,
        ),
        output,
    )
    return output
