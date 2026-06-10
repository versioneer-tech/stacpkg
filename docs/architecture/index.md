# Architecture Overview

`stacpkg` packages a selected set of STAC Items together with a lock of the
assets those Items point to. The package stays small by default, but it can also
carry files or asset bytes when a handover needs them.

The main idea is simple:

```text
package = selected STAC Items + asset lock + optional content
```

## Package Shape

A package is a directory with fixed table names:

```text
stacpkg.pkg/
  items.parquet
  assets.lock.parquet
  <optional content>
```

`items.parquet` is the STAC metadata view. It is item-oriented: one row
represents one STAC Item.

`assets.lock.parquet` is the asset workflow view. It is asset-oriented: one row
represents one locked STAC Asset location. A single Item can therefore produce
many asset-lock rows.

Optional files can travel with the package for context, such as README files,
reports, notes, or licenses. Optional asset bytes live under `assets/` and are
still described by asset-lock rows, so packaged bytes use the same table contract
as external asset locations.

## Table Flow

The CLI moves tables between commands as Arrow IPC streams. Parquet is used for
saved checkpoints and package files.

```text
STAC JSON / STAC GeoParquet
        |
        v
items Arrow IPC stream
        |
        +-- build -> package directory
        |
        +-- asset-lock derive -> asset-lock Arrow IPC stream
                                   |
                                   v
                            assets.lock.parquet
```

This keeps command pipelines predictable. A command either reads a table stream,
writes a table stream, or writes a named package/checkpoint file.

## Storage Boundary

Package files do not store credentials. They store locations and object facts
that can be checked later:

- storage type, such as `file`, `s3`, `gs`, `az`, `http`, or `https`;
- bucket, container, HTTP origin, or file path information;
- optional endpoint information for S3-compatible stores;
- object facts such as size, ETag, and last modified time when available.

The runtime environment supplies access. That can be anonymous access,
environment variables, cloud profiles, workload identity, mounted secrets, or
bucket-scoped S3 configuration.

`obstore` is the shared object-store boundary for the current implementation.
It provides the common `head`, `get`, and `put` operations used to derive
metadata, validate assets, and relocate bytes. Validation compares the current
store facts with `assets.lock.parquet`; it does not store validation results in
the lock and it does not replace the transfer guarantees of the storage client.

## Asset Relocation

Relocation is split into clear steps:

```text
derive source lock -> plan or copy target lock -> write target hrefs to STAC
```

`asset-lock relocate --dry-run` creates target rows without copying bytes. A
normal relocation copies bytes and records object facts for the destination when
the store reports them.

STAC metadata is updated later from the target lock. The asset lock remains the
durable asset record, while STAC alternates or primary hrefs are views that make
the target locations useful to STAC clients.

## OCI Distribution

Packages can be pushed and pulled through OCI registries. The OCI artifact uses
typed layers for the required tables, optional file ZIPs, and packaged asset
bytes. The fixed filenames and layer media types are the package contract; no
separate `manifest.json` file is required.

## Decision Map

The ADRs record the durable design choices behind the current package:

| ADR | Topic |
| --- | --- |
| [ADR-001](0001-arrow-ipc-streams-for-pipelines-and-cli.md) | Arrow IPC streams are the CLI and table pipeline boundary. |
| [ADR-002](0002-package-files-and-manifest.md) | Packages use fixed files and typed OCI layers. |
| [ADR-003](0003-asset-lock-row-structure.md) | Asset-lock rows use structured storage fields instead of one stored `href`. |
| [ADR-004](0004-asset-lock-checksum-facts-and-stac-projection.md) | Draft checksum support keeps checksum facts in the asset lock first. |
| [ADR-005](0005-asset-lock-driven-stac-projection.md) | STAC asset hrefs and alternates are projected from matching asset-lock rows. |
