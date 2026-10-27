# ADR-001: Package data and table pipelines

## Status

Accepted and implemented.

## Decision

Use STAC GeoParquet for saved Item metadata and Arrow IPC between table
commands. The CLI calls the same Python operations that notebooks use.
Input adapters read selected Items.

A local package is a directory. The `.pkg` suffix is a name convention.

| Path | Role | When present |
| --- | --- | --- |
| `items.parquet` | Selected STAC Items and embedded Collection documents. | Required. |
| `assets.lock.parquet` | Asset locations and file facts used for checks. | Required in built and relocated packages. |
| `assets/` | Copied asset bytes with relative hrefs in the Items table. | With `build --include-assets`. |
| Other files or directories | Context such as a license or handover notes. | With `build --includes PATH`. |

By default, assets remain at the hrefs recorded in the tables. `relocate`
copies them to its separate `--target` location. For an offline handover,
include asset bytes with `build --include-assets`.

Preserve Item property and extension fields, GeoParquet metadata, and complete
Collection documents. Collections retain the source dataset description and
extent; a selection does not recalculate them.

`assets.lock.parquet` has one row per selected Item asset location, identified
by `item_id` and `asset_key`. Store structured locations and nullable file facts.
Item IDs must be unique across the selection. The lock supplies checked file
facts and copied locations for projection into Item assets.
Keep credentials and check results outside the lock. The
[Asset Lock reference](../reference-guides/asset-lock.md) defines its fields.

Validation results go to stdout as JSON Lines.

Included assets use relative file paths. Their size and checksum describe the
included copy. Original locations remain as alternates in Item metadata.

Adapters and Item transforms process Arrow batches. Operations that need the
whole selection, such as copy planning and package validation, use tables.
Update the output schema before writing fields added by a transform.
JSON readers keep fields from all Items in a batch. Once an NDJSON stream has
its schema, reject new fields or changed types rather than discard them.

## Reason

STAC clients can read the metadata directly. Arrow retains field types and
schema metadata between operations. Fixed filenames make a package usable on
local storage without a registry or a separate package manifest.
