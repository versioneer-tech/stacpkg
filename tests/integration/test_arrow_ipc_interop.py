# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tests.data.openaerialmap_data import (
    OPENAERIALMAP,
    OPENAERIALMAP_ITEM_COUNT,
    OPENAERIALMAP_SELECTION_IDS,
)

pytestmark = [
    pytest.mark.integration,
]

REPO_ROOT = Path(__file__).resolve().parents[3]
INTEGRATION_TIMEOUT_SECONDS = int(os.environ.get("STACPKG_INTEGRATION_TIMEOUT", "120"))


def _require_executable(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        pytest.skip(f"{name!r} is required for integration tests")
    return path


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    pythonpath = [str(REPO_ROOT / "src"), str(REPO_ROOT)]
    if env.get("PYTHONPATH"):
        pythonpath.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)
    return env


def _pyarrow_table(value: object) -> pa.Table:
    if value is None:
        pytest.fail("rustac DuckdbClient.search_to_arrow returned no table")
    if isinstance(value, pa.Table):
        return value
    if isinstance(value, pa.RecordBatchReader):
        return value.read_all()

    to_pyarrow = getattr(value, "to_pyarrow", None)
    if callable(to_pyarrow):
        converted = to_pyarrow()
        if isinstance(converted, pa.Table):
            return converted
        value = converted

    try:
        return pa.table(value)
    except (pa.ArrowException, TypeError, ValueError) as error:
        pytest.fail(f"could not convert rustac Arrow result to pyarrow.Table: {error}")


def _ipc_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue().to_pybytes()


def test_gpio_inspect_reads_local_geoparquet_fixture() -> None:
    gpio = _require_executable("gpio")
    expected_items = pq.ParquetFile(OPENAERIALMAP).metadata.num_rows

    result = subprocess.run(
        [
            gpio,
            "inspect",
            str(OPENAERIALMAP),
            "--json",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_subprocess_env(),
        timeout=INTEGRATION_TIMEOUT_SECONDS,
        check=False,
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    metadata = json.loads(result.stdout)
    geometry_column = next(column for column in metadata["columns"] if column["name"] == "geometry")

    assert metadata["rows"] == expected_items
    assert metadata["geoparquet_version"] == "1.0.0"
    assert geometry_column["is_geometry"] is True


def test_rustac_py_duckdb_search_to_arrow_uses_explicit_ipc_handoff() -> None:
    """rustac-py exposes in-process Arrow tables, not CLI Arrow IPC stdout."""
    duckdb = pytest.importorskip(
        "duckdb",
        reason="DuckDB Python package is required for integration tests",
    )
    rustac = pytest.importorskip(
        "rustac",
        reason="rustac-py is required for this integration interop check",
    )
    rustac_duckdb_extensions = pytest.importorskip(
        "rustac_duckdb_extensions",
        reason="bundled DuckDB extensions are required for rustac-py integration tests",
    )
    DuckdbClient = getattr(rustac, "DuckdbClient", None)
    if DuckdbClient is None:
        pytest.skip("rustac-py must be installed with the arrow extra for DuckdbClient")

    try:
        rustac_table = DuckdbClient(
            extension_directory=rustac_duckdb_extensions.extension_directory(),
            install_extensions=False,
        ).search_to_arrow(
            str(OPENAERIALMAP),
            ids=list(OPENAERIALMAP_SELECTION_IDS),
        )
    except Exception as error:
        pytest.skip(f"rustac-py DuckdbClient.search_to_arrow is unavailable: {error}")

    table = _pyarrow_table(rustac_table)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "stacpkg",
            "asset-lock",
            "derive",
            "--asset-keys",
            "thumbnail",
        ],
        input=_ipc_bytes(table),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_subprocess_env(),
        timeout=INTEGRATION_TIMEOUT_SECONDS,
        check=False,
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    with pa.ipc.open_stream(pa.BufferReader(result.stdout)) as reader:
        asset_lock = reader.read_all()

    connection = duckdb.connect(database=":memory:")
    try:
        connection.register("asset_lock", asset_lock)
        rows = connection.execute(
            """
            SELECT asset_key, count(*) AS row_count
            FROM asset_lock
            GROUP BY asset_key
            ORDER BY asset_key
            """
        ).fetchall()
    finally:
        connection.close()

    assert dict(rows) == {
        "thumbnail": OPENAERIALMAP_ITEM_COUNT,
    }
