# Relocate assets

Use a STAC client to select existing Items as JSON or STAC GeoParquet.

## Copy and check

```bash
stacpkg items from-json selection.json \
  | stacpkg relocate relocated.pkg --target copied-assets/ --asset-keys thumbnail
stacpkg validate relocated.pkg > validation.jsonl
```

The example copies only thumbnails. Repeat `--asset-keys` to select more keys,
or omit it for all except `metadata`. Set `--include-metadata-assets` to include
that key. Only explicitly selected asset keys remain when a key filter is used.

The source locations come from the Item asset hrefs. `--target` sets where
the asset files are copied. `relocated.pkg` holds the new metadata and asset
lock; it is separate from the copied files.

Relocation hashes each source stream with SHA-256, checks any declared source
checksum, and checks the destination independently. It writes target hrefs,
original alternates, sizes, and checksums after all copies pass.

See [Relocate selected assets](../generated/usecases/openaerialmap-verified-relocation.md)
for a complete run and its output.

For a tutorial or workshop, copy the selection to prepared storage. Give
participants the package and read access to the copied assets so they can
use the prepared copy without access to the source.

The package directory must not exist. Destination objects are replaced only
with `--overwrite`. A failed run can leave copied objects at the target.
Use [`build --include-assets`](create-stac-package.md#include-asset-bytes) to put
bytes inside the package.

Validation compares assets with the lock and Item facts. It prints JSON Lines
and keeps the prior lock on failure. See the [Asset lock reference](../reference-guides/asset-lock.md#validation)
for result fields and baseline checks.

## Private storage

Choose an S3 alternate when the primary HTTPS href is unsuitable:

```bash
stacpkg items from-parquet selection.items.parquet \
  | stacpkg relocate relocated.pkg \
      --source-alternate s3 \
      --target s3://destination/selection/
```

Every selected asset must have that alternate. Either side can be local or
remote. Supply credentials only for stores that need them:

| Copy | Access needed |
| --- | --- |
| Private S3 to local | Read access to the source bucket. |
| Local to private S3 | Read and write access to the target bucket. |
| Private S3 to private S3 | Read access to the source; read and write access to the target. |
| Local to local | File permissions on both sides. |

The target needs read access because relocation checks the copied contents.
Credentials come from runtime settings and stay out of the package.

For one S3 account, set `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and,
when needed, `AWS_SESSION_TOKEN`. The local side does not use these settings.
For separate accounts, use the [per-bucket settings](../reference-guides/cli.md#storage-credentials).

An asset can record its S3 `store_endpoint_url`;
`--target-endpoint-url` sets the destination endpoint. Endpoints select a
storage service; credentials give access to it. Saved locations must not
contain credentials or temporary signed grants.

## Source paths and transfer settings

Relative hrefs use an explicit `--source-base`, then an absolute Item self
link, then the source JSON or Parquet path. For an Arrow stream without source
context, set `--source-base` to a document URI or a directory URI ending in `/`.

The [CLI reference](../reference-guides/cli.md#transfer-settings) lists worker, chunk, and buffer
settings. Select the Items and asset keys for the copy before relocation.

## Python

Use the same operations in a notebook:

```python
from stacpkg.workflows import relocate_package, validate_package

relocate_package("selection.json", "relocated.pkg", target="copied-assets/")
results = list(validate_package("relocated.pkg"))
print(results[-1])
```
