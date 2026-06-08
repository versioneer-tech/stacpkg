# CLI Reference

The CLI has package lifecycle commands and two table namespaces:

- `items` for STAC items tables.
- `asset-lock` for asset-lock tables.

Table commands compose through Arrow IPC streams on stdin and stdout. Parquet
files enter a pipeline through `from-parquet` commands and leave a pipeline
through `to-parquet` commands. STAC JSON ItemCollection documents enter through
`items from-json`; newline-delimited STAC Items enter through `items
from-ndjson`. `from-parquet` adapters stream Parquet rows as IPC batches, and
`items from-ndjson` writes IPC batches as NDJSON lines are read. `items
to-parquet` and `asset-lock to-parquet` write incoming IPC batches directly to
Parquet when their input comes from a piped Arrow stream. These adapter paths do
not load whole Parquet files or whole IPC streams as Arrow tables. Items
transforms preserve the concrete Arrow stream schema and process piped input
batch-by-batch. Interactive terminal output remains display-oriented and renders
a bounded preview.

Option-based asset-lock inputs use the same boundary: `--asset-lock` is a path
to an Apache Arrow IPC stream. The path may name a regular file, FIFO, or shell
process-substitution path. It does not read `-` from stdin, because stdin is
reserved for the primary items stream on commands such as `build`,
`items add-alternate`, and `items enrich`. When starting from an asset-lock
Parquet checkpoint, adapt it explicitly with `stacpkg asset-lock from-parquet`.

In the current version, items Parquet files are written as STAC GeoParquet-style
tables. The STAC GeoParquet core fields and metadata are normalized by the items
writer. Concrete property, extension, and asset-struct columns come from the
Arrow stream schema; transforms preserve existing columns and only add or remove
the nested asset fields they are responsible for. Asset-lock Parquet files use
the schema described in the [Asset Lock reference](asset-lock.md).

## Command Overview

| CLI command | Arrow stream input | Arrow stream output | Description |
| --- | --- | --- | --- |
| `build` | Items table | No | Build a package directory from items. |
| `inspect` | No | No | Inspect package contents as YAML, JSON, or Markdown. |
| `push` | No | No | Push package artifacts to an OCI registry. |
| `pull` | No | No | Pull package artifacts from an OCI registry. |
| `items from-json` | No | Items table | Convert STAC JSON into items Arrow streams. |
| `items from-ndjson` | No | Items table | Convert STAC NDJSON into items Arrow streams. |
| `items from-parquet` | No | Items table | Read STAC GeoParquet as Arrow IPC. |
| `items to-parquet` | Items table | No | Write items streams as STAC GeoParquet. |
| `items promote-alternate` | Items table | Items table | Promote alternate asset hrefs in items streams. |
| `items remove-alternate` | Items table | Items table | Remove alternate asset hrefs in items streams. |
| `items add-alternate` | Items table | Items table | Add alternate asset hrefs in items streams. |
| `items enrich` | Items table | Items table | Write asset lock facts into items. |
| `asset-lock derive` | Items table | Asset-lock table | Derive asset lock rows from items streams. |
| `asset-lock from-parquet` | No | Asset-lock table | Read asset-lock Parquet as Arrow IPC. |
| `asset-lock to-parquet` | Asset-lock table | No | Write asset-lock streams as Parquet. |
| `asset-lock validate` | Asset-lock table | No | Validate current assets against locked facts. |
| `asset-lock relocate` | Asset-lock table | Asset-lock table | Plan or relocate asset bytes into lock locations. |

Each command section starts with the same short description used by CLI help,
then lists arguments and options, then gives usage notes and examples.

## Package Commands

### `stacpkg build OUTPUT_DIR`

**Description:** Build a package directory from items.

| Argument or option | Required | Default | Description |
| --- | --- | --- | --- |
| `OUTPUT_DIR` | Yes | - | Package directory to create. |
| `--asset-lock PATH` | No | Derived from input items | Asset-lock Arrow IPC stream path. |
| `--includes PATH` | No | None | File or directory to include; repeat for multiple paths. |
| `--include-assets` | No | `false` | Relocate referenced asset bytes into the package directory. |
| `--include-metadata-assets` | No | `false` | Include assets whose asset key is `metadata`. |
| `--item-ids ITEM_ID` | No | All item ids | Item id to package; repeat for multiple item ids. |
| `--providers PROVIDER` | No | All providers | Provider name to package; repeat for multiple provider names. |
| `--probe-metadata`, `--no-probe-metadata` | No | `true` | Query referenced objects for current metadata when deriving package rows. |

