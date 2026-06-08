# ADR-004: Asset-Lock Checksum Facts and STAC Projection

| Status | Date | Implementation |
| --- | --- | --- |
| Draft | 2026-06-04 | Not implemented yet. |

## Context

The active asset-lock schema records asset identity, structured location, and
the current object facts from [ADR-003](0003-asset-lock-row-structure.md):

```text
size_bytes
etag
last_modified
```

`items enrich` currently writes only `size_bytes` back to STAC as `file:size`.
It deliberately removes source `file:checksum`. The active lock does not write
checksum facts back to STAC metadata.

The STAC File Info extension defines `file:checksum` as lowercase hexadecimal
Multihash, but checksum columns are outside the active asset-lock schema.

External checksum metadata is not uniform. S3 exposes checksum values alongside
a checksum type that distinguishes `FULL_OBJECT` from `COMPOSITE`, and S3 ETags
may or may not be MD5 digests depending on upload and encryption details. The
asset-lock checksum design therefore needs to distinguish portable file
checksums from store-specific validators.

STAC Items may already contain `file:checksum`. Copying that value into
`assets.lock.parquet` during metadata-only lock creation would make the lock
look more certain than it is. The package would be storing source metadata, not
a fact observed from the storage system that `stacpkg` will later validate
against.

ETags have a similar problem. They are useful storage validators, but S3
multipart ETags and weak HTTP ETags are not full-object file checksums that mean
the same thing across stores. Package file digests and OCI descriptor digests
describe package files, not external assets referenced by the asset-lock table.

## Decision

Implement checksum support by making the asset lock the source of truth and
projecting from there into STAC metadata.

Add nullable `file_checksum` to the asset-lock schema. The column belongs with
the other object facts:

```text
size_bytes
file_checksum
etag
last_modified
```

`file_checksum` in `assets.lock.parquet` means a checksum fact that the lock can
use for validation against the current store. It does not mean an unverified
checksum string copied from input STAC metadata. STAC `file:checksum` is a
projection from the lock, not the primary package record.

`file_checksum` must be encoded as lowercase hexadecimal Multihash compatible
with the STAC File Info extension. Reuse the existing checksum helpers instead
of adding another checksum representation.

When `stacpkg` calculates a checksum from bytes, it uses SHA-256 by default.
SHA-256 is already representable as STAC Multihash, is supported by S3 checksum
metadata, and avoids relying on MD5 or SHA-1 for newly calculated checksums.

Manifest-only or no-probe locking may fill `size_bytes` from STAC `file:size`,
but it must not copy STAC `file:checksum` into `assets.lock.parquet`.

Fill `file_checksum` only from evidence tied to the store location:

- provider metadata when it reports a supported full-object checksum;
- explicit ETag promotion when the caller selects that method and the ETag is
  compatible with a single-part MD5 checksum;
- explicit byte-stream calculation when the selected checksum method requires
  it;
- copy or relocation paths when they observe or derive a full-object checksum
  fact.

The first provider-metadata implementation supports S3-style full-object
checksum fields that can be converted to STAC Multihash:

```text
ChecksumSHA256
ChecksumSHA1
ChecksumSHA512
ChecksumMD5
x-amz-checksum-sha256
x-amz-checksum-sha1
x-amz-checksum-sha512
x-amz-checksum-md5
```

Accept those fields only when the companion checksum type says `FULL_OBJECT`,
using either `ChecksumType` or `x-amz-checksum-type`. Ignore `COMPOSITE`
checksums, CRC checksums, and XXHash checksums.

Add checksum strategies to lock-producing commands such as `asset-lock derive`
and `build`:

- `metadata`: use only supported full-object checksums reported by object
  metadata; this is the default checksum strategy when metadata probing is
  enabled;
- `use-etag`: promote only compatible single-part MD5 ETags to Multihash;
- `calculate-if-needed`: use provider metadata when available and stream the
  asset bytes only when no supported checksum is reported;
- `calculate-always`: stream the asset bytes and calculate the checksum even
  if metadata already contains a checksum.

Byte-stream calculation must be explicit so metadata-only workflows do not
unexpectedly read large assets. `--no-probe-metadata` remains unable to fill
`file_checksum`.

Do not add a verification/import command for existing source STAC
`file:checksum` in this implementation. Default lock creation remains
store-evidence first. Existing source STAC checksums are not imported into
`assets.lock.parquet`.

Keep `etag` separate from `file_checksum`. Do not automatically treat storage
validators as file checksums that mean the same thing across stores.

