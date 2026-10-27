# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import hashlib
import json
import shutil
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import pyarrow as pa
import pytest

from openaerialmap_fixture import write_localized_openaerialmap_item_collection_json
from stacpkg import cli
from stacpkg.arrow_io import read_parquet, write_parquet, write_stream
from stacpkg.assets import asset_lock_table, derive_asset_lock
from stacpkg.checksums import checksum_from_metadata, etag_to_file_checksum
from stacpkg.dataset import build_package
from stacpkg.enrich import enrich_items
from stacpkg.geoparquet import write_items_geoparquet
from stacpkg.locators import href_from_location
from stacpkg.object_store import stat_assets, validate_assets
from stacpkg.sources import resolve_href, resolve_item_assets, select_item_assets
from stacpkg.stac_json import read_stac_json
from stacpkg.workflows import relocate_package, validate_package


def _items(tmp_path):
    path = tmp_path / "source.json"
    write_localized_openaerialmap_item_collection_json(tmp_path, path)
    items = select_item_assets(read_stac_json(path), {"thumbnail"})
    rows = items.to_pylist()
    assets = json.loads(rows[0]["assets_json"])
    assets["thumbnail"].pop("file:checksum", None)
    assets["thumbnail"].pop("file:size", None)
    rows[0]["assets_json"] = json.dumps(assets)
    return pa.Table.from_pylist(rows, schema=items.schema)


def _asset(items):
    return json.loads(items.to_pylist()[0]["assets_json"])["thumbnail"]


def _edit_asset(items, **fields):
    rows = items.to_pylist()
    assets = json.loads(rows[0]["assets_json"])
    assets["thumbnail"].update(fields)
    rows[0]["assets_json"] = json.dumps(assets)
    return pa.Table.from_pylist(rows, schema=items.schema)


def _checksum(path):
    return "1220" + hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("kind", ["FULL_OBJECT", "COMPOSITE", None])
def test_provider_checksum_requires_full_object_type(kind):
    digest = hashlib.sha256(b"file").digest()
    metadata = {"ChecksumType": kind, "ChecksumSHA256": base64.b64encode(digest).decode()}
    assert checksum_from_metadata(metadata) == (
        "1220" + digest.hex() if kind == "FULL_OBJECT" else None
    )
    assert (
        checksum_from_metadata({"ChecksumType": "FULL_OBJECT", "ChecksumCRC32": "AAAAAA=="}) is None
    )


def test_etag_promotion_is_explicit_and_rejects_weak_or_multipart_values(tmp_path, monkeypatch):
    items = _items(tmp_path)
    lock = derive_asset_lock(items, probe_metadata=False)
    digest = hashlib.md5(b"file").hexdigest()

    async def head(_href):
        return {"e_tag": '"' + digest + '"'}

    monkeypatch.setattr("stacpkg.object_store._head_href", head)
    assert stat_assets(lock).to_pylist()[0]["file_checksum"] is None
    assert stat_assets(lock, checksum="use-etag").to_pylist()[0][
        "file_checksum"
    ] == etag_to_file_checksum(digest)
    assert etag_to_file_checksum('W/"' + digest + '"') is None
    assert etag_to_file_checksum(digest + "-2") is None


def test_offline_lock_does_not_promote_unchecked_source_checksums(tmp_path):
    items = _edit_asset(_items(tmp_path), **{"file:checksum": "1220" + "00" * 32})
    lock = derive_asset_lock(items, probe_metadata=False, checksum="calculate-always")
    assert lock.to_pylist()[0]["file_checksum"] is None
    assert "file:checksum" not in _asset(enrich_items(items, lock))


@pytest.mark.parametrize(
    ("base", "href", "expected"),
    [
        ("s3://catalog/data/item.json", "../assets/image.tif", "s3://catalog/assets/image.tif"),
        ("https://catalog/data/", "image.tif", "https://catalog/data/image.tif"),
        ("https://catalog/data/item.json", "/assets/image.tif", "https://catalog/assets/image.tif"),
        (
            "https://catalog/data/image.tif",
            "?download=true",
            "https://catalog/data/image.tif?download=true",
        ),
    ],
)
def test_relative_href_resolution(base, href, expected):
    assert resolve_href(href, base) == expected


def test_relative_href_requires_a_known_base():
    with pytest.raises(ValueError, match="source base"):
        resolve_href("image.tif")


