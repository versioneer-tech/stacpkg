# stacpkg

`stacpkg` is an Apache Arrow-native CLI and library for turning a selected set
of STAC Items into a reproducible package. It supports both package
distribution and asset relocation: packages can be pushed and pulled through
OCI registries, and referenced assets can be relocated to new locations and
recorded back into STAC metadata. Through `assets.lock.parquet`, external asset
references become verifiable assets: STAC Assets with locked locations and
object facts that can be checked, relocated, and handed over later.

At a high level, a package captures three things: the selected STAC Items, the
asset evidence needed to make external references checkable, and any optional
context files such as reports, licenses, or bundled asset bytes. That package
can then be inspected, transferred through OCI, or used as the starting point
for relocating assets.

The canonical package is a directory:

```text
stacpkg.pkg/
  items.parquet
  assets.lock.parquet
  <optional content>      # included files or bundled asset bytes
```

`items.parquet` keeps STAC metadata for selected Items. `assets.lock.parquet`
keeps the package asset lock, turning referenced STAC Assets into verifiable
assets by recording location and object facts such as size, ETag, and
last-modified metadata when available. Keeping these row types separate is the
main design choice: STAC GeoParquet is item-oriented, while asset facts are
asset-oriented.

## Why Packaging?

STAC searches, catalog exports, and external asset locations can change over
time. A package records the selected Items, asset references, and optional
context in one place.

Reproducible packaging has two basic choices: include asset bytes in the
package, or keep asset references and make those references verifiable.
Spatiotemporal assets, such as Earth Observation imagery, are often too large or
distributed to embed directly, so `stacpkg` keeps a compact package and records
the asset facts needed to audit, validate, relocate, and publish alternate
references. These locked rows are the package's verifiable assets. When
distribution needs a self-contained package, assets may also be bundled.

## Why Relocate Assets?

Workflows may need to do more than cache assets and truly relocate them. Common
reasons include:

- long-term durability when upstream assets might move or be deleted;
- faster or data-local processing near compute;
- lower egress costs and fewer repeated provider reads;
- controlled credential boundaries and recipient-specific access;
- governance, data residency, and audit requirements;
- cross-party handover, offline delivery, and provider outage isolation.

`stacpkg` can relocate assets and record relocated locations in asset lock rows.
It can project those locations back into STAC metadata, either updating the main
references or keeping the original references visible through STAC alternate
asset hrefs.

When packages move through OCI, `stacpkg` publishes typed layers rather than a
separate metadata file: `application/vnd.stacpkg.items.v1.parquet`,
`application/vnd.stacpkg.asset-lock.v1.parquet`,
`application/vnd.stacpkg.files.v1+zip` for optional directories, and
`application/vnd.stacpkg.asset.v1` or `application/vnd.stacpkg.asset.v1+zip`
for materialized asset bytes.

Start with [Create STAC Package](tutorials/create-stac-package.md) for a guided
package creation example, then see [Relocate Assets](tutorials/relocate-assets.md)
for an example relocation workflow.

## Table Pipeline Model

The core package path is deliberately table-oriented:

```text
STAC JSON / STAC GeoParquet
        |
        v
Arrow RecordBatch stream
        |
        v
Parquet items and asset-lock tables
```

CLI commands compose as Arrow IPC pipelines first. Parquet files enter and leave
pipelines through explicit `from-parquet` and `to-parquet` adapter commands.
Those adapters preserve RecordBatch flow: `from-parquet` iterates Parquet
batches, and `to-parquet` writes incoming IPC batches without loading the whole
file or stream as one Arrow table. `build` takes its package directory as a
positional argument and reads items streams from stdin.

## Current Scope

- Read STAC Item and ItemCollection JSON.
- Read and write STAC GeoParquet-style items tables.
- Create one asset lock row per STAC asset, with object facts for later validation.
- Pass STAC items and asset-lock tables through Arrow IPC pipelines.
- Materialize pipeline results explicitly as STAC GeoParquet tables, asset-lock
  Parquet tables, or package directories.
- Relocate bytes between source and destination asset locations.
- Project relocated hrefs back into STAC items metadata.
- Build package directories with items tables, asset locks, and optional
  content.
- Inspect packages as YAML, JSON, or Markdown.
- Push and pull packages through OCI registries with `oras-py`.