Relocation must preserve or refresh checksums deliberately. `asset-lock
relocate --dry-run` may carry `file_checksum` forward only when the destination
lock is a planned alternate view of the same bytes. Actual copy or relocation
paths should prefer a checksum observed or calculated for the destination
object. They may preserve a verified source checksum only when the copy path
guarantees byte-for-byte transfer and no destination checksum is available.

`asset-lock validate` should compare locked `file_checksum` values with current
store evidence when the backend reports a comparable checksum. A validation mode
that streams bytes may calculate and compare the checksum when metadata is not
enough.

`items enrich` projects `file_checksum` back to STAC as `file:checksum` only
when the lock row contains a current store-derived checksum fact. If a lock row
has no `file_checksum`, enrichment must continue removing any stale source
`file:checksum` for that asset.

Validation results are command output, not saved lock state. Results such as
`valid` and `errors` must not be stored in `assets.lock.parquet`.

## Implementation Strategy

Add `file_checksum` as a nullable asset-lock fact column and keep `etag`
separate. Update schema metadata, fixtures, and column filtering so only
accepted asset-lock fields are preserved.

Add `--checksum {metadata,use-etag,calculate-if-needed,calculate-always}` to
lock-producing and validation commands. The default is `metadata`; byte
calculation uses SHA-256. `--no-probe-metadata` does not fill
`file_checksum`.

Normalize provider checksum metadata in one place. For the first implementation,
convert S3-style `ChecksumSHA256`, `ChecksumSHA1`, `ChecksumSHA512`,
`ChecksumMD5`, and matching `x-amz-checksum-*` headers only when the checksum
type is `FULL_OBJECT`. Ignore `COMPOSITE`, CRC, and XXHash checksums.

During relocation, carry `file_checksum` through dry-run mapping only when the
new lock describes the same bytes. Copying paths should prefer a destination
checksum when available, calculate one when requested, and preserve a verified
source checksum only when byte-for-byte copy semantics are guaranteed.

Validation compares locked `file_checksum` with current store evidence, or with
a calculated SHA-256 checksum when the selected strategy allows streaming bytes.
Validation mismatches remain command output and are never written back into
`assets.lock.parquet`.

`items enrich` maps `size_bytes` to `file:size` and `file_checksum` to
`file:checksum`. If the lock row has no `file_checksum`, enrichment continues
to remove stale source `file:checksum`.

Tests cover Multihash normalization, S3-style full-object metadata, rejected
composite and unsupported checksums, ETag promotion limits, metadata-only
behavior, SHA-256 calculation, validation mismatches, relocation propagation,
STAC enrichment, and the rule that source STAC `file:checksum` is not imported.

## Alternatives Considered

- **Copy source STAC `file:checksum`:** Easy to preserve, but it can be stale or
  unrelated to the store location being locked.
- **Treat all ETags as checksums:** Convenient for some S3 objects, but wrong
  for multipart uploads, weak HTTP validators, and storage-specific validators.
- **Always compute checksums by streaming bytes:** Strong evidence, but too
  expensive for large assets and surprising for metadata-only package creation.
- **Use package file digests for asset-lock checksums:** Package file and OCI
  descriptor digests describe package files. Asset-lock checksums describe
  referenced assets and need their own evidence from the store.
- **Store validation results in the lock:** Makes a past validation look
  lasting. Validation results are observations at one time and should remain
  command output or diagnostics.

## Consequences

Checksum support requires an asset-lock schema revision. Existing readers and
tests need to handle the old schema and the new schema deliberately, and fixture
generation must include the new nullable `file_checksum` column once the schema
is accepted.

The package checksum contract becomes asset-lock first. Workflows that require
byte-level reproducibility will create or receive an asset lock with
`file_checksum`; STAC `file:checksum` is emitted later by `items enrich` from
that lock value.

Lock creation and validation gain explicit checksum strategy choices. Cheap
metadata-only workflows remain cheap, while stronger workflows can opt into ETag
promotion or byte-stream calculation.

Calculated checksum workflows standardize on SHA-256. Provider metadata support
starts with full-object S3-style MD5, SHA-1, SHA-256, and SHA-512 fields only;
composite checksums and unsupported algorithms are ignored rather than stored as
weaker facts.

Relocation paths must decide whether they are preserving a verified source
checksum, observing a destination checksum, or calculating a destination
checksum. Dry-run mapping cannot imply new byte evidence.

Existing source STAC `file:checksum` remains non-authoritative input metadata.
This implementation does not add a trusted import path for it.

Tests must cover Multihash normalization, S3-style provider metadata, ETag
promotion limits, metadata-only behavior, SHA-256 byte-calculation strategies,
validation mismatches, relocation propagation, and STAC enrichment from
`file_checksum`.