def test_source_self_link_resolves_relative_assets_unless_a_base_is_supplied(tmp_path):
    items = _edit_asset(_items(tmp_path), href="image.tif")
    rows = items.to_pylist()
    rows[0]["links_json"] = json.dumps(
        [{"rel": "self", "href": "https://publisher.test/scene.json"}]
    )
    items = pa.Table.from_pylist(rows, schema=items.schema)

    assert _asset(resolve_item_assets(items))["href"] == "https://publisher.test/image.tif"
    assert (
        _asset(resolve_item_assets(items, source_base=tmp_path / "items.json"))["href"]
        == (tmp_path / "image.tif").as_uri()
    )


def test_parquet_adapter_resolves_file_paths_from_its_document(tmp_path, monkeypatch):
    items = _items(tmp_path)
    original = Path(unquote(urlsplit(_asset(items)["href"]).path))
    image = tmp_path / "image with spaces.png"
    shutil.copyfile(original, image)
    rows = _edit_asset(items, href=image.name).to_pylist()
    rows[0]["links_json"] = "[]"
    items = pa.Table.from_pylist(rows, schema=items.schema)
    source = tmp_path / "selection.parquet"
    write_items_geoparquet(items, source)
    stdout = BytesIO()
    monkeypatch.setattr(cli.sys, "stdout", SimpleNamespace(buffer=stdout, isatty=lambda: False))
    assert cli.main(["items", "from-parquet", str(source)]) == 0
    stdout.seek(0)
    from stacpkg.arrow_io import read_stream

    asset = read_stream(stdout).to_pylist()[0]["assets"]["thumbnail"]
    assert asset["href"] == image.as_uri()


def test_source_alternate_selection(tmp_path):
    items = _items(tmp_path)
    href = _asset(items)["href"]
    items = _edit_asset(
        items,
        href="https://catalog/assets/image.tif",
        alternate={"s3": {"href": href}},
    )
    lock = derive_asset_lock(items, source_alternate="s3", checksum="calculate-always")
    assert href_from_location(lock.to_pylist()[0]) == href
    with pytest.raises(ValueError, match="missing alternate"):
        resolve_item_assets(items, source_alternate="absent")
    with pytest.raises(ValueError, match="credentials or signed"):
        resolve_href("https://catalog/image?X-Amz-Signature=xyz")


def test_relocate_copies_checks_and_projects_metadata(tmp_path):
    items = _items(tmp_path)
    package, target = tmp_path / "package", tmp_path / "copied"
    result = relocate_package(items, package, target=str(target))
    assert result["assets"] == 1
    lock = read_parquet(package / "assets.lock.parquet").to_pylist()[0]
    saved = read_parquet(package / "items.parquet").to_pylist()[0]["assets"]["thumbnail"]
    copied = Path(lock["key"])
    assert copied.is_file()
    assert lock["file_checksum"] == _checksum(copied) == saved["file:checksum"]
    assert saved["href"] == copied.as_uri()
    assert saved["alternate"]["original"]["href"] == _asset(items)["href"]
    summary = list(validate_package(package))[-1]
    assert summary["valid"] is True
    assert summary["methods"] == ["bytes-sha256"]


@pytest.mark.parametrize("method", ["relocate", "build", "build-no-probe"])
def test_source_checksum_mismatch_does_not_write_a_package(tmp_path, method):
    items = _edit_asset(_items(tmp_path), **{"file:checksum": "1220" + "00" * 32})
    package = tmp_path / "package"
    with pytest.raises(ValueError, match="source checksum mismatch"):
        if method == "relocate":
            relocate_package(items, package, target=str(tmp_path / "copied"))
        else:
            build_package(
                items, package, include_assets=True, probe_metadata=method != "build-no-probe"
            )
    assert not package.exists()


def test_copy_checks_destination_independently(tmp_path, monkeypatch):
    from stacpkg.object_store import obs

    put = obs.put_async

    async def corrupt(store, path, *args, **kwargs):
        result = await put(store, path, *args, **kwargs)
        Path(path).write_bytes(b"corrupt destination")
        return result

    monkeypatch.setattr(obs, "put_async", corrupt)
    package = tmp_path / "package"
    with pytest.raises(ValueError, match="destination checksum mismatch"):
        relocate_package(_items(tmp_path), package, target=str(tmp_path / "copied"))
    assert not package.exists()


def test_validation_detects_missing_or_duplicate_asset_rows(tmp_path):
    package = tmp_path / "package"
    relocate_package(_items(tmp_path), package, target=str(tmp_path / "copied"))
    lock_path = package / "assets.lock.parquet"
    rows = read_parquet(lock_path).to_pylist()
    write_parquet(asset_lock_table(rows * 2), lock_path)
    with pytest.raises(ValueError, match="unique"):
        list(validate_package(package))
    write_parquet(asset_lock_table([]), lock_path)
    with pytest.raises(ValueError, match="missing Item assets"):
        list(validate_package(package))


