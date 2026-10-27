# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

from stacpkg.arrow_io import read_parquet, write_parquet
from stacpkg.locators import href_from_location
from tests.unit.openaerialmap_fixture import (
    localized_openaerialmap_items,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class UsecaseShell:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.outputs: list[dict[str, str]] = []

    def run(self, command: str) -> None:
        command = dedent(command).strip()
        if not command:
            return
        result = subprocess.run(
            ["bash", "-c", f"set -euo pipefail\n{command}\n"],
            cwd=self.tmp_path,
            env={
                "PATH": os.pathsep.join([str(Path(sys.executable).parent), "/usr/bin", "/bin"]),
                "PYTHONPATH": os.pathsep.join([str(REPO_ROOT / "src"), str(REPO_ROOT)]),
            },
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            self.outputs.append(
                {
                    "command": command,
                    "stdout": result.stdout.replace(str(self.tmp_path), "/workshop"),
                    "stderr": result.stderr.replace(str(self.tmp_path), "/workshop"),
                }
            )
            return
        raise AssertionError(
            "usecase shell command failed\n"
            f"exit code: {result.returncode}\n\n"
            f"command:\n{command}\n\n"
            f"stdout:\n{result.stdout}\n\n"
            f"stderr:\n{result.stderr}"
        )

    def save_output(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.outputs), encoding="utf-8")


def setup_openaerialmap_items(tmp_path: Path, value: str, *, item_count: int = 3) -> Path:
    path = _p(tmp_path, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_parquet(
        localized_openaerialmap_items(
            tmp_path,
            item_count=item_count,
            copy_asset_keys=("metadata", "thumbnail"),
        ),
        path,
    )
    return path


def assert_parquet_rows(tmp_path: Path, value: str, count: int) -> None:
    path = _p(tmp_path, value)
    assert path.exists(), f"missing parquet file: {value}"
    assert read_parquet(path).num_rows == count


def assert_parquet_columns(tmp_path: Path, value: str, *columns: str) -> None:
    table = _read_parquet(tmp_path, value)
    missing = [column for column in columns if column not in table.schema.names]
    assert missing == [], f"{value} is missing expected columns: {missing}"


def assert_package_items(tmp_path: Path, value: str, count: int) -> None:
    assert_parquet_rows(tmp_path, f"{value.rstrip('/')}/items.parquet", count)


def assert_package_assets(tmp_path: Path, value: str, count: int) -> None:
    assert_parquet_rows(tmp_path, f"{value.rstrip('/')}/assets.lock.parquet", count)


def assert_asset_lock_keys(tmp_path: Path, value: str, *asset_keys: str) -> None:
    rows = _read_parquet(tmp_path, value).to_pylist()
    assert {row.get("asset_key") for row in rows} == set(asset_keys)


def assert_asset_lock_store(
    tmp_path: Path,
    value: str,
    store_type: str,
    *,
    container: str | None = None,
    key_prefix: str | None = None,
) -> None:
    rows = _read_parquet(tmp_path, value).to_pylist()
    assert rows, f"{value} has no asset-lock rows"
    assert {row.get("store_type") for row in rows} == {store_type}
    if container is not None:
        assert {row.get("store_container") for row in rows} == {container}
    if key_prefix is not None:
        keys = [row.get("key") for row in rows]
        assert all(isinstance(key, str) and key.startswith(key_prefix) for key in keys)


def assert_file_exists(tmp_path: Path, value: str) -> None:
    assert _p(tmp_path, value).exists(), f"expected file to exist: {value}"


def assert_no_file(tmp_path: Path, value: str) -> None:
    assert not _p(tmp_path, value).exists(), f"expected file to be absent: {value}"


def assert_verified_package(tmp_path: Path, value: str) -> None:
    package = _p(tmp_path, value)
    rows = read_parquet(package / "assets.lock.parquet").to_pylist()
    assets = {
        (item["id"], key): asset
        for item in read_parquet(package / "items.parquet").to_pylist()
        for key, asset in item["assets"].items()
        if asset and asset.get("href")
    }
    assert rows
    for row in rows:
        path = Path(row["key"])
        if not path.is_absolute():
            path = package / path
        digest = "1220" + hashlib.sha256(path.read_bytes()).hexdigest()
        asset = assets[(row["item_id"], row["asset_key"])]
        assert row["file_checksum"] == asset["file:checksum"] == digest
        assert row["size_bytes"] == asset["file:size"] == path.stat().st_size
        assert asset["href"] == href_from_location(row)
        assert asset["alternate"]["original"]["href"] != asset["href"]


def assert_validation_report(tmp_path: Path, value: str, count: int) -> None:
    results = [json.loads(line) for line in _p(tmp_path, value).read_text().splitlines()]
    assets, summary = results[:-1], results[-1]
    assert len(assets) == count
    assert all(result["valid"] for result in assets)
    assert all(result["expected_checksum"] == result["observed_checksum"] for result in assets)
    assert summary["type"] == "summary"
    assert summary["valid"] is True
    assert summary["checked"] == summary["passed"] == count
    assert summary["failed"] == 0
    assert summary["methods"] == ["bytes-sha256"]


def _p(tmp_path: Path, value: str) -> Path:
    return tmp_path / value.rstrip("/")


def _read_parquet(tmp_path: Path, value: str):
    path = _p(tmp_path, value)
    assert path.exists(), f"missing parquet file: {value}"
    return read_parquet(path)
