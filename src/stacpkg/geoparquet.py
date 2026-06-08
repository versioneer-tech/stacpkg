# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import struct
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

import pyarrow as pa
import pyarrow.parquet as pq

from stacpkg.arrow_io import align_record_batch_to_schema, align_table_to_schema, write_parquet
from stacpkg.schemas import (
    ITEM_SCHEMA_VERSION,
    SCHEMA_KIND_KEY,
    SCHEMA_VERSION_KEY,
    SchemaKind,
)

GEOMETRY_METADATA = {b"ARROW:extension:name": b"geoarrow.wkb"}
STAC_GEOPARQUET_VERSION = "1.0.0"
LINK_TYPE = pa.list_(
    pa.struct(
        [
            pa.field("href", pa.string()),
            pa.field("rel", pa.string()),
            pa.field("type", pa.string()),
            pa.field("title", pa.string()),
        ]
    )
)
STAC_GEOPARQUET_TOP_LEVEL_FIELDS = {
    "type",
    "stac_version",
    "stac_extensions",
    "id",
    "geometry",
    "bbox",
    "links",
    "assets",
    "collection",
}


def _parse_datetime(value: object) -> object:
    if not isinstance(value, str):
        return value
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _bbox(value: object) -> dict[str, float] | None:
    if not isinstance(value, list):
        return None
    if len(value) == 4:
        xmin, ymin, xmax, ymax = value
        return {
            "xmin": float(xmin),
            "ymin": float(ymin),
            "xmax": float(xmax),
            "ymax": float(ymax),
        }
    if len(value) == 6:
        xmin, ymin, zmin, xmax, ymax, zmax = value
        return {
            "xmin": float(xmin),
            "ymin": float(ymin),
            "zmin": float(zmin),
            "xmax": float(xmax),
            "ymax": float(ymax),
            "zmax": float(zmax),
        }
    return None


def _point_wkb(coordinates: list[float]) -> bytes:
    return struct.pack("<BIdd", 1, 1, float(coordinates[0]), float(coordinates[1]))


def _line_string_body(coordinates: list[list[float]]) -> bytes:
    parts = [struct.pack("<I", len(coordinates))]
    for coordinate in coordinates:
        parts.append(struct.pack("<dd", float(coordinate[0]), float(coordinate[1])))
    return b"".join(parts)


def _line_string_wkb(coordinates: list[list[float]]) -> bytes:
    return struct.pack("<BI", 1, 2) + _line_string_body(coordinates)


def _polygon_body(rings: list[list[list[float]]]) -> bytes:
    parts = [struct.pack("<I", len(rings))]
    for ring in rings:
        parts.append(_line_string_body(ring))
    return b"".join(parts)


def _polygon_wkb(rings: list[list[list[float]]]) -> bytes:
    return struct.pack("<BI", 1, 3) + _polygon_body(rings)


def _multi_geometry_wkb(wkb_type: int, geometries: list[bytes]) -> bytes:
    return (
        struct.pack("<BI", 1, wkb_type) + struct.pack("<I", len(geometries)) + b"".join(geometries)
    )


def _geometry_wkb(geometry: dict[str, Any] | None) -> bytes | None:
    if not geometry:
        return None

    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")

    if geometry_type == "Point":
        return _point_wkb(coordinates)
    if geometry_type == "LineString":
        return _line_string_wkb(coordinates)
    if geometry_type == "Polygon":
        return _polygon_wkb(coordinates)
    if geometry_type == "MultiPoint":
        return _multi_geometry_wkb(4, [_point_wkb(point) for point in coordinates])
    if geometry_type == "MultiLineString":
        return _multi_geometry_wkb(5, [_line_string_wkb(line) for line in coordinates])
    if geometry_type == "MultiPolygon":
        return _multi_geometry_wkb(6, [_polygon_wkb(polygon) for polygon in coordinates])

    raise ValueError(f"unsupported GeoJSON geometry type: {geometry_type}")


def _row(item: dict[str, Any]) -> dict[str, Any]:
    properties = item.get("properties") or {}
    row = {
        "stac_version": item.get("stac_version"),
        "stac_extensions": item.get("stac_extensions") or [],
        "id": item["id"],
        "links": item.get("links") or [],
        "assets": item.get("assets") or {},
        "collection": item.get("collection"),
        "datetime": _parse_datetime(properties.get("datetime")),
        "bbox": _bbox(item.get("bbox")),
        "geometry": _geometry_wkb(item.get("geometry")),
    }

    for key, value in properties.items():
        if key in STAC_GEOPARQUET_TOP_LEVEL_FIELDS or key in row:
            continue
        row[key] = _parse_datetime(value)

    return row


