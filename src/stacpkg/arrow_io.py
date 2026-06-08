# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, TextIO

import pyarrow as pa
import pyarrow.parquet as pq

DEFAULT_PRETTY_MAX_ROWS = 20
DEFAULT_PRETTY_MAX_CELL_WIDTH = 48
DEFAULT_PRETTY_WIDTH = 120
DEFAULT_STREAM_BATCH_SIZE = 64_000


def read_stream(source: BinaryIO) -> pa.Table:
    with pa.ipc.open_stream(source) as reader:
        return reader.read_all()


def read_stream_path(path: str | Path) -> pa.Table:
    with Path(path).open("rb") as source:
        return read_stream(source)


def write_stream(table: pa.Table, sink: BinaryIO) -> None:
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)


def align_table_to_schema(table: pa.Table, schema: pa.Schema) -> pa.Table:
    try:
        columns = []
        for field in schema:
            index = table.schema.get_field_index(field.name)
            if index == -1:
                columns.append(pa.nulls(table.num_rows, type=field.type))
                continue
            column = table.column(index)
            if not column.type.equals(field.type):
                column = column.cast(field.type)
            columns.append(column)
        return pa.Table.from_arrays(columns, schema=schema)
    except pa.ArrowException:
        return pa.Table.from_pylist(table.to_pylist(), schema=schema)


def align_record_batch_to_schema(batch: pa.RecordBatch, schema: pa.Schema) -> pa.RecordBatch:
    columns = []
    for field in schema:
        index = batch.schema.get_field_index(field.name)
        if index == -1:
            columns.append(pa.nulls(batch.num_rows, type=field.type))
            continue
        column = batch.column(index)
        if not column.type.equals(field.type):
            column = column.cast(field.type)
        columns.append(column)
    return pa.RecordBatch.from_arrays(columns, schema=schema)


def write_transformed_stream(
    source: BinaryIO,
    sink: BinaryIO,
    transform: Callable[[pa.Table], pa.Table],
) -> None:
    with pa.ipc.open_stream(source) as reader:
        writer = None
        output_schema = None
        try:
            for batch in reader:
                table = pa.Table.from_batches([batch], schema=reader.schema)
                transformed = transform(table)
                if writer is None:
                    output_schema = transformed.schema
                    writer = pa.ipc.new_stream(sink, output_schema)
                assert output_schema is not None
                writer.write_table(align_table_to_schema(transformed, output_schema))

            if writer is None:
                empty = transform(pa.Table.from_batches([], schema=reader.schema))
                with pa.ipc.new_stream(sink, empty.schema):
                    pass
        finally:
            if writer is not None:
                writer.close()


def write_parquet_stream(
    path: str | Path,
    sink: BinaryIO,
    *,
    batch_size: int = DEFAULT_STREAM_BATCH_SIZE,
    transform: Callable[[pa.Table], pa.Table] | None = None,
) -> None:
    parquet = pq.ParquetFile(path)
    if transform is None:
        with pa.ipc.new_stream(sink, parquet.schema_arrow) as writer:
            for batch in parquet.iter_batches(batch_size=batch_size):
                writer.write_batch(batch)
        return

    writer = None
    output_schema = None
    try:
        for batch in parquet.iter_batches(batch_size=batch_size):
            table = pa.Table.from_batches([batch], schema=parquet.schema_arrow)
            transformed = transform(table)
            if writer is None:
                output_schema = transformed.schema
                writer = pa.ipc.new_stream(sink, output_schema)
            assert output_schema is not None
            writer.write_table(align_table_to_schema(transformed, output_schema))

        if writer is None:
            empty = transform(pa.Table.from_batches([], schema=parquet.schema_arrow))
            with pa.ipc.new_stream(sink, empty.schema):
                pass
    finally:
        if writer is not None:
            writer.close()


