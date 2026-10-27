# Asset lock

`assets.lock.parquet` records asset locations and object facts. One row names
one STAC Item asset location and the facts available to check or move it later.
The file does not contain asset bytes or saved validation results.

## Schema

The schema version is `v1`. `item_id` and `asset_key` are required. Other
columns can be empty when a fact is unknown.

| Column | Arrow type | Required | Meaning |
| --- | --- | --- | --- |
| `item_id` | `string` | Yes | STAC Item id. |
| `asset_key` | `string` | Yes | STAC Asset key inside that item. |
| `store_type` | `string` | No | Obstore storage type name: `file`, `s3`, `gs`, `az`, `http`, or `https`. |
| `store_container` | `string` | No | Store container such as an S3 bucket, GCS bucket, Azure container, or HTTP origin. |
| `store_endpoint_url` | `string` | No | Optional object-store endpoint URL, such as `https://s3.amazonaws.com` or a MinIO endpoint, used when the bucket name alone is ambiguous. |
| `key` | `string` | No | Object key, path, or package-relative file path inside the store container. |
| `size_bytes` | `int64` | No | Full object size in bytes, copied from STAC `file:size`, observed from object metadata, or observed after relocation. |
| `file_checksum` | `string` | No | Full-object checksum as lowercase hexadecimal Multihash. |
| `etag` | `string` | No | Backend object validator observed by metadata probing or relocation operations. |
| `last_modified` | `string` | No | Backend object last-modified timestamp observed by metadata probing or relocation operations, serialized as an ISO-8601 string when reported. |

Validation results are not asset-lock columns. `asset-lock validate` computes
`valid` and `errors` dynamically and prints JSON lines.

Assets whose key is `metadata` are skipped by default because these sidecar
objects often repeat item or asset metadata already preserved in `items.parquet`.
Use `--include-metadata-assets` to include them with the rest of the lock. An
explicit `--asset-keys metadata` filter also includes them.

## Locations

Asset locations are structured instead of stored as a single `href`. The
storage type names follow obstore (`file`, `s3`, `gs`, `az`, `http`, `https`),
`store_endpoint_url` can record the S3-compatible endpoint that makes a
bucket/key location unambiguous. Credentials stay outside the lock, including
bucket-scoped S3 runtime variables such as
`STACPKG_S3_ACCESS_KEY_ID_<BUCKET>`.

When `store_endpoint_url` is empty, S3 operations fall back to runtime endpoint
configuration from `STACPKG_S3_ENDPOINT_<BUCKET>`, `STACPKG_S3_ENDPOINTS_JSON`,
`AWS_ENDPOINT_URL`, or `AWS_ENDPOINT`.

## STAC mapping

`items enrich` writes File Info fields:

| Asset lock | STAC Asset field |
| --- | --- |
| `size_bytes` | `file:size` |
| `file_checksum` | `file:checksum` |

Enrichment removes a source checksum when the lock has no checksum fact.
Lock creation checks comparable source checksum declarations before returning
observed facts. Asset inclusion checks declarations while copying, including
when initial metadata queries are disabled.

When `items enrich --alternate-key` is used, reconstructed hrefs are written
through the STAC Alternate Assets extension. If alternate hrefs are a mapped view
of existing lock locations, create that mapped lock first with
`asset-lock relocate --dry-run`, then pass it to `items enrich`.

Field definitions: [Alternate Assets](https://github.com/stac-extensions/alternate-assets)
and [File Info](https://github.com/stac-extensions/file).

## Checksum methods

`build`, `asset-lock derive`, `asset-lock validate`, and `asset-lock relocate`
accept `--checksum`. Their default is `metadata`. Package `validate` defaults
to `calculate-if-needed`. Package `relocate` always hashes the source stream
and checks the destination independently.

| Method | Behavior |
| --- | --- |
| `metadata` | Use supported full-object provider checksum fields, if available. |
| `use-etag` | Also permit a single-part MD5 ETag. Reject weak and multipart ETags. |
| `calculate-if-needed` | Read bytes when no comparable provider checksum is available. |
| `calculate-always` | Read bytes even when metadata contains a checksum. |

Calculation uses SHA-256 for a new checksum. Validation uses the expected
algorithm when comparing an existing supported checksum. Supported algorithms
are MD5, SHA-1, SHA-256, and SHA-512. Provider field normalization requires
`FULL_OBJECT`; composite and unsupported checksum fields are ignored.
Use a calculation method to check bytes through S3.

`--no-probe-metadata` skips initial asset queries and does not fill
`file_checksum`, regardless of the checksum option. With `build --include-assets`,
the subsequent copy still reads bytes and saves checked facts. A dry-run
destination plan can retain a checksum, but it is not evidence that the
destination was checked.

## Validation

`asset-lock validate` reads lock rows and prints results. It does not rewrite a
saved lock. `validate PACKAGE` also checks that Item metadata and lock rows
name the same assets and locations. It replaces `assets.lock.parquet`
atomically only after the full required selection passes.

Both paths compare known size, ETag, modification time, and checksum facts.
A matching full-object checksum permits changed or unavailable ETag and
modification-time values. These differences are reported in `metadata_changes`.
Size is always compared when expected. Without a matching checksum, known
expected storage facts must have comparable observations. A checksum mismatch
or an unavailable comparable checksum fails the check. Failed package checks
preserve the prior lock and expected checksum.

A package report has one JSON object per asset and a final summary. Asset
results contain identity and location, `valid`, `errors`, `method`,
`expected_checksum`, `observed_checksum`, `comparison`, and observed facts.
Methods distinguish `metadata`, `provider-ALGORITHM`, `etag-md5`, and
`bytes-ALGORITHM`. `comparison=baseline` means no prior checksum was available;
`comparison=match` means the expected checksum matched. If the Item and lock
use different algorithms, `declaration_check` records an additional comparison
with the Item checksum. Both checks must pass. Failures use
`comparison=mismatch` or `comparison=unavailable`. `comparison=metadata`
means the check has no checksum comparison. A scope or input error emits a
failed summary without claiming asset coverage.

The summary records `checked`, `passed`, `failed`, `baseline`, `excluded`,
`methods`, `checksum`, `lock_written`, and the Items file digest. The `metadata`
asset key is excluded unless present in the lock. Other missing or extra rows,
duplicate identities, and Item/lock disagreements fail the package check.
Package checks require Item ids to be unique across the selection.

Save a report only when needed:

```bash
stacpkg validate selection.pkg > validation.jsonl
```

An ETag is a storage validator. Checksum promotion requires a service that uses
single-part MD5.