Build a package directory from an items stream. By default, `build` creates
`items.parquet` and `assets.lock.parquet`. When `--asset-lock`
is omitted, the command derives `assets.lock.parquet` from the selected items.
The derived lock queries current object metadata facts by default and skips
`metadata` assets unless `--include-metadata-assets` is set. Use
`--no-probe-metadata` for a fast or offline derivation that only reuses facts
already present in the STAC item metadata.

```bash
stacpkg items from-parquet source.items.parquet \
  | stacpkg build stacpkg.pkg/
```

Use `--providers` or `--item-ids` to package only a selected subset of the input
items table. Both options may be repeated.

```bash
stacpkg items from-parquet openaerialmap.items.parquet \
  | stacpkg build webodm.pkg/ --providers WebODM
```

Use an existing asset lock when it should not be recreated:

```bash
stacpkg items from-parquet source.items.parquet \
  | stacpkg build stacpkg.pkg/ \
      --asset-lock <(stacpkg asset-lock from-parquet source.assets.lock.parquet)
```

Include referenced asset bytes in the package directory:

```bash
stacpkg items from-parquet source.items.parquet \
  | stacpkg build self-contained.pkg/ --include-assets
```

The package asset lock is mandatory. When `--asset-lock` is omitted, `build`
derives the listing from the selected items.

---

### `stacpkg inspect PACKAGE`

**Description:** Inspect package contents as YAML, JSON, or Markdown.

| Argument or option | Required | Default | Description |
| --- | --- | --- | --- |
| `PACKAGE` | Yes | - | Package directory to inspect. |
| `--format {yaml,json,markdown,md}` | No | `yaml` | Summary output format. |

Write a package summary to stdout. YAML is the default for terminal-friendly
inspection; JSON is useful for tools; Markdown is useful for reports.

```bash
stacpkg inspect stacpkg.pkg/
stacpkg inspect stacpkg.pkg/ --format json > inspect.json
stacpkg inspect stacpkg.pkg/ --format markdown > inspect.md
```

---

### `stacpkg push PACKAGE TARGET`

**Description:** Push package artifacts to an OCI registry.

| Argument or option | Required | Default | Description |
| --- | --- | --- | --- |
| `PACKAGE` | Yes | - | Package directory to push. |
| `TARGET` | Yes | - | OCI registry target reference. |
| `--plain-http` | No | `false` | Use plain HTTP for the registry. |
| `--insecure` | No | `false` | Disable TLS certificate checks. |

Push a package directory as an OCI artifact with typed layers for the required
tables, optional file ZIPs, and materialized asset bytes.

```bash
stacpkg push stacpkg.pkg/ ghcr.io/example/stacpkg/openaerialmap-austria:v1
```

For a local HTTP registry, use `--plain-http --insecure`.

---

### `stacpkg pull SOURCE --output-dir OUTPUT_DIR`

**Description:** Pull package artifacts from an OCI registry.

| Argument or option | Required | Default | Description |
| --- | --- | --- | --- |
| `SOURCE` | Yes | - | OCI registry source reference. |
| `--output-dir PATH` | Yes | - | Package directory to write. |
| `--plain-http` | No | `false` | Use plain HTTP for the registry. |
| `--insecure` | No | `false` | Disable TLS certificate checks. |

Pull a package artifact from an OCI registry and reconstruct the package
directory from its typed layers.

```bash
stacpkg pull ghcr.io/example/stacpkg/openaerialmap-austria:v1 --output-dir stacpkg.pkg/
```

For a local HTTP registry, use `--plain-http --insecure`.

## Item Commands

### `stacpkg items from-json [INPUT_FILE]`

**Description:** Convert STAC JSON into items Arrow streams.

