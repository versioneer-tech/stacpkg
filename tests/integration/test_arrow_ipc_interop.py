# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

import json
import copy
import shutil
import subprocess
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from stacpkg.assets import derive_asset_lock
from stacpkg.geoparquet import write_items_geoparquet
from stacpkg.stac_json import read_stac_json
from tests.unit.openaerialmap_fixture import write_localized_openaerialmap_item_collection_json
from tests.data.openaerialmap_data import (
    OPENAERIALMAP,
    OPENAERIALMAP_SELECTION_IDS,
    openaerialmap_items,
)

pytestmark = pytest.mark.integration


def test_gpio_reads_stacpkg_output(tmp_path):
    gpio = shutil.which("gpio")
    assert gpio is not None, "install the integration dependency group"
    source = tmp_path / "items.parquet"
    items = openaerialmap_items()
    write_items_geoparquet(items, source)

    result = subprocess.run(
        [gpio, "inspect", str(source), "--json"],
        capture_output=True,
        env={"PATH": str(Path(gpio).parent)},
        timeout=120,
        check=True,
    )
    report = json.loads(result.stdout)

    assert report["rows"] == items.num_rows
    geometry = next(column for column in report["columns"] if column["name"] == "geometry")
    assert geometry["is_geometry"] is True


def test_rustac_selection_can_be_locked_and_queried_with_duckdb():
    import duckdb
    import rustac
    import rustac_duckdb_extensions

    selected = rustac.DuckdbClient(
        extension_directory=rustac_duckdb_extensions.extension_directory(),
        install_extensions=False,
    ).search_to_arrow(str(OPENAERIALMAP), ids=list(OPENAERIALMAP_SELECTION_IDS))
    items = pa.table(selected)
    lock = derive_asset_lock(items, asset_keys={"thumbnail"}, probe_metadata=False)

    connection = duckdb.connect()
    try:
        connection.register("assets", lock)
        rows = connection.execute(
            "SELECT item_id, asset_key FROM assets ORDER BY item_id"
        ).fetchall()
    finally:
        connection.close()

    assert rows == [(item_id, "thumbnail") for item_id in sorted(OPENAERIALMAP_SELECTION_IDS)]


def test_duckdb_reads_every_geometry_coordinate_after_json_to_parquet(tmp_path):
    import duckdb
    import rustac_duckdb_extensions

    source = write_localized_openaerialmap_item_collection_json(
        tmp_path, tmp_path / "selection.json"
    )
    original = json.loads(source.read_text())["features"][0]
    point = [16, 48, 100]
    line = [[16, 48, 100], [17, 49, 200]]
    ring = [[16, 48, 100], [17, 48, 110], [17, 49, 120], [16, 48, 100]]
    geometries = {
        "Point": point,
        "LineString": line,
        "Polygon": [ring],
        "MultiPoint": line,
        "MultiLineString": [line, line[::-1]],
        "MultiPolygon": [[ring], [ring[::-1]]],
    }

    def coordinates_for_dimensions(coordinates, dimensions):
        if isinstance(coordinates[0], list):
            return [coordinates_for_dimensions(child, dimensions) for child in coordinates]
        return coordinates[:dimensions]

    items, expected = [], {}
    for kind, coordinates in geometries.items():
        for dimensions in (2, 3):
            item = copy.deepcopy(original)
            item["id"] = f"{original['id']}-{kind}-{dimensions}"
            item["geometry"] = {
                "type": kind,
                "coordinates": coordinates_for_dimensions(coordinates, dimensions),
            }
            item["bbox"] = [16, 48, 17, 49] if dimensions == 2 else [16, 48, 100, 17, 49, 200]
            items.append(item)
            expected[item["id"]] = item["geometry"]
    source.write_text(json.dumps({"type": "FeatureCollection", "features": items}))
    output = tmp_path / "items.parquet"
    write_items_geoparquet(read_stac_json(source), output)
    saved = pq.read_table(output)
    connection = duckdb.connect(
        config={
            "extension_directory": rustac_duckdb_extensions.extension_directory(),
            "autoinstall_known_extensions": "false",
            "autoload_known_extensions": "false",
        }
    )
    try:
        connection.execute("LOAD spatial")
        connection.register("saved", pa.table({"id": saved["id"], "wkb": saved["geometry"]}))
        rows = connection.execute(
            "SELECT id, ST_AsGeoJSON(ST_GeomFromWKB(wkb)) FROM saved"
        ).fetchall()
    finally:
        connection.close()

    assert {item_id: json.loads(geometry) for item_id, geometry in rows} == expected
