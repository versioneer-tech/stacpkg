# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from pathlib import Path

import pytest

from stacpkg.dataset import build_package
from stacpkg.oci import pull_package, push_package
from stacpkg.workflows import validate_package
from tests.unit.openaerialmap_fixture import localized_openaerialmap_items
from tests.e2e.helpers import registry_target


def _file_checksums(package_dir: Path) -> dict[str, str]:
    return {
        path.relative_to(package_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(package_dir.rglob("*"))
        if path.is_file()
    }


@pytest.mark.e2e
def test_push_and_pull_with_basic_auth_registry(tmp_path: Path, monkeypatch) -> None:
    if not os.environ.get("ORAS_USER") or not os.environ.get("ORAS_PASS"):
        pytest.skip("set ORAS_USER and ORAS_PASS to run the Basic-auth OCI registry e2e test")

    # Use test credentials without reading Docker logins or netrc files.
    monkeypatch.setattr("oras.utils.find_docker_config", lambda exists=True: None)
    monkeypatch.setenv("NETRC", str(tmp_path / "xyz.netrc"))

    package = tmp_path / "source.pkg"
    pulled = tmp_path / "pulled.pkg"
    readme = tmp_path / "README.md"
    readme.write_text("Basic-auth OCI acceptance package\n", encoding="utf-8")
    build_package(
        localized_openaerialmap_items(tmp_path, copy_asset_keys={"thumbnail", "metadata"}),
        package,
        includes=[readme],
        include_assets=True,
        include_metadata_assets=True,
    )
    expected_checksums = _file_checksums(package)
    shutil.rmtree(tmp_path / "openaerialmap-local-assets")
    target = registry_target("stacpkg/basic-auth", uuid.uuid4().hex)

    push_package(
        package,
        target,
        plain_http=True,
        auth_backend="basic",
    )
    pull_package(
        target,
        pulled,
        plain_http=True,
        auth_backend="basic",
    )

    assert _file_checksums(pulled) == expected_checksums
    assert list(validate_package(pulled))[-1]["valid"] is True