| Argument or option | Required | Default | Description |
| --- | --- | --- | --- |
| `INPUT_FILE` | No | `stdin` | STAC JSON path; omit for stdin. |
| `--collections COLLECTION` | No | All collections | Collection id to keep; repeat for multiple collection ids. |
| `--providers PROVIDER` | No | All providers | Provider name to keep; repeat for multiple provider names. |
| `--item-ids ITEM_ID` | No | All item ids | Item id to keep; repeat for multiple item ids. |

Convert a STAC Item or ItemCollection JSON document into a STAC
GeoParquet-shaped items Arrow IPC stream.

```bash
stacpkg items from-json selection.itemcollection.json
```

Omit the path to read JSON from stdin. Use `--collections`, `--providers`, or
`--item-ids` to start the stream from only matching items. Repeat an option to
keep any matching value for that field.

---

### `stacpkg items from-ndjson [INPUT_FILE]`

**Description:** Convert STAC NDJSON into items Arrow streams.

| Argument or option | Required | Default | Description |
| --- | --- | --- | --- |
| `INPUT_FILE` | No | `stdin` | STAC NDJSON path; omit for stdin. |
| `--batch-size ROWS` | No | `64000` | Maximum rows per IPC batch while reading STAC items NDJSON. |
| `--collections COLLECTION` | No | All collections | Collection id to keep; repeat for multiple collection ids. |
| `--providers PROVIDER` | No | All providers | Provider name to keep; repeat for multiple provider names. |
| `--item-ids ITEM_ID` | No | All item ids | Item id to keep; repeat for multiple item ids. |

Convert newline-delimited STAC Item JSON into a STAC GeoParquet-shaped items
Arrow IPC stream. Blank lines are ignored. The command opens the Arrow IPC
stream as soon as the first non-empty batch is available, so it can bridge tools
that emit one STAC Item JSON object per line.

```bash
stacpkg items from-ndjson selection.ndjson
```

For example, pipe `rustac` NDJSON search output directly into package creation:

```bash
rustac -o ndjson search https://api.imagery.hotosm.org/stac \
  --collections openaerialmap \
  --bbox 5,45,25,55 \
  --datetime 2025-01-01T00:00:00Z/2025-12-31T23:59:59Z \
  - \
  | stacpkg items from-ndjson \
  | stacpkg build openaerialmap.pkg/
```

Omit the path to read NDJSON from stdin. The command streams item batches rather
than loading the whole NDJSON document. Use `--batch-size` to tune the maximum
rows per IPC batch. The first emitted batch fixes the Arrow stream schema; later
batches are aligned to that schema.

Use `--collections`, `--providers`, or `--item-ids` to filter rows while reading
batches. Repeat an option to keep any matching value for that field.

---

### `stacpkg items from-parquet INPUT_FILE`

**Description:** Read STAC GeoParquet as Arrow IPC.

| Argument or option | Required | Default | Description |
| --- | --- | --- | --- |
| `INPUT_FILE` | Yes | - | STAC GeoParquet input path. |
| `--batch-size ROWS` | No | `64000` | Maximum rows per IPC batch while reading Parquet. |
| `--collections COLLECTION` | No | All collections | Collection id to keep; repeat for multiple collection ids. |
| `--providers PROVIDER` | No | All providers | Provider name to keep; repeat for multiple provider names. |
| `--item-ids ITEM_ID` | No | All item ids | Item id to keep; repeat for multiple item ids. |

Read a STAC GeoParquet items file and write an items Arrow IPC stream.

```bash
stacpkg items from-parquet source.items.parquet
```

The command iterates Parquet `RecordBatch` values rather than loading the whole
file. Use `--batch-size` to tune the maximum rows per IPC batch while reading
the Parquet file.

Use `--collections`, `--providers`, or `--item-ids` to filter rows while reading
batches. Repeat an option to keep any matching value for that field. Provider
matching checks structured `providers[].name` values and common provider
properties such as `provider` and `oam:producer_name`.

---

### `stacpkg items to-parquet OUTPUT_FILE`

**Description:** Write items streams as STAC GeoParquet.

| Argument or option | Required | Default | Description |
| --- | --- | --- | --- |
| `OUTPUT_FILE` | Yes | - | STAC GeoParquet output path. |

Read a STAC GeoParquet-shaped items Arrow IPC stream from stdin and write a STAC
GeoParquet-style items table. The concrete property, extension, and asset columns
are taken from the incoming Arrow schema, so the command can write Parquet
batches without first loading the full stream as a table.