def read_parquet_preview(
    path: str | Path,
    *,
    max_rows: int = DEFAULT_PRETTY_MAX_ROWS,
    batch_size: int = DEFAULT_STREAM_BATCH_SIZE,
    transform: Callable[[pa.Table], pa.Table] | None = None,
) -> tuple[pa.Table, int]:
    parquet = pq.ParquetFile(path)
    if transform is not None:
        batches = []
        output_schema = None
        total_rows = 0
        remaining = max(max_rows, 0)
        for batch in parquet.iter_batches(batch_size=max(batch_size, 1)):
            table = pa.Table.from_batches([batch], schema=parquet.schema_arrow)
            transformed = transform(table)
            if output_schema is None:
                output_schema = transformed.schema
            total_rows += transformed.num_rows
            if remaining:
                preview = transformed.slice(0, remaining)
                batches.extend(preview.to_batches())
                remaining -= preview.num_rows
        if output_schema is None:
            output_schema = transform(pa.Table.from_batches([], schema=parquet.schema_arrow)).schema
        return pa.Table.from_batches(batches, schema=output_schema), total_rows

    batches = []
    remaining = max(max_rows, 0)
    if remaining:
        for batch in parquet.iter_batches(batch_size=max(batch_size, 1)):
            if batch.num_rows > remaining:
                batch = batch.slice(0, remaining)
            batches.append(batch)
            remaining -= batch.num_rows
            if remaining == 0:
                break
    return pa.Table.from_batches(batches, schema=parquet.schema_arrow), parquet.metadata.num_rows


def write_parquet_terminal_table(
    path: str | Path,
    sink: TextIO,
    *,
    batch_size: int = DEFAULT_STREAM_BATCH_SIZE,
    max_width: int | None = None,
    transform: Callable[[pa.Table], pa.Table] | None = None,
) -> None:
    table, total_rows = read_parquet_preview(path, batch_size=batch_size, transform=transform)
    width = max_width or shutil.get_terminal_size((DEFAULT_PRETTY_WIDTH, 24)).columns
    sink.write(format_table(table, max_width=width, total_rows=total_rows))


def write_stream_to_parquet(source: BinaryIO, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pa.ipc.open_stream(source) as reader:
        with pq.ParquetWriter(path, reader.schema) as writer:
            for batch in reader:
                writer.write_batch(batch)


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dict | list | tuple):
        text = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    else:
        text = str(value)
    return " ".join(text.split())


def _clip_text(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    if width <= 3:
        return "." * width
    return f"{text[: width - 3]}..."


def _column_widths(
    columns: list[str],
    rows: list[list[str]],
    *,
    max_width: int,
    max_cell_width: int,
) -> list[int]:
    widths = [
        min(
            max([len(column), *(len(row[index]) for row in rows)]),
            max_cell_width,
        )
        for index, column in enumerate(columns)
    ]
    available = max(max_width, 20) - (3 * max(len(columns) - 1, 0))
    minimums = [min(width, 4) for width in widths]
    while sum(widths) > available and any(
        width > minimum for width, minimum in zip(widths, minimums)
    ):
        widest = max(range(len(widths)), key=lambda index: widths[index] - minimums[index])
        widths[widest] -= 1
    return widths


def _format_row(values: list[str], widths: list[int]) -> str:
    return " | ".join(_clip_text(value, width).ljust(width) for value, width in zip(values, widths))


def format_table(
    table: pa.Table,
    *,
    max_rows: int = DEFAULT_PRETTY_MAX_ROWS,
    max_width: int = DEFAULT_PRETTY_WIDTH,
    max_cell_width: int = DEFAULT_PRETTY_MAX_CELL_WIDTH,
    total_rows: int | None = None,
) -> str:
    columns = table.schema.names
    total_row_count = table.num_rows if total_rows is None else total_rows
    if not columns:
        return f"{total_row_count} rows x 0 columns\n"

    row_count = min(max(max_rows, 0), table.num_rows)
    rows = [
        [_cell_text(row.get(column)) for column in columns]
        for row in table.slice(0, row_count).to_pylist()
    ]
    widths = _column_widths(
        columns,
        rows,
        max_width=max_width,
        max_cell_width=max(max_cell_width, 4),
    )
    lines = [
        _format_row(columns, widths),
        "-+-".join("-" * width for width in widths),
    ]
    lines.extend(_format_row(row, widths) for row in rows)

    footer = f"{total_row_count} rows x {table.num_columns} columns"
    if row_count < total_row_count:
        footer = f"{footer} (showing first {row_count})"
    lines.append(footer)
    return f"{'\n'.join(lines)}\n"


def write_terminal_table(table: pa.Table, sink: TextIO, *, max_width: int | None = None) -> None:
    width = max_width or shutil.get_terminal_size((DEFAULT_PRETTY_WIDTH, 24)).columns
    sink.write(format_table(table, max_width=width))


def read_parquet(path: str | Path) -> pa.Table:
    return pq.read_table(path)


def write_parquet(table: pa.Table, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)
