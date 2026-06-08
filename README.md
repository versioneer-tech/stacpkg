# stacpkg

**Reproducible STAC packages for handoff, audit, and relocation.**

`stacpkg` turns a selected set of STAC Items into a compact package that can be
inspected, validated, shared, and moved between environments.

It is for the moment after a STAC search, when "these are the items" needs to
become a durable artifact:

- snapshot selected Items as `items.parquet`;
- lock referenced assets in `assets.lock.parquet`;
- ship the result as a package directory or OCI artifact.

## Install

```bash
pip install stacpkg
```

## Start

Build your first package from a STAC GeoParquet items table:

```bash
stacpkg items from-parquet source.items.parquet \
  | stacpkg build stacpkg.pkg/
```

Inspect the package:

```bash
stacpkg inspect stacpkg.pkg/
```

For available commands:

```bash
stacpkg --help
```

## Docs

- [Documentation](https://stacpkg.versioneer.at/)
- [Create STAC Package](https://stacpkg.versioneer.at/tutorials/create-stac-package/)
- [Relocate Assets](https://stacpkg.versioneer.at/tutorials/relocate-assets/)
- [CLI Reference](https://stacpkg.versioneer.at/reference-guides/cli/)
- [Items Reference](https://stacpkg.versioneer.at/reference-guides/items/)
- [Asset Lock Reference](https://stacpkg.versioneer.at/reference-guides/asset-lock/)

## Development Commands

Use the repository `Makefile` as the source of truth for local quality gates:

- `make sync`: install all dependency groups.
- `make pre-commit`: run formatting, lint, and metadata checks.
- `make test-unit`: run fast unit tests.
- `make test-integration`: run optional local cross-tool integration tests.
- `make test-e2e`: run the CI-sized kind/MinIO/registry e2e suite.
- `make test-e2e-full`: run all e2e tests, including performance checks.
- `make test-all`: run pre-commit, docs, unit, integration, and full e2e gates.

## License

Apache 2.0 (Apache License Version 2.0, January 2004)  
<https://www.apache.org/licenses/LICENSE-2.0>