```bash
stacpkg items from-json selection.itemcollection.json \
  | stacpkg items to-parquet selection.items.parquet
```

---

### `stacpkg items promote-alternate --alternate-key KEY`

**Description:** Promote alternate asset hrefs in items streams.

| Argument or option | Required | Default | Description |
| --- | --- | --- | --- |
| `--alternate-key KEY`, `--key KEY` | Yes | - | Alternate asset map key to promote. |
| `--mode {replace,switch}` | No | `replace` | Promotion mode to apply. |
| `--switched-alternate-name NAME` | No | `original` | `alternate:name` to use for the demoted primary href in switch mode. |
| `--drop-alternates` | No | `false` | Remove the alternate asset map after replace promotion. |

Promote `asset.alternate[KEY].href` to the primary `asset.href` and write the
resulting items stream.

```bash
stacpkg items from-parquet source.items.parquet \
  | stacpkg items promote-alternate --alternate-key s3 --mode replace \
  | stacpkg asset-lock derive
```

`--mode replace` makes the alternate href primary. `--mode switch` also moves the
previous primary href back to `alternate[KEY].href`. In switch mode, the demoted
primary gets `alternate:name: original` unless `--switched-alternate-name` is set.
Use `--drop-alternates` with `--mode replace` when the promoted stream should not
carry the alternate asset map forward.

---

### `stacpkg items remove-alternate --alternate-key KEY`

**Description:** Remove alternate asset hrefs in items streams.

| Argument or option | Required | Default | Description |
| --- | --- | --- | --- |
| `--alternate-key KEY`, `--key KEY` | Yes | - | Alternate asset map key to remove. |

Remove `asset.alternate[KEY]` without changing the primary `asset.href`.

```bash
stacpkg items from-parquet source.items.parquet \
  | stacpkg items remove-alternate --alternate-key s3 \
  | stacpkg items to-parquet without-s3-alternate.items.parquet
```

---

### `stacpkg items add-alternate --asset-lock PATH --alternate-key KEY`

**Description:** Add alternate asset hrefs in items streams.

| Argument or option | Required | Default | Description |
| --- | --- | --- | --- |
| `--asset-lock PATH` | Yes | - | Asset-lock Arrow IPC stream path with alternate hrefs to add. |
| `--alternate-key KEY`, `--key KEY` | Yes | - | Alternate asset map key to write. |
| `--alternate-name NAME`, `--name NAME` | No | `--alternate-key` | `alternate:name` to write; defaults to `--alternate-key`. |

Add asset lock hrefs to STAC items metadata as alternate asset hrefs.
`--asset-lock` must point at an Apache Arrow IPC stream. Use
`asset-lock from-parquet` when the lock currently lives as Parquet.

```bash
stacpkg items from-parquet source.items.parquet \
  | stacpkg items add-alternate \
      --asset-lock <(stacpkg asset-lock from-parquet asset-lock.parquet) \
      --alternate-key mirror \
      --alternate-name "Mirror" \
  | stacpkg items to-parquet projected.items.parquet
```

`--alternate-key` selects the `asset.alternate[KEY]` map entry to write.
`--alternate-name` writes `alternate:name`; when omitted, it defaults to the
alternate key.

---

### `stacpkg items enrich --asset-lock PATH`

**Description:** Write asset lock facts into items.

| Argument or option | Required | Default | Description |
| --- | --- | --- | --- |
| `--asset-lock PATH` | Yes | - | Asset-lock Arrow IPC stream path. |
| `--alternate-key KEY` | No | None | Alternate asset map key to write when adding reconstructed lock hrefs. |

Read items metadata and an asset-lock Arrow IPC path, then write asset lock facts
back into STAC assets as File Info fields and, optionally, Alternate Assets
hrefs.

```bash
stacpkg items from-parquet source.items.parquet \
  | stacpkg items enrich \
      --asset-lock <(stacpkg asset-lock from-parquet asset-lock.parquet) \
      --alternate-key mirror \
  | stacpkg items to-parquet enriched.items.parquet
```

When alternate hrefs are a deterministic mapping of existing asset lock hrefs,
materialize that mapped asset lock first with `asset-lock relocate --dry-run`,
then enrich items from the planned lock:

