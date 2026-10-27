# stacpkg

Copy assets from an existing STAC selection, check the destination, and keep
the new hrefs, source locations, and file facts in STAC GeoParquet.

A package holds Item metadata and an asset lock. Asset bytes stay outside it
unless included with `build --include-assets`. OCI distribution is optional.
See the [package format](architecture/0001-package-and-pipelines.md) for file roles.

## Start here

- [Relocate assets](tutorials/relocate-assets.md): copy a selection to another location.
- [Create and share a package](tutorials/create-stac-package.md): keep existing locations or include files for offline use.

## Reference

- [CLI](reference-guides/cli.md): commands, options, and defaults.
- [Items](reference-guides/items.md): STAC GeoParquet and embedded Collections.
- [Asset lock](reference-guides/asset-lock.md): locations, checksums, and validation results.

See [Architecture](architecture/index.md) for the package and transfer design.
