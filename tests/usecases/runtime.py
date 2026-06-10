# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from stacpkg.arrow_io import read_parquet, write_parquet
from stacpkg.assets import derive_asset_lock
from stacpkg.items import filter_items
from tests.data.openaerialmap_data import OPENAERIALMAP_S3
from tests.unit.openaerialmap_fixture import (
    localized_openaerialmap_item_collection,
    localized_openaerialmap_items,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class UsecaseShell:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.bin_dir = tmp_path / ".usecase-bin"
        self.fake_oci_dir = tmp_path / ".usecase-oci"
        self._install_shims()
        self.env = self._environment()

    def run(self, command: str) -> None:
        command = command.strip()
        if not command:
            return
        result = subprocess.run(
            ["bash", "-c", f"set -euo pipefail\n{command}\n"],
            cwd=self.tmp_path,
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return
        raise AssertionError(
            "usecase shell command failed\n"
            f"exit code: {result.returncode}\n\n"
            f"command:\n{command}\n\n"
            f"stdout:\n{result.stdout}\n\n"
            f"stderr:\n{result.stderr}"
        )

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        pythonpath = [
            str(REPO_ROOT),
            str(REPO_ROOT / "src"),
            *(part for part in env.get("PYTHONPATH", "").split(os.pathsep) if part),
        ]
        env["PATH"] = os.pathsep.join(
            [
                str(self.bin_dir),
                str(Path(sys.executable).parent),
                env.get("PATH", ""),
            ]
        )
        env["PYTHONPATH"] = os.pathsep.join(pythonpath)
        env.setdefault("STACPKG_USECASE_RUN_ID", "pytest")
        env["STACPKG_FAKE_OCI_DIR"] = str(self.fake_oci_dir)
        env.setdefault("STACPKG_OBSTORE_ALLOW_HTTP", "true")
        return env

    def _install_shims(self) -> None:
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        wrapper = self.tmp_path / ".usecase-stacpkg-wrapper.py"
        wrapper.write_text(_STACPKG_WRAPPER, encoding="utf-8")
        _write_executable(
            self.bin_dir / "stacpkg",
            f'exec {shlex.quote(sys.executable)} {shlex.quote(str(wrapper))} "$@"\n',
        )
        for name in ("curl", "rustac", "gpio"):
            _write_executable(
                self.bin_dir / name,
                f'exec {shlex.quote(sys.executable)} -m tests.usecases.runtime {name} "$@"\n',
            )


def setup_openaerialmap_items(tmp_path: Path, value: str, *, item_count: int = 3) -> Path:
    path = _p(tmp_path, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_parquet(localized_openaerialmap_items(tmp_path, item_count=item_count), path)
    return path


def setup_openaerialmap_s3_items(tmp_path: Path, value: str, *, item_count: int = 3) -> Path:
    source = read_parquet(OPENAERIALMAP_S3)
    if source.num_rows < item_count:
        raise ValueError(f"OpenAerialMap S3 fixture only has {source.num_rows} rows")
    path = _p(tmp_path, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_parquet(source.slice(0, item_count), path)
    return path


def setup_openaerialmap_asset_lock(tmp_path: Path, value: str, *, item_count: int = 3) -> Path:
    path = _p(tmp_path, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_parquet(
        derive_asset_lock(
            localized_openaerialmap_items(tmp_path, item_count=item_count),
            probe_metadata=False,
        ),
        path,
    )
    return path


def setup_openaerialmap_provider_items(
    tmp_path: Path,
    value: str,
    *,
    item_count: int = 3,
) -> Path:
    path = _p(tmp_path, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_parquet(_openaerialmap_provider_items(tmp_path, item_count=item_count), path)
    return path


def setup_openaerialmap_provider_asset_lock(
    tmp_path: Path,
    value: str,
    *,
    item_count: int = 3,
) -> Path:
    path = _p(tmp_path, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_parquet(
        derive_asset_lock(
            _openaerialmap_provider_items(tmp_path, item_count=item_count),
            probe_metadata=False,
        ),
        path,
    )
    return path


def setup_file(tmp_path: Path, value: str, *, text: str) -> Path:
    path = _p(tmp_path, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def assert_parquet_rows(tmp_path: Path, value: str, count: int) -> None:
    path = _p(tmp_path, value)
    assert path.exists(), f"missing parquet file: {value}"
    assert read_parquet(path).num_rows == count


def assert_parquet_columns(tmp_path: Path, value: str, *columns: str) -> None:
    table = _read_parquet(tmp_path, value)
    missing = [column for column in columns if column not in table.schema.names]
    assert missing == [], f"{value} is missing expected columns: {missing}"


def assert_parquet_equals(tmp_path: Path, left: str, right: str) -> None:
    left_table = _read_parquet(tmp_path, left)
    right_table = _read_parquet(tmp_path, right)
    assert left_table.to_pylist() == right_table.to_pylist()


def assert_package_items(tmp_path: Path, value: str, count: int) -> None:
    assert_parquet_rows(tmp_path, f"{value.rstrip('/')}/items.parquet", count)


def assert_package_assets(tmp_path: Path, value: str, count: int) -> None:
    assert_parquet_rows(tmp_path, f"{value.rstrip('/')}/assets.lock.parquet", count)


def assert_package_file(tmp_path: Path, package: str, relative_path: str) -> None:
    assert_file_exists(tmp_path, f"{package.rstrip('/')}/{relative_path}")


def assert_package_asset_files(tmp_path: Path, package: str, count: int) -> None:
    asset_root = _p(tmp_path, f"{package.rstrip('/')}/assets")
    assert asset_root.is_dir(), f"expected package asset directory: {package}/assets"
    files = [path for path in asset_root.rglob("*") if path.is_file()]
    assert len(files) == count


def assert_item_provider_names(tmp_path: Path, value: str, *names: str) -> None:
    rows = _read_parquet(tmp_path, value).to_pylist()
    expected = set(names)
    for row in rows:
        actual = {
            provider.get("name")
            for provider in row.get("providers", [])
            if isinstance(provider, dict)
        }
        assert actual == expected


def assert_item_asset_hrefs(
    tmp_path: Path,
    value: str,
    prefix: str,
    *,
    asset_keys: tuple[str, ...] = (),
) -> None:
    hrefs = _item_hrefs(tmp_path, value, asset_keys=asset_keys)
    for href in hrefs:
        assert href.startswith(prefix)


def assert_item_alternate_hrefs(
    tmp_path: Path,
    value: str,
    alternate_key: str,
    prefix: str,
    *,
    asset_keys: tuple[str, ...] = (),
) -> None:
    hrefs = []
    for _asset_key, asset in _selected_item_assets(tmp_path, value, asset_keys=asset_keys):
        alternate = asset.get("alternate") or {}
        if not isinstance(alternate, dict):
            continue
        target = alternate.get(alternate_key) or {}
        if isinstance(target, dict) and isinstance(href := target.get("href"), str) and href:
            hrefs.append(href)
    assert hrefs, f"{value} has no matching alternate hrefs for {alternate_key}"
    for href in hrefs:
        assert href.startswith(prefix)


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


def shim_curl(argv: Sequence[str]) -> int:
    output = _option_value(argv, "--output") or _option_value(argv, "-o")
    document = localized_openaerialmap_item_collection(Path.cwd(), item_count=3)
    text = json.dumps(document, sort_keys=True) + "\n"
    if output is None:
        print(text, end="")
        return 0
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return 0


def shim_rustac(_argv: Sequence[str]) -> int:
    document = localized_openaerialmap_item_collection(Path.cwd(), item_count=3)
    for feature in document.get("features") or []:
        print(json.dumps(feature, sort_keys=True))
    return 0


def shim_gpio(argv: Sequence[str]) -> int:
    args = list(argv)
    if len(args) >= 2 and args[0] == "inspect":
        path = Path(args[1])
        if not path.exists():
            print(f"missing file: {path}", file=sys.stderr)
            return 1
        print(json.dumps({"path": str(path), "format": "parquet"}))
        return 0
    if len(args) >= 4 and args[:2] == ["sort", "hilbert"]:
        source = Path(args[2])
        target = Path(args[3])
        if not source.exists():
            print(f"missing file: {source}", file=sys.stderr)
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return 0
    if len(args) >= 3 and args[:2] == ["check", "all"]:
        path = Path(args[2])
        if not path.exists():
            print(f"missing file: {path}", file=sys.stderr)
            return 1
        return 0
    print(f"unsupported gpio shim command: {' '.join(args)}", file=sys.stderr)
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("expected shim command", file=sys.stderr)
        return 1
    command = args.pop(0)
    if command == "curl":
        return shim_curl(args)
    if command == "rustac":
        return shim_rustac(args)
    if command == "gpio":
        return shim_gpio(args)
    print(f"unsupported usecase shim: {command}", file=sys.stderr)
    return 1


def _p(tmp_path: Path, value: str) -> Path:
    return tmp_path / value.rstrip("/")


def _read_parquet(tmp_path: Path, value: str):
    path = _p(tmp_path, value)
    assert path.exists(), f"missing parquet file: {value}"
    return read_parquet(path)


def _openaerialmap_provider_items(tmp_path: Path, *, item_count: int):
    return filter_items(
        localized_openaerialmap_items(tmp_path, item_count=item_count),
        providers={"ODM"},
    )


def _selected_item_assets(
    tmp_path: Path,
    value: str,
    *,
    asset_keys: tuple[str, ...],
) -> list[tuple[str, dict[str, object]]]:
    selected = set(asset_keys)
    pairs = []
    for row in _read_parquet(tmp_path, value).to_pylist():
        assets = row.get("assets") or {}
        if not isinstance(assets, dict):
            continue
        if selected:
            missing = selected.difference(assets)
            assert missing == set(), f"{value} is missing assets: {sorted(missing)}"
        for asset_key, asset in assets.items():
            if selected and asset_key not in selected:
                continue
            if isinstance(asset, dict):
                pairs.append((str(asset_key), asset))
    return pairs


def _item_hrefs(
    tmp_path: Path,
    value: str,
    *,
    asset_keys: tuple[str, ...],
) -> list[str]:
    hrefs = []
    for asset_key, asset in _selected_item_assets(tmp_path, value, asset_keys=asset_keys):
        href = asset.get("href")
        assert isinstance(href, str) and href, f"{value} asset {asset_key} has no href"
        hrefs.append(href)
    assert hrefs, f"{value} has no matching asset hrefs"
    return hrefs


def _write_executable(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _option_value(tokens: Sequence[str], option: str) -> str | None:
    values = []
    for index, token in enumerate(tokens):
        prefix = f"{option}="
        if token.startswith(prefix):
            values.append(token.removeprefix(prefix))
            continue
        if token == option and index + 1 < len(tokens):
            values.append(tokens[index + 1])
    return values[-1] if values else None


_STACPKG_WRAPPER = r"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import stacpkg.cli as cli
import stacpkg.object_store as object_store
import stacpkg.oci as oci


async def fake_head_href(*_args, **_kwargs):
    return {}


def fake_copy_assets(_source_assets, target_assets, **_kwargs):
    return target_assets


class FakeAuth:
    def load_configs(self, _container):
        return None


def fake_oci_root():
    root = Path(os.environ["STACPKG_FAKE_OCI_DIR"])
    root.mkdir(parents=True, exist_ok=True)
    (root / "blobs").mkdir(exist_ok=True)
    (root / "manifests").mkdir(exist_ok=True)
    return root


def target_key(target):
    return hashlib.sha256(target.encode("utf-8")).hexdigest()


def manifest_path(target):
    return fake_oci_root() / "manifests" / f"{target_key(target)}.json"


def blob_path(digest):
    return fake_oci_root() / "blobs" / digest.replace(":", "_")


class FakeOrasClient:
    def __init__(self, *, insecure=False, tls_verify=True):
        self.auth = FakeAuth()

    def get_container(self, target):
        return target

    def upload_blob(self, blob, _container, layer):
        blob_path(str(layer["digest"])).write_bytes(Path(blob).read_bytes())
        return object()

    def upload_manifest(self, manifest, container):
        manifest_path(container).write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
        return object()

    def _check_200_response(self, _response):
        return None

    def get_manifest(self, target):
        return json.loads(manifest_path(target).read_text(encoding="utf-8"))

    def download_blob(self, _target, digest, outfile):
        Path(outfile).write_bytes(blob_path(str(digest)).read_bytes())
        return outfile


object_store._head_href = fake_head_href
cli.copy_assets = fake_copy_assets
oci.OrasClient = FakeOrasClient

raise SystemExit(cli.main(sys.argv[1:]))
""".lstrip()


if __name__ == "__main__":
    raise SystemExit(main())
