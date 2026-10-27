# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from io import BytesIO

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from stacpkg.arrow_io import write_stream
from stacpkg.geoparquet import write_items_geoparquet, write_items_geoparquet_stream
from stacpkg.stac_json import read_stac_json
from openaerialmap_fixture import (
    write_localized_openaerialmap_item_collection_json,
)


def test_write_items_library_call_preserves_openaerialmap_fields_and_local_asset_metadata(
    tmp_path,
):
    source = write_localized_openaerialmap_item_collection_json(
        tmp_path,
        tmp_path / "openaerialmap-local.itemcollection.json",
    )
    output = tmp_path / "openaerialmap.items.parquet"

    write_items_geoparquet(read_stac_json(source), output)

    table = pq.read_table(output)
    schema = table.schema
    row = table.to_pylist()[0]
    metadata = row["assets"]["metadata"]
    thumbnail = row["assets"]["thumbnail"]

    assert table.num_rows == 1
    assert schema.metadata[b"stac_geoparquet:version"] == b"1.0.0"
    assert b"stac-geoparquet" in schema.metadata
    assert b"geo" in schema.metadata
    assert schema.field("geometry").metadata[b"ARROW:extension:name"] == b"geoarrow.wkb"
    assert pa.types.is_timestamp(schema.field("created").type)
    assert row["collection"] == "openaerialmap"
    assert row["oam:platform_type"]
    assert row["gsd"] > 0
    assert thumbnail["href"].startswith("file://")
    assert thumbnail["file:size"] > 0
    assert thumbnail["file:checksum"].startswith("1220")
    assert thumbnail["type"] == "image/png"
    assert metadata["type"] == "application/json"


@pytest.mark.parametrize("input_format", ["json", "parquet"])
def test_link_fields_survive_conversion_and_streamed_parquet_round_trip(tmp_path, input_format):
    source = write_localized_openaerialmap_item_collection_json(
        tmp_path, tmp_path / "selection.json", item_count=2
    )
    document = json.loads(source.read_text())
    items = document["features"]
    links = [
        [
            {
                "href": "https://publisher.test/search",
                "rel": "via",
                "method": "POST",
                "body": {"ids": [items[0]["id"]]},
                "headers": {"Accept": "application/geo+json"},
                "custom:trace": "xyz",
            }
        ],
        [{"href": "https://publisher.test/license", "rel": "license", "custom:rank": 2}],
    ]
    expected = [
        [{**links[0][0], "type": None, "title": None, "custom:rank": None}],
        [
            {
                **links[1][0],
                "type": None,
                "title": None,
                "method": None,
                "body": None,
                "headers": None,
                "custom:trace": None,
            }
        ],
    ]
    if input_format == "json":
        for item, item_links in zip(items, links):
            item["links"] = item_links
        source.write_text(json.dumps(document))
        table = read_stac_json(source)
    else:
        source = tmp_path / "external.parquet"
        pq.write_table(pa.table({"id": [item["id"] for item in items], "links": links}), source)
        table = pq.read_table(source)

    output = tmp_path / "converted.parquet"
    write_items_geoparquet(table, output)
    converted = pq.read_table(output)
    assert converted.column("links").to_pylist() == expected

    stream = BytesIO()
    with pa.ipc.new_stream(stream, converted.schema) as writer:
        writer.write_table(converted, max_chunksize=1)
    stream.seek(0)
    restored = tmp_path / "restored.parquet"
    write_items_geoparquet_stream(stream, restored)

    assert pq.read_table(restored).column("links").to_pylist() == expected


def test_point_z_and_bbox_survive_parquet_and_arrow_round_trip(tmp_path):
    source = write_localized_openaerialmap_item_collection_json(
        tmp_path, tmp_path / "selection.json"
    )
    document = json.loads(source.read_text())
    item = document["features"][0]
    item["geometry"] = {"type": "Point", "coordinates": [16, 48, 100]}
    item["bbox"] = [16, 48, 100, 16, 48, 100]
    source.write_text(json.dumps(document))
    output = tmp_path / "items.parquet"

    table = write_items_geoparquet(read_stac_json(source), output)
    stream = BytesIO()
    write_stream(table, stream)
    stream.seek(0)
    restored = tmp_path / "restored.parquet"
    write_items_geoparquet_stream(stream, restored)

    row = pq.read_table(restored).to_pylist()[0]
    assert row["geometry"] == bytes.fromhex(
        "01e9030000000000000000304000000000000048400000000000005940"
    )
    assert row["bbox"] == {"xmin": 16, "ymin": 48, "zmin": 100, "xmax": 16, "ymax": 48, "zmax": 100}
