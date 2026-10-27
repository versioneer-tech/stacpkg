# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
from types import SimpleNamespace

import pytest

from openaerialmap_fixture import localized_openaerialmap_items
from stacpkg import cli
from stacpkg.dataset import build_package
from stacpkg.oci import (
    ARTIFACT_TYPE,
    ASSET_MEDIA_TYPE,
    FILES_ZIP_MEDIA_TYPE,
    ITEMS_MEDIA_TYPE,
    TITLE_ANNOTATION,
    pull_package,
    push_package,
)
from stacpkg.sources import select_item_assets
from stacpkg.workflows import validate_package


@pytest.fixture
def registry(monkeypatch):
    state = SimpleNamespace(manifest={}, blobs={})

    class Registry:
        def __init__(self, **options):
            self.auth = SimpleNamespace(load_configs=lambda container: None)

        def get_container(self, target):
            return target

        def upload_blob(self, path, container, descriptor):
            state.blobs[descriptor["digest"]] = Path(path).read_bytes()

        def upload_manifest(self, manifest, container):
            state.manifest = manifest

        def _check_200_response(self, response):
            pass

        def get_manifest(self, container):
            return state.manifest

        def download_blob(self, container, digest, output):
            Path(output).write_bytes(state.blobs[digest])

    monkeypatch.setattr("stacpkg.oci.OrasClient", Registry)
    return state


def _package(tmp_path):
    items = select_item_assets(localized_openaerialmap_items(tmp_path), {"thumbnail"})
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "README.txt").write_text("Selected imagery for an offline run.\n")
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"source_snapshot":"xyz"}\n')
    package = tmp_path / "source.pkg"
    build_package(items, package, include_assets=True, includes=[notes, manifest])
    return package


def _files(directory):
    return {
        path.relative_to(directory): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


def test_oci_handover_preserves_the_complete_package(tmp_path, registry):
    package = _package(tmp_path)
    target = "registry.local/imagery:selection"
    delivered = tmp_path / "delivered.pkg"

    assert cli.main(["push", str(package), target]) == 0
    assert cli.main(["pull", target, "--output-dir", str(delivered)]) == 0

    assert _files(delivered) == _files(package)
    assert list(validate_package(delivered))[-1]["valid"] is True
    assert registry.manifest["artifactType"] == ARTIFACT_TYPE


@pytest.mark.parametrize(
    "kind",
    [
        pytest.param(ITEMS_MEDIA_TYPE, id="items"),
        pytest.param(ASSET_MEDIA_TYPE, id="asset-bytes"),
        pytest.param(FILES_ZIP_MEDIA_TYPE, id="context-archive"),
        pytest.param("size", id="wrong-size"),
    ],
)
def test_pull_rejects_changed_content_before_publishing_a_package(tmp_path, registry, kind):
    push_package(_package(tmp_path), "registry.local/imagery:selection")
    layer = next(
        layer
        for layer in registry.manifest["layers"]
        if kind == "size" or layer["mediaType"] == kind
    )
    if kind == "size":
        layer["size"] += 1
    else:
        contents = registry.blobs[layer["digest"]]
        registry.blobs[layer["digest"]] = b"X" + contents[1:]
    delivered = tmp_path / "delivered.pkg"
    with pytest.raises(RuntimeError, match="digest or size mismatch"):
        pull_package("registry.local/imagery:selection", delivered)
    assert not delivered.exists()


def test_pull_rejects_a_file_path_outside_the_package(tmp_path, registry):
    push_package(_package(tmp_path), "registry.local/imagery:selection")
    layer = next(
        layer for layer in registry.manifest["layers"] if layer["mediaType"] == ASSET_MEDIA_TYPE
    )
    layer["annotations"][TITLE_ANNOTATION] = "../../outside.png"
    delivered = tmp_path / "delivered.pkg"
    with pytest.raises(ValueError, match="unsafe package path"):
        pull_package("registry.local/imagery:selection", delivered)
    assert not delivered.exists()
    assert not (tmp_path / "outside.png").exists()
