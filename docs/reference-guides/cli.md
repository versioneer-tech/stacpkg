# CLI reference

Package commands work with directories. Table commands use Arrow IPC on stdin
and stdout. When stdout is a terminal, table output is a short text preview.

## Streams and files

Use `from-parquet` and `to-parquet` to enter or leave a table pipeline.
The adapters and Item transforms process batches. Package planning and
validation hold the selection in memory.

`--asset-lock PATH` takes an Arrow IPC file, FIFO, or process-substitution
path. It does not take Parquet or `-`; stdin carries the Items stream.

```bash
stacpkg items from-parquet items.parquet \
  | stacpkg items enrich \
      --asset-lock <(stacpkg asset-lock from-parquet assets.lock.parquet) \
  | stacpkg items to-parquet enriched.parquet
```

## Source and checksum options

`relocate`, `build`, and `asset-lock derive` accept:

| Option | Default | Use |
| --- | --- | --- |
| `--source-base URI` | Source context or Item self link | Resolve relative hrefs. A directory URI must end in `/`. |
| `--source-alternate KEY` | Primary href | Use this alternate for every selected asset. Missing alternates fail. |

An explicit base takes priority, followed by an Item self link, then the input
document path. Use runtime credentials
for private stores. Saved hrefs and endpoints must not contain credentials or
temporary signed grants.

`build`, `asset-lock derive`, `asset-lock validate`, and `asset-lock relocate`
accept `--checksum METHOD`, defaulting to `metadata`. Package `validate` defaults
to `calculate-if-needed`. Package `relocate` always hashes and checks the copy.

