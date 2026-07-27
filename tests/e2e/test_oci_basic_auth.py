# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

import pytest

from stacpkg.dataset import build_package
from stacpkg.oci import pull_package, push_package
from tests.data.openaerialmap_data import openaerialmap_items
from tests.e2e.helpers import registry_target


def _file_checksums(package_dir: Path) -> dict[str, str]:
    return {
        path.relative_to(package_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(package_dir.rglob("*"))
        if path.is_file()
    }


@pytest.mark.e2e
def test_push_and_pull_with_basic_auth_registry(tmp_path: Path) -> None:
    if not os.environ.get("ORAS_USER") or not os.environ.get("ORAS_PASS"):
        pytest.skip("set ORAS_USER and ORAS_PASS to run the Basic-auth OCI registry e2e test")

    package = tmp_path / "source.pkg"
    pulled = tmp_path / "pulled.pkg"
    readme = tmp_path / "README.md"
    readme.write_text("Basic-auth OCI acceptance package\n", encoding="utf-8")
    build_package(
        openaerialmap_items(item_count=1),
        package,
        includes=[readme],
        probe_metadata=False,
    )
    expected_checksums = _file_checksums(package)
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

    assert expected_checksums.keys() == {
        "README.md",
        "assets.lock.parquet",
        "items.parquet",
    }
    assert _file_checksums(pulled) == expected_checksums
