<h1><img src="docs/imgs/logo.png" height="48" alt="Versioneer logo" /> stacpkg</h1>

[![Tests](https://github.com/versioneer-tech/stacpkg/actions/workflows/pr.yaml/badge.svg?branch=main&event=push)](https://github.com/versioneer-tech/stacpkg/actions/workflows/pr.yaml)
[![Package version](https://img.shields.io/pypi/v/stacpkg)](https://pypi.org/project/stacpkg/)
[![License](https://img.shields.io/github/license/versioneer-tech/stacpkg)](LICENSE)

stacpkg copies assets from a STAC selection and checks the copied bytes.
It keeps the Item metadata and source locations with the copy. Use it from
Python or the command line to prepare data for workloads, workshops, or
offline use.

## Use

Requires Python 3.13.

```bash
pip install stacpkg

stacpkg items from-json selection.json \
  | stacpkg relocate selection.pkg --target /data/selection/
stacpkg validate selection.pkg
```

Start with STAC Item or ItemCollection JSON from your STAC client. Relocation
checks the destination before writing the package. The package holds metadata
and an asset lock; the asset bytes are at `/data/selection/`.

For an offline handover, put the asset bytes inside the package:

```bash
stacpkg items from-json selection.json \
  | stacpkg build offline.pkg --include-assets --include-metadata-assets
```

Credentials come from runtime settings. Supply Collection JSON to keep dataset
descriptions with the selection. OCI push and pull are optional.

## Documentation

- [Create and share a package](docs/tutorials/create-stac-package.md)
- [Relocate assets](docs/tutorials/relocate-assets.md), including private S3 alternates
- [CLI reference](docs/reference-guides/cli.md) and [package format](docs/architecture/0001-package-and-pipelines.md)
- [Architecture and scope](docs/architecture/index.md)
- [Published docs](https://stacpkg.versioneer.at/latest/) and [local checks](tests/README.md)

Use `make help` for development commands. [Apache License 2.0](LICENSE).