```bash
stacpkg asset-lock from-parquet source.assets.lock.parquet \
  | stacpkg asset-lock relocate \
      --dry-run \
      --source-prefix https://example.com/source/ \
      --store-type s3 \
      --store-container bucket \
      --key source/ \
      --layout source-key \
  | stacpkg asset-lock to-parquet s3.assets.lock.parquet

stacpkg items from-parquet source.items.parquet \
  | stacpkg items enrich \
      --asset-lock <(stacpkg asset-lock from-parquet s3.assets.lock.parquet) \
      --alternate-key s3 \
  | stacpkg items to-parquet enriched.items.parquet
```

Enrichment writes `asset.alternate[KEY].href` from the href reconstructed from
the supplied asset-lock row. Use `asset-lock relocate --dry-run` when the lock
needs to describe alternate access paths without copying bytes.

## Asset-Lock Commands

### `stacpkg asset-lock derive`

**Description:** Derive asset lock rows from items streams.

| Argument or option | Required | Default | Description |
| --- | --- | --- | --- |
| `--probe-metadata`, `--no-probe-metadata` | No | `true` | Query referenced objects for current metadata while deriving rows. |
| `--item-ids ITEM_ID` | No | All item ids | Item id to keep; repeat for multiple item ids. |
| `--providers PROVIDER` | No | All providers | Provider name to keep; repeat for multiple provider names. |
| `--asset-keys ASSET_KEY` | No | All non-metadata asset keys | Asset key to keep; repeat for multiple asset keys. |
| `--include-metadata-assets` | No | `false` | Include assets whose asset key is `metadata`. |
| `--keep-going` | No | `false` | Keep rows after recoverable metadata errors. |
| `--max-workers WORKERS` | No | `4` | Maximum concurrent object metadata requests. |

Emit one asset lock row per locked STAC asset. By default,
`asset-lock derive` records structured locations plus current object metadata
facts such as `size_bytes`, `etag`, and `last_modified` when the backend reports
them. It skips assets whose key is `metadata`.

```bash
stacpkg items from-json selection.itemcollection.json \
  | stacpkg items promote-alternate --alternate-key s3 \
  | stacpkg asset-lock derive \
  | stacpkg asset-lock validate
```

Use `--no-probe-metadata` for a fast or offline derivation that only reuses
facts already present in STAC item metadata, such as `file:size`. Use
`--item-ids`, `--providers`, and `--asset-keys` to limit rows. Use
`--include-metadata-assets` to include `metadata` assets; an explicit
`--asset-keys metadata` filter also includes them.

---

### `stacpkg asset-lock from-parquet INPUT_FILE`

**Description:** Read asset-lock Parquet as Arrow IPC.

| Argument or option | Required | Default | Description |
| --- | --- | --- | --- |
| `INPUT_FILE` | Yes | - | Asset-lock Parquet input path. |
| `--batch-size ROWS` | No | `64000` | Maximum rows per IPC batch while reading Parquet. |

Read an asset-lock Parquet file and write an asset-lock Arrow IPC stream.

```bash
stacpkg asset-lock from-parquet source.assets.lock.parquet
```

The command iterates Parquet `RecordBatch` values rather than loading the whole
file. Use `--batch-size` to tune the maximum rows per IPC batch while reading
the Parquet file.

---

### `stacpkg asset-lock to-parquet OUTPUT_FILE`

**Description:** Write asset-lock streams as Parquet.

| Argument or option | Required | Default | Description |
| --- | --- | --- | --- |
| `OUTPUT_FILE` | Yes | - | Asset-lock Parquet output path. |

Read an asset-lock Arrow IPC stream and write an asset-lock Parquet file. This
adapter preserves incoming IPC batches and does not first materialize the full
asset-lock stream as an Arrow table.

```bash
stacpkg items from-parquet source.items.parquet \
  | stacpkg asset-lock derive \
  | stacpkg asset-lock to-parquet source.assets.lock.parquet
```

---

### `stacpkg asset-lock validate`

**Description:** Validate current assets against locked facts.