Choose `metadata`, `use-etag`, `calculate-if-needed`, or `calculate-always`.
See [Asset lock](asset-lock.md#checksum-methods) for their behavior.

## Storage credentials

Source locations come from the Item assets, or from `--source-alternate`.
`--target` sets the destination for copied assets. Each store uses its own
runtime credentials. Local files use filesystem permissions.

For S3, the storage library reads `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
and optional `AWS_SESSION_TOKEN`. Use per-bucket environment variables when
the stores need different credentials:

| Setting | Example for `source-bucket` |
| --- | --- |
| Access key | `STACPKG_S3_ACCESS_KEY_ID_SOURCE_BUCKET` |
| Secret key | `STACPKG_S3_SECRET_ACCESS_KEY_SOURCE_BUCKET` |
| Session token, if needed | `STACPKG_S3_SESSION_TOKEN_SOURCE_BUCKET` |
| Region | `STACPKG_S3_REGION_SOURCE_BUCKET` |
| Service endpoint | `STACPKG_S3_ENDPOINT_SOURCE_BUCKET` |

Replace `SOURCE_BUCKET` with the bucket name in uppercase, with each group
of punctuation replaced by an underscore. For `target-bucket`, use
`TARGET_BUCKET`. These settings apply by bucket name, on either side of a
copy. Set both the access key and secret key when using per-bucket keys.

An endpoint stored with an asset takes priority over runtime endpoint
settings. `--target-endpoint-url` sets the target endpoint. Without a
per-bucket endpoint, `AWS_ENDPOINT_URL` or `AWS_ENDPOINT` can set a shared
endpoint for S3-compatible storage.

Relocation needs source read access and target read and write access.
Credentials are not stored in the package or passed as CLI options.

## Package commands

### `relocate OUTPUT_DIR --target URI`

Read Items, copy selected assets to the target, and write a package after all
copies pass. The output directory must not exist. A failed run can leave target
objects. Local targets must be outside the package directory; use
`build --include-assets` to put bytes inside it.

| Option | Default | Use |
| --- | --- | --- |
| `--target URI` | Required | Local directory or object-store prefix. |
| `--target-endpoint-url URL` | Runtime settings | Destination object-store endpoint. |
| `--asset-keys KEY` | All except `metadata` | Repeat to select keys. Other assets leave the output selection. |
| `--include-metadata-assets` | Off | Also copy the `metadata` asset key. |
| `--collection PATH` | Embedded documents | Repeat to supply Collection JSON. |
| `--overwrite` | Off | Permit replacement of destination objects. |

Source options are listed above; transfer settings are below. The output is a
JSON summary. Library method: `stacpkg.workflows.relocate_package`.

### `validate PACKAGE`

Check assets against Item and lock facts. Print JSON Lines results and a final
summary. Save the lock atomically only after all required assets pass. Missing
or extra rows, duplicate identities, conflicting facts, or changed assets fail
the check. Failure returns a non-zero exit code and keeps the prior lock.

Options: `--checksum METHOD` (default `calculate-if-needed`) and
`--max-workers COUNT` (default `4`). The [Asset lock reference](asset-lock.md#validation)
defines report fields. Library method: `stacpkg.workflows.validate_package`.
Consume its iterator to completion to save the lock.

### `build OUTPUT_DIR`

Read Items and write a [package directory](../architecture/0001-package-and-pipelines.md).
Assets stay at their current locations unless included.
The output directory must not exist. Build publishes it after the package is
complete. Included files must have distinct names and must not replace the
tables or copied assets.

| Option | Default | Use |
| --- | --- | --- |
| `--asset-lock PATH` | Derive from Items | Reuse an Arrow IPC lock. |
| `--collection PATH` | Preserve embedded documents | Repeat to supply Collection JSON objects or arrays. |
| `--includes PATH` | None | Repeat to include files or directories. |
| `--include-assets` | Off | Copy selected assets under `assets/` and set relative hrefs. |
| `--include-metadata-assets` | Off | Include the `metadata` asset key when deriving the lock. |
| `--item-ids ID` | All | Repeat to select Items. |
| `--providers NAME` | All | Repeat to select providers. |
| `--probe-metadata`, `--no-probe-metadata` | Probe | Query storage when deriving the lock. |

Source and checksum options are listed above. Disabling the probe does not
skip checks made by `--include-assets`. Explicit Collection documents replace
embedded ones. Library method: `stacpkg.dataset.build_package`.

### `inspect PACKAGE`

Print file digests, Item and asset counts, and embedded Collection IDs.
Use `--format {yaml,json,markdown,md}`; the default is `yaml`.

### `push PACKAGE TARGET` and `pull SOURCE --output-dir PATH`

Transfer package files through an OCI registry. Pull requires a new output
directory and checks each layer digest and size before publishing the directory.
External assets stay at their recorded locations. Run `validate` after pulling
to check the assets.

| Option | Default | Use |
| --- | --- | --- |
| `--auth-backend BACKEND` | `token` | oras authentication backend; use `basic` for HTTP Basic authentication. |
| `--plain-http` | Off | Use HTTP instead of HTTPS. |
| `--insecure` | Off | Disable TLS certificate checks. |

Runtime authentication can use `ORAS_USER`, `ORAS_PASS`, or Docker authentication
settings. Basic authentication does not require either transport flag.
Library methods: `stacpkg.oci.push_package` and `stacpkg.oci.pull_package`.
See [ADR-003](../architecture/0003-distribution-and-evidence.md) for artifact files.

## Item tables

All commands below start with `stacpkg items`.

| Command | Input | Output |
| --- | --- | --- |
| `from-json [FILE]` | Item or ItemCollection JSON; stdin if omitted. | Items Arrow stream. |
| `from-ndjson [FILE]` | One Item JSON per line; stdin if omitted. | Items Arrow stream. |
| `from-parquet FILE` | Local STAC GeoParquet. | Items Arrow stream. |
| `to-parquet FILE` | Items Arrow stream. | STAC GeoParquet file. |

The three readers accept repeatable `--collections ID`, `--providers NAME`,
and `--item-ids ID` filters. Repeated values within a filter are alternatives;
different filters must all match. JSON input accepts Items and ItemCollections.
Relative hrefs use the input document path or an absolute Item self link.

NDJSON and Parquet readers accept `--batch-size ROWS`, default `64000`.
The first NDJSON batch fixes the Arrow schema. Later batches can omit fields;
new fields or changed types cause an error. Use JSON input for varied fields.
The writer accepts repeatable `--collection PATH` inputs and preserves
[Collection metadata](items.md#collection-metadata) already in the stream.

### Asset hrefs and file facts

These commands read and write Items Arrow streams.

| Command | Required options | Behavior |
| --- | --- | --- |
| `promote-alternate` | `--alternate-key KEY` | Make an alternate the primary href. |
| `remove-alternate` | `--alternate-key KEY` | Remove that alternate and keep the primary. |
| `add-alternate` | `--asset-lock PATH --alternate-key KEY` | Add hrefs reconstructed from the lock. |
| `enrich` | `--asset-lock PATH` | Write lock size and checksum facts as File Info fields. |

`promote-alternate`, `remove-alternate`, and `add-alternate` also accept `--key`
as an alias for `--alternate-key`.

Promotion accepts `--mode {replace,switch}`, default `replace`. In `switch`
mode, the old primary becomes the selected alternate. Set its name with
`--switched-alternate-name NAME`, default `original`. `--drop-alternates` removes
the alternate map after replacement and cannot be used with `switch`.

`add-alternate` accepts `--alternate-name NAME` (alias `--name`), defaulting to
the alternate key. `enrich --alternate-key KEY` also adds a reconstructed href
there. Enrichment removes a source checksum if the lock has no checksum fact.

## Collection documents

`stacpkg collections extract FILE [--output PATH]` reads embedded Collection
documents and writes a JSON array sorted by ID. Output goes to stdout when
`--output` is omitted. Stored asset hrefs are preserved.
See [Items](items.md#collection-extraction) for the metadata format.

## Asset-lock tables

All commands below start with `stacpkg asset-lock`.

| Command | Input | Output |
| --- | --- | --- |
| `derive` | Items Arrow stream. | Asset-lock Arrow stream. |
| `from-parquet FILE` | Asset-lock Parquet. | Asset-lock Arrow stream. |
| `to-parquet FILE` | Asset-lock Arrow stream. | Asset-lock Parquet. |
| `validate` | Asset-lock Arrow stream. | JSON Lines results. |
| `relocate` | Source asset-lock Arrow stream. | Destination asset-lock Arrow stream. |

`from-parquet` accepts `--batch-size ROWS`, default `64000`.

### `derive`

Derive locations and query current file facts. Options:

- `--item-ids ID`, `--providers NAME`, and `--asset-keys KEY`: repeat to filter.
- `--include-metadata-assets`: include the `metadata` asset key; it is skipped by default.
- `--probe-metadata` or `--no-probe-metadata`: probing is on by default.
- `--keep-going`: retain rows after recoverable metadata errors.
- `--max-workers COUNT`: default `4`.

Source and checksum options are listed above. Offline derivation records
locations and known size facts.
An explicit `--asset-keys metadata` also includes metadata assets.

### `validate`

Compare current assets with locked facts. Options: repeatable `--item-ids ID`
and `--asset-keys KEY`, `--checksum METHOD` (default `metadata`), `--max-workers`
(default `4`), and `--keep-going`. Exit status is zero only when every emitted
result is valid. This command does not rewrite a saved lock.

### `relocate`

Choose a reviewed destination lock with `--destination-lock PATH` (Parquet),
or describe a destination with `--store-type {file,s3,gs,az,http,https}`.
These choices are mutually exclusive.

| Option | Default | Use |
| --- | --- | --- |
| `--store-container VALUE` | None | Bucket, container, or HTTP origin; required except for files. |
| `--store-endpoint-url URL` | Runtime settings | Destination object-store endpoint. |
| `--key PREFIX` | Empty | Destination prefix; required for files. |
| `--source-prefix URI` | All | Only map locations at or below this prefix. |
| `--layout {item-asset,source-key}` | `item-asset` | Use Item/asset names or keep source object key paths. |
| `--dry-run` | Off | Write destination rows without copying bytes or checking target existence. |
| `--overwrite`, `--no-overwrite` | Overwrite | Control replacement of destination objects. |
| `--keep-going` | Off | Retain rows after recoverable copy errors. |

Checksum options are listed above. Rows match by `item_id` and `asset_key`.
A destination lock can describe an alternate access route.

## Transfer settings

Package `relocate` and `asset-lock relocate` accept:

| Option | Default | Use |
| --- | --- | --- |
| `--max-workers COUNT` | `4` | Maximum copy tasks. |
| `--memory-limit-bytes SIZE` | `2GiB` | Reserved transfer buffer budget. |
| `--chunk-size-bytes SIZE` | `8MiB` | Streaming chunk size. |
| `--put-max-concurrency COUNT` | `1` | Multipart puts per task. |

Sizes accept bytes or units such as `128MiB`.