def _json_object(value: object, *, default: object) -> object:
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


def _compact_item_row(row: dict[str, Any]) -> dict[str, Any]:
    properties = _json_object(row.get("properties_json"), default={})
    if not isinstance(properties, dict):
        properties = {}

    return {
        "type": "Feature",
        "stac_version": row.get("stac_version"),
        "stac_extensions": row.get("stac_extensions") or [],
        "id": row["id"],
        "collection": row.get("collection"),
        "geometry": _json_object(row.get("geometry_json"), default=None),
        "bbox": row.get("bbox"),
        "properties": properties,
        "links": _json_object(row.get("links_json"), default=[]),
        "assets": _json_object(row.get("assets_json"), default={}),
    }


def compact_items_to_stac_items(items: pa.Table) -> list[dict[str, Any]]:
    return [_compact_item_row(row) for row in items.to_pylist()]


def _metadata() -> dict[bytes, bytes]:
    stac_geoparquet = {"version": STAC_GEOPARQUET_VERSION}
    geo = {
        "version": "1.0.0",
        "primary_column": "geometry",
        "columns": {
            "geometry": {
                "encoding": "WKB",
                "geometry_types": [],
                "crs": None,
            }
        },
    }
    return {
        SCHEMA_KIND_KEY: SchemaKind.ITEMS.value.encode(),
        SCHEMA_VERSION_KEY: ITEM_SCHEMA_VERSION.encode(),
        b"stac_geoparquet:version": STAC_GEOPARQUET_VERSION.encode(),
        b"stac-geoparquet": json.dumps(
            stac_geoparquet,
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
        b"geo": json.dumps(geo, sort_keys=True, separators=(",", ":")).encode(),
    }


def _geoparquet_schema(schema: pa.Schema) -> pa.Schema:
    for name, field_type in {
        "type": pa.string(),
        "stac_version": pa.string(),
        "stac_extensions": pa.list_(pa.string()),
        "id": pa.string(),
        "links": LINK_TYPE,
        "collection": pa.string(),
    }.items():
        field_index = schema.get_field_index(name)
        if field_index != -1:
            field = schema.field(field_index)
            schema = schema.set(
                field_index,
                pa.field(
                    field.name,
                    field_type,
                    nullable=field.nullable,
                    metadata=field.metadata,
                ),
            )
    if "geometry" in schema.names:
        geometry_index = schema.get_field_index("geometry")
        geometry_field = pa.field(
            "geometry",
            pa.binary(),
            nullable=schema.field(geometry_index).nullable,
            metadata=GEOMETRY_METADATA,
        )
        schema = schema.set(geometry_index, geometry_field)
    metadata = dict(schema.metadata or {})
    metadata.update(_metadata())
    return schema.with_metadata(metadata)


def _with_geoparquet_metadata(table: pa.Table) -> pa.Table:
    return align_table_to_schema(table, _geoparquet_schema(table.schema))


def stac_items_to_geoparquet_table(items: list[dict[str, Any]]) -> pa.Table:
    table = pa.Table.from_pylist([_row(item) for item in items])
    return _with_geoparquet_metadata(table)


def items_table_to_geoparquet_table(items: pa.Table) -> pa.Table:
    if "assets_json" in items.schema.names:
        return stac_items_to_geoparquet_table(compact_items_to_stac_items(items))
    return _with_geoparquet_metadata(items)


def write_items_geoparquet(items: pa.Table, output_path: str | Path) -> pa.Table:
    table = items_table_to_geoparquet_table(items)
    write_parquet(table, output_path)
    return table


def write_items_geoparquet_stream(source: BinaryIO, output_path: str | Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pa.ipc.open_stream(source) as reader:
        if "assets_json" in reader.schema.names:
            raise ValueError(
                "items to-parquet expects a STAC GeoParquet-shaped item stream; "
                "use the CLI items from-json/from-ndjson/from-parquet adapters to create one"
            )

        output_schema = _geoparquet_schema(reader.schema)
        with pq.ParquetWriter(output_path, output_schema) as writer:
            for batch in reader:
                writer.write_batch(align_record_batch_to_schema(batch, output_schema))