| Argument or option | Required | Default | Description |
| --- | --- | --- | --- |
| `--item-ids ITEM_ID` | No | All item ids | Item id to validate; repeat for multiple item ids. |
| `--asset-keys ASSET_KEY` | No | All asset keys | Asset key to validate; repeat for multiple asset keys. |
| `--keep-going` | No | `false` | Emit invalid rows after recoverable validation errors. |
| `--max-workers WORKERS` | No | `4` | Maximum concurrent object metadata requests. |

Validate current assets against locked facts. Validation reads an asset-lock
Arrow IPC stream and emits JSONL result rows with `valid` and `errors`.

```bash
stacpkg asset-lock from-parquet source.assets.lock.parquet \
  | stacpkg asset-lock validate
```

The command exits with status `0` only when every emitted result is valid.

---

### `stacpkg asset-lock relocate`

**Description:** Plan or relocate asset bytes into lock locations.

| Argument or option | Required | Default | Description |
| --- | --- | --- | --- |
| `--destination-lock PATH` | Destination option | - | Destination asset-lock Parquet path. Mutually exclusive with `--store-type`. |
| `--store-type {file,s3,gs,az,http,https}` | Destination option | - | Destination obstore storage type. Mutually exclusive with `--destination-lock`. |
| `--store-container VALUE` | When needed | None | Destination bucket, container, or HTTP origin. Required for `s3`, `gs`, `az`, `http`, and `https`. |
| `--store-endpoint-url URL` | No | None | Destination object-store endpoint URL. |
| `--key PREFIX` | When needed | Empty prefix | Destination key or path prefix. Required for `file`. |
| `--source-prefix PREFIX` | No | All source hrefs | Only map asset hrefs at or below this source href prefix. |
| `--layout {item-asset,source-key}` | No | `item-asset` | Destination key layout for relocated rows. |
| `--dry-run` | No | `false` | Write planned destination lock rows without copying asset bytes. |
| `--overwrite`, `--no-overwrite` | No | `--overwrite` | Overwrite existing destination objects. |
| `--keep-going` | No | `false` | Keep rows after recoverable relocation errors. |
| `--max-workers WORKERS` | No | `4` | Maximum concurrent relocation tasks. |
| `--memory-limit-bytes SIZE` | No | `2147483648` | Maximum reserved streaming relocation memory, e.g. `2GiB` or `512MiB`. |
| `--chunk-size-bytes SIZE` | No | `8388608` | Streaming relocation chunk size, e.g. `8MiB`. |
| `--put-max-concurrency COUNT` | No | `1` | Maximum concurrent multipart puts per relocation task. |

Relocate reads source asset-lock rows from stdin, creates or reads destination
rows, and writes the destination lock as an Arrow IPC stream. By default it also
transfers bytes and records destination object metadata. With `--dry-run`, it
writes the planned destination rows without copying bytes or proving that the
destination objects exist. Rows are matched by `item_id` and `asset_key`.

```bash
stacpkg asset-lock from-parquet source.assets.lock.parquet \
  | stacpkg asset-lock relocate \
      --store-type s3 \
      --store-container recipient-products \
      --key products/ \
  | stacpkg asset-lock to-parquet asset-lock.parquet
```

Use `--destination-lock` when destination asset-lock rows have already been
prepared, reviewed, or supplied by another party:

```bash
stacpkg asset-lock from-parquet source.assets.lock.parquet \
  | stacpkg asset-lock relocate \
      --destination-lock reviewed-destination.asset-lock.parquet \
  | stacpkg asset-lock to-parquet asset-lock.parquet
```

With `--store-type`, the command first maps destination locations in memory.
`--layout item-asset` writes keys from item and asset identifiers; `--layout
source-key` keeps the source object key shape under the destination prefix.
`--source-prefix` leaves non-matching rows unchanged.

Use `--dry-run` when the destination rows describe an equivalent access path
rather than a copy operation. For example, public HTTPS OpenAerialMap assets can
be projected to their equivalent S3 locations and then used as STAC alternates:

```bash
stacpkg asset-lock from-parquet source.assets.lock.parquet \
  | stacpkg asset-lock relocate \
      --dry-run \
      --source-prefix https://oin-hotosm-temp.s3.amazonaws.com/ \
      --store-type s3 \
      --store-container oin-hotosm-temp \
      --store-endpoint-url https://s3.amazonaws.com \
      --layout source-key \
  | stacpkg asset-lock to-parquet s3.assets.lock.parquet
```
