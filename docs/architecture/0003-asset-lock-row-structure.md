# ADR-003: Asset Lock Row Structure

| Status | Date | Implementation |
| --- | --- | --- |
| Accepted | 2026-05-17 | Implemented in active asset-lock schema `v1` as of `0.1.0`. |

## Context

`assets.lock.parquet` is the package asset-lock table. One row names one STAC
Asset location and stores the facts needed to write STAC hrefs back out, move
the asset, and validate the asset later.

The schema must stay stable when a package moves between local directories,
object stores, and OCI registries.

A single `href` string is convenient for display, but it mixes several things
together: store type, bucket or container, object key, endpoint, and sometimes
credentials or signed query parameters. That makes validation and relocation
harder.

## Decision

Use flat, structured asset-lock rows. Each row has:

- identity fields;
- location fields;
- nullable object facts.

Nullable means a column can be empty when an operation cannot know that fact.
The physical schema stays the same across operations, but each operation only
fills the facts it can observe.

Required identity fields:

```text
item_id
asset_key
```

Location fields:

```text
store_type
store_container
store_endpoint_url
key
```

`store_type` uses obstore storage type names: `file`, `s3`, `gs`, `az`, `http`,
or `https`. These names describe the kind of storage. They are not rclone remote
names or config section names.

`store_endpoint_url` may record the service endpoint needed to make
S3-compatible bucket/key locations unambiguous. Credentials and runtime auth
configuration stay outside `assets.lock.parquet`.

Current object facts that can be carried between stores:

```text
size_bytes
etag
last_modified
```

`asset-lock derive --no-probe-metadata` normally fills only `size_bytes`, and
only when STAC `file:size` is present. Default metadata probing and relocation
operations can fill object facts such as ETag and last modified time when the
storage service reports them.

Do not store a main `href` column. Rebuild hrefs from `store_type`,
`store_container`, `store_endpoint_url`, and `key` when projecting back to STAC
or when calling current object-store APIs.

This ADR defines the active row structure only. Draft checksum rules for
`file_checksum` are covered separately in
[ADR-004](0004-asset-lock-checksum-facts-and-stac-projection.md).

## Alternatives Considered

- **Keep only `href`:** Simple to display, but hard to check and move because
  storage type, container, key, endpoint, and possible query secrets are mixed
  together.
- **Use rclone remote names:** Familiar to some users, but it would tie a
  package to local runtime configuration. A package should describe the object
  location without depending on a user's rclone config.
- **Store validation results in the row:** Easy to read later, but validation
  results are observations at one time. The lock should store facts about the
  expected object, not the result of a past command.

## Consequences

The lock format is easier to validate and relocate because the storage fields
are explicit. It also avoids tying the package to rclone-style config or remote
names.

Existing STAC hrefs are still accepted at package boundaries and normalized into
the structured fields.

Commands that need a display href or STAC href must rebuild it from the
structured fields.
