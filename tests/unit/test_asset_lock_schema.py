# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from stacpkg.schemas import ASSET_LOCK_COLUMNS, ASSET_LOCK_FIELDS, ASSET_LOCK_SCHEMA_VERSION


def _asset_lock_reference_rows() -> list[dict[str, str]]:
    repo_root = Path(__file__).parents[2]
    reference = repo_root / "docs" / "reference-guides" / "asset-lock.md"
    lines = reference.read_text(encoding="utf-8").splitlines()
    header = "| Column | Arrow type | Required | Meaning |"
    start = lines.index(header) + 2

    rows: list[dict[str, str]] = []
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        rows.append(
            {
                "column": cells[0].strip("`"),
                "type": cells[1].strip("`"),
                "required": cells[2],
            }
        )
    return rows


def test_asset_lock_schema_uses_only_valid_fields() -> None:
    assert set(ASSET_LOCK_COLUMNS) == set(ASSET_LOCK_FIELDS)
    assert tuple(ASSET_LOCK_FIELDS) == ASSET_LOCK_COLUMNS


def test_asset_lock_reference_matches_schema_fields() -> None:
    rows = _asset_lock_reference_rows()

    assert [row["column"] for row in rows] == list(ASSET_LOCK_COLUMNS)
    assert [row["type"] for row in rows] == [
        str(ASSET_LOCK_FIELDS[name].type) for name in ASSET_LOCK_COLUMNS
    ]
    assert [row["required"] for row in rows] == [
        "No" if ASSET_LOCK_FIELDS[name].nullable else "Yes" for name in ASSET_LOCK_COLUMNS
    ]


def test_asset_lock_reference_matches_schema_version() -> None:
    repo_root = Path(__file__).parents[2]
    reference = repo_root / "docs" / "reference-guides" / "asset-lock.md"

    assert f"schema version is `{ASSET_LOCK_SCHEMA_VERSION}`" in reference.read_text(
        encoding="utf-8"
    )
