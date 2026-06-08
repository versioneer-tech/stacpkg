# Create STAC Package

This tutorial walks through a small `stacpkg` packaging workflow. You will
query the OpenAerialMap STAC API over Austria, derive object metadata facts for the
imagery assets, validate those assets, create a package, enrich the item
metadata, and write an inspection summary.

The goal is not to cover every option. The goal is to see the shape of the
workflow once, end to end. Start with the short path when you only need the
package artifact. Use the step-by-step flow when you want reusable intermediate
tables for inspection, validation, or later commands.

## Short Path

The example queries the OpenAerialMap STAC API at
`https://api.imagery.hotosm.org/stac` for the `openaerialmap` collection. It
uses a small Austria-only bounding box, sorts by `start_datetime` oldest first,
and limits the response to two Items.

OpenAerialMap items can include `metadata`, `thumbnail`, and `visual` assets.
`stacpkg build` skips `metadata` assets by default, so the package asset lock
contains the `thumbnail` and `visual` assets used in this sample.

This shortest flow streams the selected STAC Items into a package directory
without writing an items checkpoint first. Run the commands in the same shell so
`$tmpdir` remains available:

!!! note
    We recommend the [`rustac` CLI](https://github.com/stac-utils/rustac) for
    this short path because it is fast and stream-oriented. You can also use
    `pystac-client` or direct HTTP calls; the
    [step-by-step checkpoints](#step-by-step-checkpoints) below demonstrate the
    direct HTTP path with `curl`.

```bash
tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/stacpkg-openaerialmap-austria.XXXXXX")
export STACPKG_TUTORIAL_DIR="$tmpdir"
bbox='16.415,47.1705,16.431,47.734' # subset of Austria

rustac --output-format ndjson search \
    https://api.imagery.hotosm.org/stac \
    - \
    --collections openaerialmap \
    --bbox "$bbox" \
    --sortby start_datetime \
    --max-items 2 \
  | stacpkg items from-ndjson \
  | stacpkg build "$tmpdir/openaerialmap-austria.pkg"

echo "created $tmpdir/openaerialmap-austria.pkg"
```

Sample output:

```text
created /tmp/stacpkg-openaerialmap-austria.ABC123/openaerialmap-austria.pkg
```

The command creates this package layout:

```text
/tmp/stacpkg-openaerialmap-austria.ABC123/openaerialmap-austria.pkg/
  items.parquet
  assets.lock.parquet
```

This package can now be pushed to any OCI-compliant registry; see
[Distribute The Package](#distribute-the-package) below.

Validate the package's asset lock whenever you want to check the recorded object
metadata against the current live assets:

```bash
stacpkg asset-lock from-parquet "$tmpdir/openaerialmap-austria.pkg/assets.lock.parquet" \
  | stacpkg asset-lock validate
```

## Step-by-Step Checkpoints

The rest of this tutorial expands the same workflow into materialized
checkpoints under `$tmpdir`. This is useful when you want to inspect the selected
Items, validate the asset lock before building, or reuse an intermediate table
in another command.

### Materialize The Items

Create a temporary directory, then use a plain STAC API `curl` request to fetch
one small ItemCollection response. `items from-json` adapts that STAC JSON into
the items Arrow stream, and `items to-parquet` writes a reusable STAC
GeoParquet-style checkpoint:

```bash
tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/stacpkg-openaerialmap-austria.XXXXXX")
export STACPKG_TUTORIAL_DIR="$tmpdir"
bbox='16.415,47.1705,16.431,47.734' # subset of Austria

curl -fsS https://api.imagery.hotosm.org/stac/search \
  --header "Accept: application/geo+json" \
  --header "Content-Type: application/json" \
  --data-binary "{
    \"collections\": [\"openaerialmap\"],
    \"bbox\": [$bbox],
    \"sortby\": [{\"field\": \"start_datetime\", \"direction\": \"asc\"}],
    \"limit\": 2
  }" \
  | stacpkg items from-json \
  | stacpkg items to-parquet "$tmpdir/openaerialmap-austria.items.parquet"

echo "created $tmpdir/openaerialmap-austria.items.parquet"
```

Sample output:

```text
created /tmp/stacpkg-openaerialmap-austria.ABC123/openaerialmap-austria.items.parquet
```

This `curl` request asks the STAC API for one two-item response. It does not page
through all matches. For larger searches, prefer the NDJSON `rustac` path from
the short flow so `stacpkg items from-ndjson` can stream item batches into Arrow
IPC.

Rendered as selected columns, the source selection now looks like this.

| item_id | collection | start_datetime | title | asset keys |
| --- | --- | --- | --- | --- |
| `631ee6653cdf1c0006b63c5b` | `openaerialmap` | `2022-07-28T13:20:34Z` | `Winzerstrasse - 28.7.2022` | `metadata`, `thumbnail`, `visual` |
| `631ee8593cdf1c0006b63c5f` | `openaerialmap` | `2022-07-28T16:11:00Z` | `Brundlgfangen - 28.7.2022` | `metadata`, `thumbnail`, `visual` |

### Lock Asset Metadata

Create an asset lock with probed metadata for all non-metadata assets:

```bash
stacpkg items from-parquet "$tmpdir/openaerialmap-austria.items.parquet" \
  | stacpkg asset-lock derive \
  | stacpkg asset-lock to-parquet "$tmpdir/openaerialmap-austria.assets.lock.parquet"

echo "created $tmpdir/openaerialmap-austria.assets.lock.parquet"
```

Sample output:

```text
created /tmp/stacpkg-openaerialmap-austria.ABC123/openaerialmap-austria.assets.lock.parquet
```

The lock has one row per non-metadata asset. For this sample that is two Items
times the `thumbnail` and `visual` asset keys, or four asset lock rows.

Rendered as selected columns, the metadata lock looks like this:

| item_id | asset_key | store_type | store_container | key | size_bytes | etag | last_modified |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `631ee6653cdf1c0006b63c5b` | `thumbnail` | `https` | `https://oin-hotosm-temp.s3.amazonaws.com` | `631ee60c.../631ee60c...png` | `793014` | `"81acb9dabba94aa59ab376948bf46259"` | `2025-01-16T18:35:01+00:00` |
| `631ee6653cdf1c0006b63c5b` | `visual` | `https` | `https://oin-hotosm-temp.s3.amazonaws.com` | `631ee60c.../631ee60c...tif` | `9913534` | `"7d1bfba6a65db543052fa6d7b4187e64-2"` | `2025-01-16T18:35:01+00:00` |
| `631ee8593cdf1c0006b63c5f` | `thumbnail` | `https` | `https://oin-hotosm-temp.s3.amazonaws.com` | `631ee840.../631ee840...png` | `609308` | `"c1797f27c862a7ece7a05dbb4f505171"` | `2025-01-16T18:35:02+00:00` |
| `631ee8593cdf1c0006b63c5f` | `visual` | `https` | `https://oin-hotosm-temp.s3.amazonaws.com` | `631ee840.../631ee840...tif` | `5434212` | `"552f232cc25c681bf4457572522cefbd"` | `2025-01-16T18:35:02+00:00` |

### Validate The Assets

Use the metadata-probed asset lock to validate the recorded provenance facts
against the current live assets:

```bash
stacpkg asset-lock from-parquet "$tmpdir/openaerialmap-austria.assets.lock.parquet" \
  | stacpkg asset-lock validate
```

Sample output:

```json
{"asset_key":"thumbnail","errors":[],"item_id":"631ee6653cdf1c0006b63c5b","key":"631ee60c3cdf1c0006b63c56/0/631ee60c3cdf1c0006b63c57.png","store_container":"https://oin-hotosm-temp.s3.amazonaws.com","store_type":"https","valid":true}
{"asset_key":"visual","errors":[],"item_id":"631ee6653cdf1c0006b63c5b","key":"631ee60c3cdf1c0006b63c56/0/631ee60c3cdf1c0006b63c57.tif","store_container":"https://oin-hotosm-temp.s3.amazonaws.com","store_type":"https","valid":true}
{"asset_key":"thumbnail","errors":[],"item_id":"631ee8593cdf1c0006b63c5f","key":"631ee8403cdf1c0006b63c5d/0/631ee8403cdf1c0006b63c5e.png","store_container":"https://oin-hotosm-temp.s3.amazonaws.com","store_type":"https","valid":true}
{"asset_key":"visual","errors":[],"item_id":"631ee8593cdf1c0006b63c5f","key":"631ee8403cdf1c0006b63c5d/0/631ee8403cdf1c0006b63c5e.tif","store_container":"https://oin-hotosm-temp.s3.amazonaws.com","store_type":"https","valid":true}
```

Validation prints dynamic JSON lines and exits non-zero if any asset no longer
matches the lock.

### Build A Package

Create a package directory from the selected Items and the validated asset
lock:

```bash
stacpkg items from-parquet "$tmpdir/openaerialmap-austria.items.parquet" \
  | stacpkg build "$tmpdir/openaerialmap-austria.pkg" \
      --asset-lock <(stacpkg asset-lock from-parquet \
        "$tmpdir/openaerialmap-austria.assets.lock.parquet")

echo "created $tmpdir/openaerialmap-austria.pkg/items.parquet"
echo "created $tmpdir/openaerialmap-austria.pkg/assets.lock.parquet"
```

Sample output:

```text
created /tmp/stacpkg-openaerialmap-austria.ABC123/openaerialmap-austria.pkg/items.parquet
created /tmp/stacpkg-openaerialmap-austria.ABC123/openaerialmap-austria.pkg/assets.lock.parquet
```

The package contains:

```text
/tmp/stacpkg-openaerialmap-austria.ABC123/openaerialmap-austria.pkg/
  items.parquet
  assets.lock.parquet
```

At this point the package has snapshotted the selected STAC Items and the
validated asset lock rows.

### Inspect The Asset Lock

Read the package's asset lock when you want to see referenced assets as rows:

```bash
stacpkg asset-lock from-parquet "$tmpdir/openaerialmap-austria.pkg/assets.lock.parquet"
```

Sample terminal output:

```text
item_id                  | asset_key | store_type | size_bytes
-------------------------+-----------+------------+-----------
631ee6653cdf1c0006b63c5b | thumbnail | https      | 793014
631ee6653cdf1c0006b63c5b | visual    | https      | 9913534
4 rows x 9 columns
```

This matches the four rows from the earlier "Lock Asset Metadata" section:
`stacpkg build` placed that validated asset lock into the package as
`assets.lock.parquet`.

The items table answers which STAC records were selected. The asset lock answers
which asset files those records reference and what object metadata was observed.

### Enrich The STAC Items

The OpenAerialMap objects are also available through public S3. Write asset
metadata and S3 alternate hrefs back into STAC GeoParquet. First create an
S3-shaped asset lock from the package lock. This maps HTTPS locations to
equivalent `s3://` hrefs without copying bytes:

```bash
stacpkg asset-lock from-parquet "$tmpdir/openaerialmap-austria.pkg/assets.lock.parquet" \
  | stacpkg asset-lock relocate \
      --dry-run \
      --source-prefix https://oin-hotosm-temp.s3.amazonaws.com/ \
      --store-type s3 \
      --store-container oin-hotosm-temp \
      --store-endpoint-url https://s3.amazonaws.com \
      --layout source-key \
  | stacpkg asset-lock to-parquet "$tmpdir/openaerialmap-austria.s3.assets.lock.parquet"

echo "created $tmpdir/openaerialmap-austria.s3.assets.lock.parquet"
```

Sample output:

```text
created /tmp/stacpkg-openaerialmap-austria.ABC123/openaerialmap-austria.s3.assets.lock.parquet
```

Then enrich the items from that mapped lock:

```bash
stacpkg items from-parquet "$tmpdir/openaerialmap-austria.pkg/items.parquet" \
  | stacpkg items enrich \
      --asset-lock <(stacpkg asset-lock from-parquet \
        "$tmpdir/openaerialmap-austria.s3.assets.lock.parquet") \
      --alternate-key s3 \
  | stacpkg items to-parquet "$tmpdir/openaerialmap-austria.enriched.items.parquet"

echo "created $tmpdir/openaerialmap-austria.enriched.items.parquet"
```

Sample output:

```text
created /tmp/stacpkg-openaerialmap-austria.ABC123/openaerialmap-austria.enriched.items.parquet
```

This keeps the asset lock as the package contract while making selected fields
and `alternate.s3.href` values available to STAC clients.

For the first selected thumbnail, the enriched STAC asset now includes:

```json
{
  "href": "https://oin-hotosm-temp.s3.amazonaws.com/631ee60c3cdf1c0006b63c56/0/631ee60c3cdf1c0006b63c57.png",
  "alternate": {
    "s3": {
      "href": "s3://oin-hotosm-temp/631ee60c3cdf1c0006b63c56/0/631ee60c3cdf1c0006b63c57.png"
    }
  }
}
```

### Inspect The Package

Create a package summary:

```bash
stacpkg inspect "$tmpdir/openaerialmap-austria.pkg" > "$tmpdir/inspect.yaml"

echo "created $tmpdir/inspect.yaml"
```

Sample output:

```text
created /tmp/stacpkg-openaerialmap-austria.ABC123/inspect.yaml
```

Sample `$tmpdir/inspect.yaml` contents:

```yaml
package: "/tmp/stacpkg-openaerialmap-austria.ABC123/openaerialmap-austria.pkg"
items:
  count: 2
  collections:
    - "openaerialmap"
assets:
  count: 4
  asset_keys:
    - "thumbnail"
    - "visual"
  known_size_bytes: 16750068
  known_size_count: 4
```

Open `$tmpdir/inspect.yaml` and check the item count, asset count, and package
files.

### Distribute The Package

You can now push this package to any OCI-compliant registry and pull it back in
another environment. Use `$tmpdir/openaerialmap-austria.pkg` as the package
directory for `stacpkg push`; `stacpkg pull` reconstructs the same
`items.parquet` and `assets.lock.parquet` files.

See the [package commands](../reference-guides/cli.md#package-commands) in the
CLI reference for the `push` and `pull` syntax, including local HTTP registry
options.

## What You Learned

You streamed an OpenAerialMap Austria STAC search directly into a package, then
expanded the same workflow with materialized checkpoints. The package now has:

- the exact selected Items in `items.parquet`;
- validated object metadata in `assets.lock.parquet`;
- package file metadata derived from the package directory and OCI descriptors;
- dynamic validation results for current assets;
- an enriched items table and a package inspection summary;
- an OCI-distributable package that can be pushed and pulled.

Related executable coverage for OpenAerialMap packaging lives in
[`tests/e2e/test_usecase_openaerialmap_s3_alternate_package.py`](https://github.com/versioneer-tech/stacpkg/blob/main/tests/e2e/test_usecase_openaerialmap_s3_alternate_package.py).
