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
    assert "failed to list kind clusters" in script
    assert "ensure Docker is available for kind" in script
    assert "pytest_targets=(tests/e2e)" in script
    assert "pytest_targets=(tests/e2e tests/usecases)" not in script
    assert "uv run --group integration pytest" in script
    assert "STACPKG_E2E_RENDER_DOCS" in script
    assert "STACPKG_E2E_RENDER_NOTEBOOKS" not in script
    assert "uv run python scripts/generate_usecase_tests.py --no-tests" in script
    assert "uv run --group docs --group integration mkdocs build --strict" in script
