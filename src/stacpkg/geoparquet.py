# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import struct
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, TypeAlias

import pyarrow as pa
import pyarrow.parquet as pq

from stacpkg.arrow_io import align_record_batch_to_schema, align_table_to_schema, write_parquet
from stacpkg.sources import check_document_hrefs
from stacpkg.schemas import (
    ITEM_SCHEMA_VERSION,
    SCHEMA_KIND_KEY,
    SCHEMA_VERSION_KEY,
    SchemaKind,
)

GEOMETRY_METADATA = {b"ARROW:extension:name": b"geoarrow.wkb"}
STAC_GEOPARQUET_VERSION = "1.0.0"
STAC_GEOPARQUET_COLLECTIONS_VERSION = "1.1.0"
STAC_GEOPARQUET_METADATA_KEY = b"stac-geoparquet"
STAC_GEOPARQUET_VERSION_KEY = b"stac_geoparquet:version"
CollectionDocuments: TypeAlias = (
    Mapping[str, Mapping[str, Any]] | Iterable[Mapping[str, Any]] | None
)
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


def _coordinate_dimensions(coordinates: object, depth: int) -> int | None:
    if not isinstance(coordinates, list):
        raise ValueError("geometry coordinates must be arrays")
    if depth == 0:
        if len(coordinates) not in {2, 3}:
            raise ValueError("geometry positions must have two or three ordinates")
        return len(coordinates)
    dimensions = {
        size
        for child in coordinates
        if (size := _coordinate_dimensions(child, depth - 1)) is not None
    }
    if len(dimensions) > 1:
        raise ValueError("geometry positions must have consistent dimensions")
    return next(iter(dimensions), None)


def _wkb_header(wkb_type: int, dimensions: int) -> bytes:
    # ISO WKB adds 1000 to the geometry type for Z coordinates.
    return struct.pack("<BI", 1, wkb_type + (1000 if dimensions == 3 else 0))


def _point_wkb(coordinates: list[float], dimensions: int) -> bytes:
    return _wkb_header(1, dimensions) + struct.pack(
        "<" + "d" * dimensions, *(float(value) for value in coordinates)
    )


def _line_string_body(coordinates: list[list[float]], dimensions: int) -> bytes:
    parts = [struct.pack("<I", len(coordinates))]
    for coordinate in coordinates:
        parts.append(struct.pack("<" + "d" * dimensions, *(float(value) for value in coordinate)))
    return b"".join(parts)


def _line_string_wkb(coordinates: list[list[float]], dimensions: int) -> bytes:
    return _wkb_header(2, dimensions) + _line_string_body(coordinates, dimensions)


def _polygon_body(rings: list[list[list[float]]], dimensions: int) -> bytes:
    parts = [struct.pack("<I", len(rings))]
    for ring in rings:
        parts.append(_line_string_body(ring, dimensions))
    return b"".join(parts)


def _polygon_wkb(rings: list[list[list[float]]], dimensions: int) -> bytes:
    return _wkb_header(3, dimensions) + _polygon_body(rings, dimensions)


def _multi_geometry_wkb(wkb_type: int, geometries: list[bytes], dimensions: int) -> bytes:
    return (
        _wkb_header(wkb_type, dimensions)
        + struct.pack("<I", len(geometries))
        + b"".join(geometries)
    )


def _geometry_wkb(geometry: dict[str, Any] | None) -> bytes | None:
    if not geometry:
        return None

    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    depths = {
        "Point": 0,
        "LineString": 1,
        "Polygon": 2,
        "MultiPoint": 1,
        "MultiLineString": 2,
        "MultiPolygon": 3,
    }
    if geometry_type not in depths:
        raise ValueError(f"unsupported GeoJSON geometry type: {geometry_type}")
    dimensions = _coordinate_dimensions(coordinates, depths[geometry_type]) or 2

    if geometry_type == "Point":
        return _point_wkb(coordinates, dimensions)
    if geometry_type == "LineString":
        return _line_string_wkb(coordinates, dimensions)
    if geometry_type == "Polygon":
        return _polygon_wkb(coordinates, dimensions)
    if geometry_type == "MultiPoint":
        return _multi_geometry_wkb(
            4, [_point_wkb(point, dimensions) for point in coordinates], dimensions
        )
    if geometry_type == "MultiLineString":
        return _multi_geometry_wkb(
            5, [_line_string_wkb(line, dimensions) for line in coordinates], dimensions
        )
    return _multi_geometry_wkb(
        6, [_polygon_wkb(polygon, dimensions) for polygon in coordinates], dimensions
    )


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


