# Architecture and scope

stacpkg starts with published STAC data. Take a fixed selection, move it, and
check that its metadata and asset contents still describe the same dataset.

## Scope

- Move data close to edge workloads without rebuilding the source catalog.
- Prepare repeatable inputs for secure or isolated environments.
- Stage workshop data once so participants can use a prepared copy.
- Detect changed or incomplete assets against a trusted snapshot.
- Support local files and object storage without requiring a registry.
- Transfer package files through OCI when a registry is useful.

## Design decisions

- [Package data and table pipelines](0001-package-and-pipelines.md): package
  files, STAC GeoParquet, Collection metadata, and Arrow IPC.
- [Relocation and validation](0002-relocation-and-validation.md): source
  locations, copy checks, checksums, and validation results.
- [Package distribution](0003-distribution-and-evidence.md): OCI transport,
  package contents, and registry authentication.
