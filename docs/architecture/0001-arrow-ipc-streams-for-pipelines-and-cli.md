# ADR-001: Use Arrow IPC Streams for Table Pipelines and CLI

| Status | Date | Implementation |
| --- | --- | --- |
| Accepted | 2026-05-14 | Implemented by current CLI and library entrypoints in `0.1.0`. |

## Context

`stacpkg` works with two table row types:

- STAC Item rows
- asset-lock rows

The same work should run through shell commands and direct Python calls. Shell
commands need to compose with pipes. Python calls keep tests and larger
workflows from starting a new process for every step.

Pipeline steps need one moving table format that keeps column names, column
types, and schema metadata. The format should avoid turning Arrow or Parquet
data back into JSON just to pass it to the next step.

Apache Arrow IPC streams fit this role. An Arrow IPC stream carries one schema
and then a sequence of `RecordBatch` values. The schema tells downstream steps
which columns and types to expect.

`items.parquet` follows the STAC GeoParquet model: one row represents one STAC
Item. STAC GeoParquet defines core fields such as `id`, `geometry`, `bbox`,
`links`, `assets`, `stac_extensions`, and `collection`. STAC property and
extension columns vary by dataset, so each items stream must carry the concrete
STAC GeoParquet-shaped Arrow schema for that dataset.

The asset-lock row structure is defined separately in
[ADR-003](0003-asset-lock-row-structure.md).

`stacpkg` should stay focused on packaging, checking, relocating, and handing
over STAC assets. Large catalog search and spatial query work should stay in
tools built for that job.

## Decision

Use Arrow IPC streams between pipeline steps for both row types:

```text
STAC items Arrow IPC stream
asset-lock Arrow IPC stream
```

Use Parquet for saved checkpoints and package tables. `from-parquet` and
`to-parquet` are the explicit adapters between saved Parquet files and moving
Arrow IPC streams:

```text
source.items.parquet        -> items from-parquet      -> items Arrow IPC stream
items Arrow IPC stream      -> items to-parquet        -> output.items.parquet

source.assets.lock.parquet  -> asset-lock from-parquet -> asset-lock Arrow IPC stream
asset-lock Arrow IPC stream -> asset-lock to-parquet   -> output.assets.lock.parquet
```

For items streams, the Arrow IPC schema is the concrete STAC GeoParquet-shaped
schema for that dataset. `stacpkg` normalizes the STAC GeoParquet core fields
and metadata, and keeps the dataset-specific property, extension, and asset
columns carried by the stream. The Parquet writer does not scan the whole stream
to discover extra columns.

The CLI is a thin layer over library functions, not a second implementation.
Command behavior should match library entrypoints such as `build_package`,
`derive_asset_lock`, `project_item_assets`, `copy_assets`, `push_package`, and
`pull_package`.

Table transforms read Arrow IPC streams from standard input and write Arrow IPC
streams to standard output when stdout is piped or redirected. When stdout is a
terminal, those commands show a compact text preview instead of binary IPC
bytes.

The stream rules are:

- `from-parquet` iterates PyArrow `RecordBatch` values.
- `to-parquet` writes incoming IPC `RecordBatch` values directly.
- Pipeline code should not turn the full input into one `pa.Table` unless an
  operation needs a full table.
- Terminal output may show small previews, but piped output remains Arrow IPC.
- Items transforms process input batch by batch and use the incoming stream
  schema as the items schema for that dataset.
- Transforms that add or remove known nested asset fields update the output
  schema before writing output batches.

`stacpkg` provides only small built-in filters: collection, item id, provider,
and asset-key selection. It does not implement large catalog features such as
spatial filtering, partitioning, remote GeoParquet scans, or query planning.
For those tasks, use tools such as `geoparquet-io`/`gpio`, DuckDB, or similar
systems before or after `stacpkg`.

## Alternatives Considered

- **File-only CLI:** Simple command behavior, but it forces unnecessary
  temporary files. Arrow IPC streams fit table pipelines better.
- **GeoParquet files between pipeline steps:** Easy to exchange, but every step
  would have to write and read a file. Keep Parquet for checkpoints and
  packages.
- **CLI as the primary implementation:** Tests would mirror user commands, but
  the project would duplicate logic between shell commands and Python calls.
- **JSON Lines as the stream format:** Easy to read, but it loses the Arrow
  schema and repeats encoding work. Keep JSONL for diagnostics only.
- **Universal STAC GeoParquet schema:** STAC extensions and custom properties
  are open-ended. Each dataset needs its own concrete items schema.
- **Full-stream schema discovery before writing Parquet:** Later batches could
  introduce columns, but `items to-parquet` would need to save or load the whole
  stream before writing Parquet.
- **Large-catalog query engine in `stacpkg`:** Convenient in one command, but it
  would duplicate mature table and geospatial tools.

## Consequences

CLI workflows can connect commands with pipes. Larger workflows can call the
same logic directly from Python.

File writes happen only at explicit adapter commands, which keeps command
behavior predictable.

Pipeline steps should keep batches as batches. Turning a stream into a full
table should happen only in operations that need it, such as package build,
asset-lock derivation, validation, copy planning, and copy execution.

The project needs to preserve Arrow schema compatibility across stream and
Parquet file steps. Pretty terminal output is display-only. Downstream commands
should receive Arrow IPC whenever stdout is not a TTY.

Future items transforms that add columns must update the output Arrow schema
before writing the first output batch. Transforms must not rely on later rows to
discover new columns.