def _compact_json(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    except (TypeError, ValueError) as error:
        raise ValueError(f"STAC Collection documents must be JSON serializable: {error}") from error


def _validate_collection_document(
    collection: Mapping[str, Any],
    *,
    context: str,
) -> tuple[str, dict[str, Any]]:
    if collection.get("type") != "Collection":
        raise ValueError(f"{context} type must be 'Collection'")
    collection_id = collection.get("id")
    if not isinstance(collection_id, str) or not collection_id.strip():
        raise ValueError(f"{context} must have a non-empty string id")
    document = dict(collection)
    check_document_hrefs(document)
    _compact_json(document)
    return collection_id, document


def _normalize_collections(collections: CollectionDocuments) -> dict[str, dict[str, Any]] | None:
    if collections is None:
        return None

    normalized: dict[str, dict[str, Any]] = {}
    if isinstance(collections, Mapping):
        entries: Iterable[tuple[object, object]] = collections.items()
        for key, value in entries:
            if not isinstance(value, Mapping):
                raise ValueError(f"STAC Collection mapping value for key {key!r} must be an object")
            collection_id, document = _validate_collection_document(
                value,
                context=f"STAC Collection mapped by {key!r}",
            )
            if key != collection_id:
                raise ValueError(
                    f"STAC Collection mapping key {key!r} does not match "
                    f"Collection id {collection_id!r}"
                )
            normalized[collection_id] = document
        return normalized or None

    if isinstance(collections, str | bytes):
        raise ValueError("STAC Collections must be a mapping or an iterable of objects")

    try:
        iterator = iter(collections)
    except TypeError as error:
        raise ValueError("STAC Collections must be a mapping or an iterable of objects") from error

    for index, value in enumerate(iterator):
        if not isinstance(value, Mapping):
            raise ValueError(f"STAC Collection at index {index} must be an object")
        collection_id, document = _validate_collection_document(
            value,
            context=f"STAC Collection at index {index}",
        )
        if collection_id in normalized:
            raise ValueError(f"duplicate STAC Collection id {collection_id!r}")
        normalized[collection_id] = document
    return normalized or None


def _item_collection_ids(items: pa.Table | pa.RecordBatch) -> set[str]:
    collection_index = items.schema.get_field_index("collection")
    if collection_index == -1:
        return set()
    return {value for value in items.column(collection_index).to_pylist() if isinstance(value, str)}


def _validate_item_collection_ids(
    items: pa.Table | pa.RecordBatch,
    collections: Mapping[str, Mapping[str, Any]] | None,
) -> None:
    if collections is None:
        return
    missing = sorted(_item_collection_ids(items).difference(collections))
    if missing:
        missing_ids = ", ".join(value or "<empty string>" for value in missing)
        raise ValueError(
            "missing supplied STAC Collection documents for Item collection IDs: " + missing_ids
        )


def _default_geo_metadata() -> bytes:
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
    return _compact_json(geo)


def _metadata(
    existing: Mapping[bytes, bytes] | None,
    *,
    collections: Mapping[str, Mapping[str, Any]] | None,
) -> dict[bytes, bytes]:
    metadata = dict(existing or {})
    metadata.update(
        {
            SCHEMA_KIND_KEY: SchemaKind.ITEMS.value.encode(),
            SCHEMA_VERSION_KEY: ITEM_SCHEMA_VERSION.encode(),
        }
    )

    if collections is not None:
        metadata[STAC_GEOPARQUET_METADATA_KEY] = _compact_json(
            {
                "version": STAC_GEOPARQUET_COLLECTIONS_VERSION,
                "collections": collections,
            }
        )
        metadata[STAC_GEOPARQUET_VERSION_KEY] = STAC_GEOPARQUET_COLLECTIONS_VERSION.encode()
    elif STAC_GEOPARQUET_METADATA_KEY not in metadata:
        metadata[STAC_GEOPARQUET_METADATA_KEY] = _compact_json({"version": STAC_GEOPARQUET_VERSION})
        metadata[STAC_GEOPARQUET_VERSION_KEY] = STAC_GEOPARQUET_VERSION.encode()
    else:
        try:
            payload = json.loads(metadata[STAC_GEOPARQUET_METADATA_KEY])
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("version"), str):
            _collections_from_metadata_payload(payload, source="Items metadata", required=False)
            metadata[STAC_GEOPARQUET_VERSION_KEY] = payload["version"].encode()

    metadata.setdefault(b"geo", _default_geo_metadata())
    return metadata


