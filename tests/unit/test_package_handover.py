# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

import json
import shutil
import hashlib
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import pytest

from openaerialmap_fixture import write_localized_openaerialmap_item_collection_json
from stacpkg import cli
from stacpkg.arrow_io import read_parquet, read_stream
from stacpkg.assets import asset_lock_table, derive_asset_lock
from stacpkg.dataset import build_package
from stacpkg.stac_json import read_stac_json
from stacpkg.sources import resolve_item_assets
from stacpkg.workflows import relocate_package, validate_package


def _selection(tmp_path, item_count=1):
    source = tmp_path / "selection.json"
    write_localized_openaerialmap_item_collection_json(
        tmp_path,
        source,
        item_count=item_count,
        copy_asset_keys=("thumbnail",),
    )
    document = json.loads(source.read_text())
    for item in document["features"]:
        item["assets"] = {"thumbnail": item["assets"]["thumbnail"]}
    source.write_text(json.dumps(document))
    return source


@pytest.mark.parametrize("method", ["build", "relocate"])
def test_copy_uses_an_accessible_alternate_when_the_primary_is_unavailable(tmp_path, method):
    source = _selection(tmp_path)
    document = json.loads(source.read_text())
    asset = document["features"][0]["assets"]["thumbnail"]
    primary = Path(unquote(urlsplit(asset["href"]).path))
    expected_bytes = primary.read_bytes()
    alternate = tmp_path / "cached-thumbnail.png"
    shutil.copyfile(primary, alternate)
    primary.unlink()
    asset["alternate"] = {"cache": {"href": alternate.as_uri()}}
    source.write_text(json.dumps(document))

    package = tmp_path / "package"
    if method == "relocate":
        relocate_package(
            source, package, target=str(tmp_path / "destination"), source_alternate="cache"
        )
    else:
        build_package(source, package, include_assets=True, source_alternate="cache")
    alternate.unlink()

    lock = read_parquet(package / "assets.lock.parquet").to_pylist()[0]
    saved = read_parquet(package / "items.parquet").to_pylist()[0]["assets"]["thumbnail"]
    copied = Path(lock["key"])
    if not copied.is_absolute():
        copied = package / copied
    assert copied.read_bytes() == expected_bytes
    assert saved["alternate"]["original"]["href"] == primary.as_uri()
    assert list(validate_package(package))[-1]["valid"] is True


def test_offline_package_can_supply_another_copy_without_the_publisher(tmp_path, monkeypatch):
    source = _selection(tmp_path)
    document = json.loads(source.read_text())
    item = document["features"][0]
    item["links"] = [{"rel": "self", "href": "https://publisher.test/scene.json"}]
    original = Path(unquote(urlsplit(item["assets"]["thumbnail"]["href"]).path))
    expected = original.read_bytes()
    source.write_text(json.dumps(document))
    package = tmp_path / "source.pkg"
    build_package(source, package, include_assets=True)
    delivered = tmp_path / "delivered.pkg"
    shutil.move(package, delivered)
    saved_links = read_parquet(delivered / "items.parquet").to_pylist()[0]["links"]
    assert [(link["rel"], link["href"]) for link in saved_links] == [
        ("via", "https://publisher.test/scene.json")
    ]
    original.unlink()
    output = BytesIO()
    monkeypatch.setattr(cli.sys, "stdout", SimpleNamespace(buffer=output, isatty=lambda: False))

    assert cli.main(["items", "from-parquet", str(delivered / "items.parquet")]) == 0
    output.seek(0)
    copied = tmp_path / "copied.pkg"
    relocate_package(read_stream(output), copied, target=str(tmp_path / "copied-assets"))

    lock = read_parquet(copied / "assets.lock.parquet").to_pylist()[0]
    assert Path(lock["key"]).read_bytes() == expected
    assert list(validate_package(copied))[-1]["valid"] is True


@pytest.mark.parametrize("include_assets", [False, True])
def test_package_build_and_relocation_keep_complete_item_links(tmp_path, include_assets):
    source = _selection(tmp_path)
    document = json.loads(source.read_text())
    item = document["features"][0]
    self_link = {
        "rel": "self",
        "href": "https://publisher.test/scene.json",
        "type": "application/geo+json",
        "title": "Source Item",
        "custom:trace": "xyz",
    }
    search_link = {
        "rel": "via",
        "href": "https://publisher.test/search",
        "method": "POST",
        "body": {"ids": [item["id"]]},
        "headers": {"Accept": "application/geo+json"},
        "merge": True,
    }
    item["links"] = [self_link, search_link]
    source.write_text(json.dumps(document))
    expected = [
        {
            **self_link,
            "rel": "via" if include_assets else "self",
            "method": None,
            "body": None,
            "headers": None,
            "merge": None,
        },
        {**search_link, "type": None, "title": None, "custom:trace": None},
    ]
    package = tmp_path / "source.pkg"
    build_package(source, package, include_assets=include_assets)
    assert read_parquet(package / "items.parquet").column("links").to_pylist() == [expected]

    relocated = tmp_path / "relocated.pkg"
    relocate_package(package / "items.parquet", relocated, target=str(tmp_path / "copied-assets"))

    assert read_parquet(relocated / "items.parquet").column("links").to_pylist() == [expected]
    assert list(validate_package(relocated))[-1]["valid"] is True


