# ADR-003: Package distribution

## Status

Accepted and implemented.

## Decision

Keep local packages usable without OCI. Use `oras-py` for optional registry
push and pull. Artifacts carry typed layers for the Items table, asset
lock, optional file archives, and included assets. Descriptors record digests,
sizes, media types, and safe relative paths. Reject paths that escape the output
directory. Check each downloaded layer against its digest and size before
using it. Stage the package and publish the output directory after all required
files pass. Reject duplicate package paths.

Both tables are required in the OCI artifact. Included asset bytes
travel with it; externally referenced assets stay at their recorded locations.
Preserve every included file during transport.
The two required table paths are reserved for package tables.
Push uploads package files. Pull checks package layers. Use `validate` to check
the assets after delivery.

Use an empty OCI config. Package information belongs in the files and layer
descriptors. Runtime configuration supplies registry credentials. Expose the
oras authentication backend, defaulting to `token`; `basic`, plain HTTP, and
TLS certificate checks are independent choices.

## Reason

Local and registry delivery serve different environments. OCI provides content
digests for the delivered package files.

Reference: [OCI distribution](https://github.com/opencontainers/distribution-spec/blob/main/spec.md).