def test_validation_marks_first_observation_as_baseline(tmp_path):
    package = tmp_path / "package"
    build_package(_items(tmp_path), package, probe_metadata=False)
    results = list(validate_package(package))
    assert results[0]["comparison"] == "baseline"
    assert results[-1]["baseline"] == 1
    assert read_parquet(package / "assets.lock.parquet").to_pylist()[0]["file_checksum"]


def test_a_package_copy_accepts_new_storage_metadata_when_the_bytes_match(tmp_path):
    package = tmp_path / "folder with spaces" / "package"
    build_package(_items(tmp_path), package, include_assets=True)
    copied = tmp_path / "collection=xyz" / "copied-package"
    shutil.copytree(package, copied)
    results = list(validate_package(copied))
    assert results[-1]["valid"] is True
    assert results[0]["comparison"] == "match"
    assert results[0]["metadata_changes"]


def test_cli_relocate_and_validate(tmp_path, monkeypatch):
    stream, stdout = BytesIO(), StringIO()
    write_stream(_items(tmp_path), stream)
    stream.seek(0)
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(buffer=stream))
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    package = tmp_path / "package"
    assert cli.main(["relocate", str(package), "--target", str(tmp_path / "copied")]) == 0
    stdout.seek(0)
    stdout.truncate()
    assert cli.main(["validate", str(package)]) == 0
    assert json.loads(stdout.getvalue().splitlines()[-1])["valid"] is True
    row = read_parquet(package / "assets.lock.parquet").to_pylist()[0]
    Path(row["key"]).unlink()
    assert cli.main(["validate", str(package)]) == 1


def test_validation_with_no_comparable_checksum_does_not_claim_a_match(tmp_path):
    lock = derive_asset_lock(_items(tmp_path), checksum="calculate-always")
    result = validate_assets(lock, checksum="metadata")[0]
    assert result["valid"] is False
    assert "checksum unavailable for comparison" in result["errors"]


def test_validation_uses_the_expected_checksum_algorithm(tmp_path):
    lock = derive_asset_lock(_items(tmp_path), probe_metadata=False)
    rows = lock.to_pylist()
    rows[0]["file_checksum"] = "1114" + hashlib.sha1(Path(rows[0]["key"]).read_bytes()).hexdigest()
    result = validate_assets(asset_lock_table(rows), checksum="calculate-if-needed")[0]
    assert result["valid"] is True
    assert result["method"] == "bytes-sha1"
    assert result["observed_checksum"] == rows[0]["file_checksum"]


def test_package_checks_both_item_and_lock_checksum_algorithms(tmp_path):
    items = _items(tmp_path)
    image = Path(unquote(urlsplit(_asset(items)["href"]).path))
    declared = "1114" + hashlib.sha1(image.read_bytes()).hexdigest()
    items = _edit_asset(items, **{"file:checksum": declared})
    package = tmp_path / "package"
    build_package(items, package, checksum="calculate-always")
    results = list(validate_package(package))
    assert results[-1]["valid"] is True
    assert results[0]["declaration_check"]["expected_checksum"] == declared
    assert results[0]["declaration_check"]["observed_checksum"] == declared
    assert results[-1]["methods"] == ["bytes-sha1", "bytes-sha256"]


def test_validation_does_not_save_partial_results_or_changed_metadata(tmp_path):
    package = tmp_path / "package"
    build_package(_items(tmp_path), package, probe_metadata=False)
    lock_path = package / "assets.lock.parquet"
    before = lock_path.read_bytes()
    checks = validate_package(package)
    assert next(checks)["valid"] is True
    assert lock_path.read_bytes() == before
    items_path = package / "items.parquet"
    items_path.write_bytes(items_path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="package changed"):
        next(checks)
    assert lock_path.read_bytes() == before


def test_relocation_rejects_destination_name_collisions_before_copy(tmp_path):
    items = _items(tmp_path)
    row = items.to_pylist()[0]
    rows = [{**row, "id": "scene/a"}, {**row, "id": "scene_a"}]
    target = tmp_path / "copied"
    with pytest.raises(ValueError, match="same destination"):
        relocate_package(
            pa.Table.from_pylist(rows, schema=items.schema),
            tmp_path / "package",
            target=str(target),
        )
    assert not target.exists()


def test_checksum_observation_rejects_changes_during_read(tmp_path, monkeypatch):
    lock = derive_asset_lock(_items(tmp_path), probe_metadata=False)
    calls = 0

    async def head(_href):
        nonlocal calls
        calls += 1
        return {"e_tag": "first" if calls == 1 else "changed"}

    monkeypatch.setattr("stacpkg.object_store._head_href", head)
    with pytest.raises(ValueError, match="asset changed"):
        stat_assets(lock, checksum="calculate-always")
