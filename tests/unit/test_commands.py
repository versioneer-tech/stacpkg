# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

import json
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from openaerialmap_fixture import write_localized_openaerialmap_item_collection_json
from stacpkg import cli
from stacpkg.arrow_io import read_parquet, read_stream, write_stream
from stacpkg.assets import derive_asset_lock
from stacpkg.dataset import build_package
from stacpkg.geoparquet import (
    extract_collections,
    items_table_to_geoparquet_table,
    write_items_geoparquet,
)
from stacpkg.stac_json import read_stac_json


@pytest.fixture
def items(tmp_path):
    source = tmp_path / "selection.json"
    write_localized_openaerialmap_item_collection_json(
        tmp_path, source, copy_asset_keys={"thumbnail", "metadata"}
    )
    return items_table_to_geoparquet_table(read_stac_json(source))


def _pipe_input(monkeypatch, table):
    stream = BytesIO()
    write_stream(table, stream)
    stream.seek(0)
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(buffer=stream))


def _collection(collection_id):
    return {
        "type": "Collection",
        "id": collection_id,
        "description": "Selected imagery.",
        "license": "proprietary",
        "extent": {
            "spatial": {"bbox": [[-180, -90, 180, 90]]},
            "temporal": {"interval": [[None, None]]},
        },
        "links": [],
        "example:extension": {"preserved": True},
    }


def test_items_writer_embeds_a_collection_file(tmp_path, monkeypatch, items):
    collection = _collection("openaerialmap")
    collection_file = tmp_path / "collection.json"
    collection_file.write_text(json.dumps(collection))
    output = tmp_path / "items.parquet"
    _pipe_input(monkeypatch, items)

    assert cli.main(["items", "to-parquet", str(output), "--collection", str(collection_file)]) == 0

    assert extract_collections(output) == [collection]


def test_build_accepts_several_collection_files(tmp_path, monkeypatch, items):
    first = _collection("openaerialmap")
    second = _collection("other")
    first_file = tmp_path / "first.json"
    second_file = tmp_path / "second.json"
    first_file.write_text(json.dumps(first))
    second_file.write_text(json.dumps([second]))
    package = tmp_path / "package"
    _pipe_input(monkeypatch, items)

    assert (
        cli.main(
            [
                "build",
                str(package),
                "--collection",
                str(first_file),
                "--collection",
                str(second_file),
            ]
        )
        == 0
    )

    assert extract_collections(package / "items.parquet") == [first, second]


def test_collection_extraction_writes_the_same_documents_to_stdout_and_file(
    tmp_path, capsys, items
):
    collection = _collection("openaerialmap")
    source = tmp_path / "items.parquet"
    output = tmp_path / "collections.json"
    write_items_geoparquet(items, source, collections=[collection])

    assert cli.main(["collections", "extract", str(source)]) == 0
    stdout = capsys.readouterr().out
    assert json.loads(stdout) == [collection]

    assert cli.main(["collections", "extract", str(source), "--output", str(output)]) == 0
    assert json.loads(output.read_text()) == [collection]


def test_asset_lock_option_rejects_stdin_because_items_use_it(capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(["items", "enrich", "--asset-lock", "-"])

    assert error.value.code == 2
    assert "Arrow IPC stream" in capsys.readouterr().err


def test_json_items_can_enter_through_stdin(tmp_path, monkeypatch):
    source = tmp_path / "selection.json"
    write_localized_openaerialmap_item_collection_json(tmp_path, source)
    output = BytesIO()
    monkeypatch.setattr(cli.sys, "stdin", StringIO(source.read_text()))
    monkeypatch.setattr(cli.sys, "stdout", SimpleNamespace(buffer=output))

    assert cli.main(["items", "from-json"]) == 0

    output.seek(0)
    item = read_stream(output).to_pylist()[0]
    assert item["id"] == json.loads(source.read_text())["features"][0]["id"]
    assert item["assets"]["thumbnail"]["type"] == "image/png"


def test_lock_relocation_copies_the_selected_files(tmp_path, monkeypatch, items):
    assets = derive_asset_lock(items)
    destination = tmp_path / "copied"
    output = BytesIO()
    _pipe_input(monkeypatch, assets)
    monkeypatch.setattr(cli.sys, "stdout", SimpleNamespace(buffer=output))

    assert (
        cli.main(
            [
                "asset-lock",
                "relocate",
                "--store-type",
                "file",
                "--key",
                str(destination),
            ]
        )
        == 0
    )

    output.seek(0)
    copied = read_stream(output).to_pylist()
    for source, target in zip(assets.to_pylist(), copied, strict=True):
        assert Path(target["key"]).read_bytes() == Path(source["key"]).read_bytes()


def test_dry_run_writes_a_plan_without_creating_the_destination(tmp_path, monkeypatch, items):
    destination = tmp_path / "planned"
    output = BytesIO()
    _pipe_input(monkeypatch, derive_asset_lock(items))
    monkeypatch.setattr(cli.sys, "stdout", SimpleNamespace(buffer=output))

    assert (
        cli.main(
            [
                "asset-lock",
                "relocate",
                "--store-type",
                "file",
                "--key",
                str(destination),
                "--dry-run",
            ]
        )
        == 0
    )

    output.seek(0)
    assert all(
        Path(row["key"]).is_relative_to(destination) for row in read_stream(output).to_pylist()
    )
    assert not destination.exists()


def test_lock_validation_reports_a_wrong_size(tmp_path, monkeypatch, capsys, items):
    assets = derive_asset_lock(items, asset_keys={"thumbnail"})
    source = Path(assets.to_pylist()[0]["key"])
    source.write_bytes(b"changed")
    _pipe_input(monkeypatch, assets)

    assert cli.main(["asset-lock", "validate"]) == 1

    result = json.loads(capsys.readouterr().out)
    assert result["valid"] is False
    assert any("size mismatch" in error for error in result["errors"])


def test_build_keeps_a_supplied_lock_and_handover_notes(tmp_path, monkeypatch, items):
    assets = derive_asset_lock(items, checksum="calculate-always")
    lock_file = tmp_path / "assets.arrow"
    with lock_file.open("wb") as output:
        write_stream(assets, output)
    notes = tmp_path / "notes.txt"
    notes.write_text("Inputs for the field run.\n")
    package = tmp_path / "package"
    _pipe_input(monkeypatch, items)

    assert (
        cli.main(
            [
                "build",
                str(package),
                "--asset-lock",
                str(lock_file),
                "--includes",
                str(notes),
            ]
        )
        == 0
    )

    assert read_parquet(package / "assets.lock.parquet").equals(assets)
    assert (package / "notes.txt").read_text() == notes.read_text()


@pytest.mark.parametrize("format", ["json", "yaml", "markdown"])
def test_inspection_lists_package_files_and_collections(tmp_path, capsys, items, format):
    package = tmp_path / "package"
    build_package(items, package, collections=[_collection("openaerialmap")])

    assert cli.main(["inspect", str(package), "--format", format]) == 0

    report = capsys.readouterr().out
    assert "items.parquet" in report
    assert "assets.lock.parquet" in report
    assert "openaerialmap" in report
