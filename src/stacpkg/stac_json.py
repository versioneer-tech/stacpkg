# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, BinaryIO, TextIO

import pyarrow as pa

from stacpkg.arrow_io import DEFAULT_STREAM_BATCH_SIZE, align_table_to_schema
from stacpkg.geoparquet import items_table_to_geoparquet_table
from stacpkg.schemas import items_schema


def _compact_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _document_items(document: dict[str, Any]) -> list[dict[str, Any]]:
    document_type = document.get("type")
    if document_type == "Feature" and "assets" in document:
        return [document]
    if document_type in {"FeatureCollection", "ItemCollection"}:
        return list(document.get("features") or [])
    raise ValueError("expected a STAC Item, STAC ItemCollection, or GeoJSON FeatureCollection")


def load_stac_items(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        document = json.load(handle)
    return _document_items(document)


def read_stac_json_document(
    document: dict[str, Any],
    *,
    source_href: str | None = None,
) -> pa.Table:
    return items_to_table(_document_items(document), source_href=source_href)


def items_to_table(items: list[dict[str, Any]], *, source_href: str | None = None) -> pa.Table:
    rows: list[dict[str, Any]] = []
    for item in items:
        properties = item.get("properties") or {}
        rows.append(
            {
                "id": item["id"],
                "collection": item.get("collection"),
                "geometry_json": _compact_json(item.get("geometry")),
                "bbox": item.get("bbox"),
                "datetime": properties.get("datetime")
                or properties.get("start_datetime")
                or properties.get("end_datetime"),
                "stac_version": item.get("stac_version"),
                "stac_extensions": item.get("stac_extensions") or [],
                "links_json": _compact_json(item.get("links") or []),
                "assets_json": _compact_json(item.get("assets") or {}),
                "properties_json": _compact_json(properties),
                "source_href": source_href,
            }
        )
    return pa.Table.from_pylist(rows, schema=items_schema())


def read_stac_json(path: str | Path) -> pa.Table:
    return items_to_table(load_stac_items(path), source_href=str(path))


def _ndjson_items(source: TextIO) -> Iterator[dict[str, Any]]:
    for line_number, line in enumerate(source, start=1):
        text = line.strip()
        if not text:
            continue
        try:
            document = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid STAC NDJSON on line {line_number}") from error
        yield from _document_items(document)


def iter_stac_ndjson_tables(
    source: TextIO,
    *,
    batch_size: int = DEFAULT_STREAM_BATCH_SIZE,
    source_href: str | None = None,
) -> Iterator[pa.Table]:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    batch: list[dict[str, Any]] = []
    for item in _ndjson_items(source):
        batch.append(item)
        if len(batch) >= batch_size:
            yield items_to_table(batch, source_href=source_href)
            batch = []
    if batch:
        yield items_to_table(batch, source_href=source_href)


def read_stac_ndjson(path: str | Path) -> pa.Table:
    path = Path(path)
    with path.open("r", encoding="utf-8") as source:
        return items_to_table(list(_ndjson_items(source)), source_href=str(path))


def read_stac_ndjson_document(
    source: TextIO,
    *,
    source_href: str | None = None,
) -> pa.Table:
    return items_to_table(list(_ndjson_items(source)), source_href=source_href)


def write_stac_ndjson_stream(
    source: TextIO,
    sink: BinaryIO,
    *,
    batch_size: int = DEFAULT_STREAM_BATCH_SIZE,
    transform: Callable[[pa.Table], pa.Table] | None = None,
    source_href: str | None = None,
) -> None:
    writer = None
    output_schema = None
    try:
        for compact in iter_stac_ndjson_tables(
            source,
            batch_size=batch_size,
            source_href=source_href,
        ):
            table = items_table_to_geoparquet_table(compact)
            if transform is not None:
                table = transform(table)
            if writer is None:
                output_schema = table.schema
                writer = pa.ipc.new_stream(sink, output_schema)
            assert output_schema is not None
            writer.write_table(align_table_to_schema(table, output_schema))

        if writer is None:
            empty = items_table_to_geoparquet_table(
                pa.Table.from_batches([], schema=items_schema())
            )
            if transform is not None:
                empty = transform(empty)
            with pa.ipc.new_stream(sink, empty.schema):
                pass
    finally:
        if writer is not None:
            writer.close()