def _geoparquet_schema(
    schema: pa.Schema,
    *,
    collections: Mapping[str, Mapping[str, Any]] | None = None,
) -> pa.Schema:
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
            if name == "links" and (
                pa.types.is_list(field.type)
                or pa.types.is_large_list(field.type)
                or pa.types.is_fixed_size_list(field.type)
            ):
                if pa.types.is_struct(field.type.value_type):
                    core_names = {core.name for core in LINK_TYPE.value_type}
                    extra_fields = [
                        extra for extra in field.type.value_type if extra.name not in core_names
                    ]
                    field_type = pa.list_(pa.struct([*LINK_TYPE.value_type, *extra_fields]))
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
    return schema.with_metadata(_metadata(schema.metadata, collections=collections))


def _with_geoparquet_metadata(
    table: pa.Table,
    *,
    collections: Mapping[str, Mapping[str, Any]] | None = None,
) -> pa.Table:
    output = align_table_to_schema(
        table,
        _geoparquet_schema(table.schema, collections=collections),
    )
    _validate_item_collection_ids(output, collections)
    return output


def _items_table(items: list[dict[str, Any]]) -> pa.Table:
    rows = [_row(item) for item in items]
    names = dict.fromkeys(name for row in rows for name in row)
    return pa.Table.from_pydict({name: [row.get(name) for row in rows] for name in names})


def stac_items_to_geoparquet_table(items: list[dict[str, Any]]) -> pa.Table:
    table = _items_table(items)
    return _with_geoparquet_metadata(table)


def items_table_to_geoparquet_table(
    items: pa.Table,
    *,
    collections: CollectionDocuments = None,
) -> pa.Table:
    normalized_collections = _normalize_collections(collections)
    if "assets_json" in items.schema.names:
        table = _items_table(compact_items_to_stac_items(items))
        table = table.replace_schema_metadata(items.schema.metadata)
        return _with_geoparquet_metadata(table, collections=normalized_collections)
    return _with_geoparquet_metadata(items, collections=normalized_collections)


def write_items_geoparquet(
    items: pa.Table,
    output_path: str | Path,
    *,
    collections: CollectionDocuments = None,
) -> pa.Table:
    table = items_table_to_geoparquet_table(items, collections=collections)
    write_parquet(table, output_path)
    return table


def write_items_geoparquet_stream(
    source: BinaryIO,
    output_path: str | Path,
    *,
    collections: CollectionDocuments = None,
) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_collections = _normalize_collections(collections)
    with pa.ipc.open_stream(source) as reader:
        if "assets_json" in reader.schema.names:
            raise ValueError(
                "items to-parquet expects a STAC GeoParquet-shaped item stream; "
                "use the CLI items from-json/from-ndjson/from-parquet adapters to create one"
            )

        output_schema = _geoparquet_schema(
            reader.schema,
            collections=normalized_collections,
        )
        with pq.ParquetWriter(output_path, output_schema) as writer:
            for batch in reader:
                output_batch = align_record_batch_to_schema(batch, output_schema)
                _validate_item_collection_ids(output_batch, normalized_collections)
                writer.write_batch(output_batch)


def read_stac_geoparquet_metadata(input_path: str | Path) -> dict[str, Any]:
    metadata = pq.ParquetFile(input_path).schema_arrow.metadata
    if metadata is None or STAC_GEOPARQUET_METADATA_KEY not in metadata:
        raise ValueError(f"{input_path}: missing stac-geoparquet metadata")
    try:
        payload = json.loads(metadata[STAC_GEOPARQUET_METADATA_KEY])
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{input_path}: malformed stac-geoparquet metadata: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"{input_path}: malformed stac-geoparquet metadata: expected an object")
    return payload


def _collections_from_metadata_payload(
    payload: Mapping[str, Any],
    *,
    source: str | Path,
    required: bool,
) -> dict[str, dict[str, Any]]:
    collections = payload.get("collections")
    if "collections" in payload:
        if not isinstance(collections, Mapping):
            raise ValueError(
                f"{source}: malformed stac-geoparquet metadata: collections must be an object"
            )
        try:
            normalized = _normalize_collections(collections) or {}
        except ValueError as error:
            raise ValueError(f"{source}: malformed stac-geoparquet metadata: {error}") from error
        if normalized:
            return normalized

    collection = payload.get("collection")
    if collection is not None:
        if not isinstance(collection, Mapping):
            raise ValueError(
                f"{source}: malformed stac-geoparquet metadata: collection must be an object"
            )
        try:
            return _normalize_collections([collection]) or {}
        except ValueError as error:
            raise ValueError(f"{source}: malformed stac-geoparquet metadata: {error}") from error

    if required:
        raise ValueError(f"{source}: stac-geoparquet metadata contains no STAC Collection objects")
    return {}


def extract_collections(input_path: str | Path) -> list[dict[str, Any]]:
    payload = read_stac_geoparquet_metadata(input_path)
    collections = _collections_from_metadata_payload(
        payload,
        source=input_path,
        required=True,
    )
    return [collections[collection_id] for collection_id in sorted(collections)]
