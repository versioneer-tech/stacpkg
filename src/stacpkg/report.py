# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stacpkg.arrow_io import read_parquet
from stacpkg.dataset import (
    ASSET_LOCK_PACKAGE_PATH,
    ITEMS_PACKAGE_PATH,
    package_file_entries,
)


def package_inspect_data(package_dir: str | Path) -> dict[str, Any]:
    package_dir = Path(package_dir)
    data: dict[str, Any] = {
        "package": str(package_dir),
        "files": package_file_entries(package_dir),
    }

    items_path = package_dir / ITEMS_PACKAGE_PATH
    if items_path.exists():
        items = read_parquet(items_path)
        item_rows = items.to_pylist()
        data["items"] = {
            "count": items.num_rows,
            "collections": sorted(
                {row["collection"] for row in item_rows if row.get("collection") is not None}
            ),
        }
    else:
        data["items"] = {"count": 0, "collections": []}

    assets_path = package_dir / ASSET_LOCK_PACKAGE_PATH
    if assets_path.exists():
        assets = read_parquet(assets_path)
        asset_rows = assets.to_pylist()
        known_sizes = [
            int(row["size_bytes"]) for row in asset_rows if row.get("size_bytes") is not None
        ]
        data["assets"] = {
            "count": assets.num_rows,
            "asset_keys": sorted(
                {row["asset_key"] for row in asset_rows if row.get("asset_key") is not None}
            ),
            "known_size_bytes": sum(known_sizes),
            "known_size_count": len(known_sizes),
        }
    else:
        data["assets"] = {
            "count": 0,
            "asset_keys": [],
            "known_size_bytes": 0,
            "known_size_count": 0,
        }

    return data


def _yaml_scalar(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return json.dumps(str(value))


def _yaml_lines(value: object, *, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        if not value:
            return [f"{prefix}{{}}"]
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, dict | list):
                lines.append(f"{prefix}{key}:")
                lines.extend(_yaml_lines(item, indent=indent + 2))
            else:
                lines.append(f"{prefix}{key}: {_yaml_scalar(item)}")
        return lines
    if isinstance(value, list):
        if not value:
            return [f"{prefix}[]"]
        lines = []
        for item in value:
            if isinstance(item, dict | list):
                lines.append(f"{prefix}-")
                lines.extend(_yaml_lines(item, indent=indent + 2))
            else:
                lines.append(f"{prefix}- {_yaml_scalar(item)}")
        return lines
    return [f"{prefix}{_yaml_scalar(value)}"]


def package_inspect_yaml(package_dir: str | Path) -> str:
    return "\n".join(_yaml_lines(package_inspect_data(package_dir))) + "\n"


def package_inspect_markdown(package_dir: str | Path) -> str:
    data = package_inspect_data(package_dir)
    items = data["items"]
    assets = data["assets"]
    lines = [
        "# stacpkg Inspect",
        "",
        f"- Package: `{data['package']}`",
        f"- Items: {items['count']}",
        f"- Collections: {', '.join(items['collections']) or 'none'}",
        f"- Assets: {assets['count']}",
        f"- Asset keys: {', '.join(assets['asset_keys']) or 'none'}",
        f"- Known asset bytes: {assets['known_size_bytes']}",
        "",
        "## Files",
        "",
    ]
    files = data.get("files") or []
    if not files:
        lines.append("No files recorded.")
    else:
        for file_entry in files:
            if not isinstance(file_entry, dict):
                continue
            lines.append(
                f"- `{file_entry.get('path')}` "
                f"({file_entry.get('mediaType')}, {file_entry.get('size')} bytes)"
            )
    lines.append("")
    return "\n".join(lines)