@pytest.mark.parametrize("operation", ["build", "relocate"])
@pytest.mark.parametrize(
    "geometry",
    [
        pytest.param({"type": "Point", "coordinates": [16, 48, 100, 1]}, id="four-ordinates"),
        pytest.param({"type": "Point", "coordinates": [16]}, id="one-ordinate"),
        pytest.param(
            {"type": "LineString", "coordinates": [[16, 48, 100], [17, 49]]},
            id="mixed-line",
        ),
        pytest.param(
            {
                "type": "MultiLineString",
                "coordinates": [[[16, 48], [17, 49]], [[16, 48, 100], [17, 49, 200]]],
            },
            id="mixed-components",
        ),
        pytest.param(
            {
                "type": "Polygon",
                "coordinates": [
                    [[16, 48], [17, 48], [17, 49], [16, 48]],
                    [[16.1, 48.1, 100], [16.2, 48.1, 100], [16.2, 48.2, 100], [16.1, 48.1, 100]],
                ],
            },
            id="mixed-rings",
        ),
    ],
)
def test_invalid_geometry_dimensions_fail_before_package_or_copy(tmp_path, operation, geometry):
    source = _selection(tmp_path)
    document = json.loads(source.read_text())
    document["features"][0]["geometry"] = geometry
    source.write_text(json.dumps(document))
    package = tmp_path / "invalid.pkg"
    target = tmp_path / "copied-assets"

    with pytest.raises(ValueError, match="dimensions|ordinates"):
        if operation == "build":
            build_package(source, package, include_assets=True)
        else:
            relocate_package(source, package, target=str(target))

    assert not package.exists()
    assert not target.exists()


@pytest.mark.parametrize("kind", ["duplicate-id", "destination-collision"])
def test_build_rejects_ambiguous_items_before_copy(tmp_path, kind):
    source = _selection(tmp_path)
    document = json.loads(source.read_text())
    item = document["features"][0]
    item["id"] = "scene/a"
    document["features"].append({**item, "id": item["id"] if kind == "duplicate-id" else "scene_a"})
    source.write_text(json.dumps(document))
    package = tmp_path / "package"

    with pytest.raises(ValueError, match="unique|same destination"):
        build_package(source, package, include_assets=True)

    assert not package.exists()


@pytest.mark.parametrize("name", ["items.parquet", "assets.lock.parquet", "assets"])
def test_included_content_cannot_replace_generated_files(tmp_path, name):
    source = _selection(tmp_path)
    include = tmp_path / name
    include.write_bytes(b"xyz")
    package = tmp_path / "package"

    with pytest.raises(ValueError, match="duplicate package path"):
        build_package(source, package, include_assets=True, includes=[include])

    assert not package.exists()


def test_build_keeps_an_existing_package_unchanged(tmp_path):
    source = _selection(tmp_path)
    package = tmp_path / "package"
    build_package(source, package, probe_metadata=False)
    before = {path.name: path.read_bytes() for path in package.iterdir()}

    with pytest.raises(ValueError, match="output directory must not exist"):
        build_package(source, package, probe_metadata=False)

    assert {path.name: path.read_bytes() for path in package.iterdir()} == before


@pytest.mark.parametrize("kind", ["missing", "extra", "duplicate"])
def test_build_checks_the_supplied_lock_covers_the_selection(tmp_path, kind):
    source = _selection(tmp_path)
    rows = derive_asset_lock(read_stac_json(source), probe_metadata=False).to_pylist()
    if kind == "missing":
        rows = []
    elif kind == "extra":
        rows.append({**rows[0], "item_id": "other"})
    else:
        rows *= 2
    package = tmp_path / "package"

    with pytest.raises(ValueError, match="missing Item assets|outside the Item selection|unique"):
        build_package(source, package, asset_lock=asset_lock_table(rows))

    assert not package.exists()


@pytest.mark.parametrize("correct", [True, False])
def test_asset_inclusion_checks_item_and_lock_checksums_with_different_algorithms(
    tmp_path, correct
):
    source = _selection(tmp_path)
    items = resolve_item_assets(read_stac_json(source))
    lock = derive_asset_lock(items, checksum="calculate-always")
    document = json.loads(source.read_text())
    asset = document["features"][0]["assets"]["thumbnail"]
    contents = Path(unquote(urlsplit(asset["href"]).path)).read_bytes()
    asset["file:checksum"] = "1114" + hashlib.sha1(contents if correct else b"xyz").hexdigest()
    source.write_text(json.dumps(document))
    package = tmp_path / "package"

    if correct:
        build_package(source, package, asset_lock=lock, include_assets=True)
        assert list(validate_package(package))[-1]["valid"] is True
    else:
        with pytest.raises(ValueError, match="source checksum mismatch"):
            build_package(source, package, asset_lock=lock, include_assets=True)
        assert not package.exists()


def test_changed_bytes_with_the_same_size_fail_the_whole_package(tmp_path, monkeypatch):
    package = tmp_path / "package"
    build_package(_selection(tmp_path, item_count=2), package, include_assets=True)
    lock_path = package / "assets.lock.parquet"
    baseline = lock_path.read_bytes()
    rows = read_parquet(lock_path).to_pylist()
    changed = package / rows[1]["key"]
    contents = changed.read_bytes()
    changed.write_bytes(b"X" + contents[1:])
    assert changed.stat().st_size == rows[1]["size_bytes"]

    stdout = StringIO()
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    assert cli.main(["validate", str(package)]) == 1
    results = [json.loads(line) for line in stdout.getvalue().splitlines()]
    summary = results[-1]
    assert summary["checked"] == 2
    assert summary["passed"] == summary["failed"] == 1
    assert summary["lock_written"] is False
    failure = next(result for result in results[:-1] if not result["valid"])
    assert "checksum mismatch" in failure["errors"]
    assert failure["expected_checksum"] == rows[1]["file_checksum"]
    assert failure["observed_checksum"] != failure["expected_checksum"]
    assert lock_path.read_bytes() == baseline
