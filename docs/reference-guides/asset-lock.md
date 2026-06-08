# Asset Lock

`assets.lock.parquet` records verifiable assets for a package: STAC Asset
references with locked locations and store facts. One row names one locked STAC
Asset location and the facts needed to check or move that asset later. The term
does not require bundled bytes; it means the package has enough recorded
evidence to validate, relocate, and hand over referenced assets intentionally.

## Schema

The current asset-lock schema version is `v1`. `stacpkg` writes a stable,
wide schema for every asset-lock table so files from different operations can be
concatenated and streamed through the same Arrow contract. `item_id` and
`asset_key` are required; the other columns are nullable because different
operations populate different facts.

| Column | Arrow type | Required | Meaning |
| --- | --- | --- | --- |
| `item_id` | `string` | Yes | STAC Item id. |
| `asset_key` | `string` | Yes | STAC Asset key inside that item. |
| `store_type` | `string` | No | Obstore storage type name: `file`, `s3`, `gs`, `az`, `http`, or `https`. |
| `store_container` | `string` | No | Store container such as an S3 bucket, GCS bucket, Azure container, or HTTP origin. |
| `store_endpoint_url` | `string` | No | Optional object-store endpoint URL, such as `https://s3.amazonaws.com` or a MinIO endpoint, used when the bucket name alone is ambiguous. |
| `key` | `string` | No | Object key, path, or package-relative file path inside the store container. |
| `size_bytes` | `int64` | No | Full object size in bytes, copied from STAC `file:size`, observed from object metadata, or observed after relocation. |
| `etag` | `string` | No | Backend object validator observed by metadata probing or relocation operations. |
| `last_modified` | `string` | No | Backend object last-modified timestamp observed by metadata probing or relocation operations, serialized as an ISO-8601 string when reported. |

Validation results are not asset-lock columns. `asset-lock validate` computes
`valid` and `errors` dynamically and prints JSON lines.

Assets whose key is `metadata` are skipped by default because these sidecar
objects often repeat item or asset metadata already preserved in `items.parquet`.
Use `--include-metadata-assets` to include them with the rest of the lock. An
explicit `--asset-keys metadata` filter also includes them.

Deferred fields such as media type, checksums, provider object identity, and
content type are intentionally outside the active schema. Draft checksum
semantics are captured in
[ADR-004: Asset-Lock Checksum Facts and STAC Projection](../architecture/0004-asset-lock-checksum-facts-and-stac-projection.md).
That draft plans to store checksum facts in the asset lock first and then
project them to STAC `file:checksum`.

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

## STAC Mapping

`items enrich` writes lock size facts back to STAC Assets using the STAC File
Info extension:

| Asset lock | STAC Asset field |
| --- | --- |
| `size_bytes` | `file:size` |

The active lock does not write checksum facts back to STAC metadata.
Planned checksum support will extend this mapping with `file_checksum` to
`file:checksum` after checksum facts become part of the asset-lock schema.

When `items enrich --alternate-key` is used, reconstructed hrefs are written
through the STAC Alternate Assets extension. If alternate hrefs are a mapped view
of existing lock locations, create that mapped lock first with
`asset-lock relocate --dry-run`, then pass it to `items enrich`.

References:

- STAC Alternate Assets extension: <https://github.com/stac-extensions/alternate-assets>
- STAC File Info extension: <https://github.com/stac-extensions/file>

## Validation

`asset-lock validate` compares a lock row with the current asset at its structured
location. It validates against `assets.lock`; upload and download transfer
correctness remains the responsibility of the object-store library.

Validation compares `size_bytes`, `etag`, and `last_modified` when those facts
are locked and the backend reports comparable current values. A missing nullable
fact is skipped.

## ETag Handling

`etag` is stored as a backend object validator. It is not treated as a portable
file checksum. S3 multipart ETags and weak HTTP ETags are validators with
store-specific meaning, so byte-level checksum semantics are deferred.
