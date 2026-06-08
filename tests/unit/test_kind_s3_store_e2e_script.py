# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "kind-s3-store-e2e.sh"


def test_kind_s3_store_runner_uses_distinct_minio_credentials() -> None:
    script = SCRIPT.read_text()
    old_registry_path = "tests/e2e" + "/kubernetes"

    assert "tests/setup/s3-stores.yaml" in script
    assert "tests/setup/registry.yaml" in script
    assert old_registry_path not in script
    assert "s3-store1-root" in script
    assert "s3-store2-root" in script
    assert "name: s3-store-root" not in script
    assert "minioadmin1" in script
    assert "minioadmin2" in script
    assert "STACPKG_S3_ACCESS_KEY_ID_STACPKG_E2E_S3STORE1" in script
    assert "STACPKG_S3_ACCESS_KEY_ID_STACPKG_E2E_S3STORE2" in script
