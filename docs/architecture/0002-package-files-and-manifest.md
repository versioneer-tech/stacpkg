# ADR-002: Package Files and OCI Layers

| Status | Date | Implementation |
| --- | --- | --- |
| Accepted | 2026-05-16 | Directory package implemented as of `0.1.0`. |

## Context

`stacpkg` needs a package mechanism that can be inspected and moved later. The
package must keep:

- selected STAC Item metadata;
- an asset-lock table with asset locations and object facts;
- optional included files, such as reports or licenses;
- optional asset bytes when a package should carry the data itself.

The package should be reproducible: the same inputs should produce the same
package contents. It must also avoid credentials. It records object locations
and the object facts supported by the asset-lock schema; fields such as media
type and checksum can be added by later schema revisions. Access tokens,
passwords, signed URLs, and other secrets must stay outside the package.

Package consumers should be able to inspect an OCI artifact before pulling all
content. OCI descriptors already expose layer media type, digest, size, and
string annotations, so the package should use those fields instead of adding a
second metadata file that repeats the same information.

## Decision

Use fixed package files for the required tables:

```text
stacpkg.pkg/
  items.parquet
  assets.lock.parquet
  <optional content>
  assets/
```

`items.parquet` is always present. It contains one row per selected STAC Item.

`assets.lock.parquet` is always present. It contains the package asset-lock
table. If the caller does not provide an asset-lock table, `stacpkg` creates one
from the selected Items. The table may be empty when the selected Items have no
lockable assets.

The table files have fixed package-relative names. Table schema kind and version
are stored in the Parquet schema metadata.

Optional included content is not a source of package metadata. A single
included file keeps its package-relative filename. An included directory is
represented as a ZIP archive whose entries preserve the package-relative
directory layout.

Optional asset bytes are distinct from optional included content. Asset bytes
copied into the package live under `assets/` in the local package. Their
identity and package-relative location stay in `assets.lock.parquet`, with
`store_type=file` and `key` pointing at the package path.

Use typed OCI layers for registry transport. No separate metadata JSON file or
custom OCI config JSON is required to rebuild the package.

OCI image manifests require a config descriptor. For now, `stacpkg` uses the
OCI empty JSON config descriptor and does not put authoritative package
information in that config. The package information lives in fixed file names,
Parquet schema metadata, OCI layer media types, descriptor annotations, and ZIP
entries. This avoids a custom STAC package config blob until package metadata
appears that cannot be represented without meaningful duplication.

The OCI artifact uses:

```text
artifactType: application/vnd.stacpkg.package.v1+json
config.mediaType: application/vnd.oci.empty.v1+json
```

Required table layers:

```text
application/vnd.stacpkg.items.v1.parquet
  extracts as items.parquet

application/vnd.stacpkg.asset-lock.v1.parquet
  extracts as assets.lock.parquet
```

Every layer records its safe package-relative path with the OCI
`org.opencontainers.image.title` annotation.

Optional directory content uses ZIP:

```text
application/vnd.stacpkg.files.v1+zip
```

Copied package assets use separate asset layers so they remain distinguishable
from generic optional files:

```text
application/vnd.stacpkg.asset.v1
application/vnd.stacpkg.asset.v1+zip
```

For single asset blobs, `org.opencontainers.image.title` records the safe
package-relative asset path. For asset ZIP layers, the ZIP entries preserve
package-relative `assets/` paths. Consumers must reject unsafe paths such as
absolute paths and `..` parent traversal.

Package files must not contain credentials. Source and target access stays in
the runtime environment: cloud profiles, environment variables, workload
identity, mounted secrets, or provider-specific runtime configuration.

## Alternatives Considered

- **STAC Catalog directory only:** Familiar to STAC users, but it does not
  provide an asset-lock table or a package-level table layout.
- **Manifest-only package:** Small and easy to inspect, but it pushes all item
  and asset-lock data into JSON and loses the efficient table files.
- **Package metadata JSON as OCI source of truth:** Explicit, but it duplicates
  data already present in fixed filenames, Parquet schema metadata, OCI
  descriptors, descriptor annotations, and ZIP entries.
- **Store credentials in the package:** Packages could be self-contained for
  immediate access, but this is unsafe to share. Authentication belongs to the
  runtime environment.

## Consequences

The package is easy to inspect, archive, hash, and move through OCI or other
package stores. Consumers can identify the required tables from fixed local
filenames or OCI layer media types without guessing.

OCI artifacts expose the package shape before pull: readers can see whether the
artifact has item metadata, an asset lock, optional file archives, and copied
asset layers from descriptor media types and annotations.

The package does not include credentials. Workflows that need source or target
access must provide credentials at execution time.
