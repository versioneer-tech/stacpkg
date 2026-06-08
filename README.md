# stacpkg

**Reproducible STAC packages for handoff, audit, verification, and relocation.**

`stacpkg` turns selected STAC Items into a compact package that another
environment can inspect, validate, share, and move. It is for the moment after a
STAC search, when "these are the items" needs to become a durable artifact.

```text
package = selected STAC Items + verifiable asset lock + optional content
```

The package keeps two required tables:

- `items.parquet`: the selected STAC Items as a STAC GeoParquet-style table.
- `assets.lock.parquet`: one row per locked STAC Asset, with structured
  location fields and observed object facts such as size, ETag, and last
  modified time when available.

With that lock in place, you can verify that referenced assets still match,
relocate assets into controlled storage, enrich STAC metadata with alternate
hrefs, and move the package through an OCI registry.

## Install

```bash
pip install stacpkg
```

The quickstart below uses `curl` against a STAC API. `stacpkg` can start from
STAC JSON, STAC NDJSON, or existing STAC GeoParquet.

## Quickstart

Search the OpenAerialMap STAC API for two Austria Items and build a package:

```bash
tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/stacpkg-openaerialmap-austria.XXXXXX")
bbox='16.415,47.1705,16.431,47.734'

curl -fsS https://api.imagery.hotosm.org/stac/search \
  --header "Accept: application/geo+json" \
  --header "Content-Type: application/json" \
  --data-binary "{
    \"collections\": [\"openaerialmap\"],
    \"bbox\": [$bbox],
    \"sortby\": [{\"field\": \"start_datetime\", \"direction\": \"asc\"}],
    \"limit\": 2
  }" \
  | stacpkg items from-json \
  | stacpkg build "$tmpdir/openaerialmap-austria.pkg"

echo "created $tmpdir/openaerialmap-austria.pkg"
```

This `curl` example keeps the request intentionally small and does not page
through all matches. For larger catalogs, use a scalable STAC client such as
[`rustac`](https://github.com/stac-utils/rustac) to stream newline-delimited
STAC Items into `stacpkg items from-ndjson`.

Sample output:

```text
created /tmp/stacpkg-openaerialmap-austria.ABC123/openaerialmap-austria.pkg
```

The package is just files on disk:

```text
/tmp/stacpkg-openaerialmap-austria.ABC123/openaerialmap-austria.pkg/
  items.parquet
  assets.lock.parquet
```

Inspect it:

```bash
stacpkg inspect "$tmpdir/openaerialmap-austria.pkg" --format markdown
```

Sample output:

```markdown
# stacpkg Inspect

- Package: `/tmp/stacpkg-openaerialmap-austria.ABC123/openaerialmap-austria.pkg`
- Items: 2
- Collections: openaerialmap
- Assets: 4
- Asset keys: thumbnail, visual
- Known asset bytes: 16750068
```

## Verify Assets

Validate the package asset lock against the current live objects:

```bash
stacpkg asset-lock from-parquet "$tmpdir/openaerialmap-austria.pkg/assets.lock.parquet" \
  | stacpkg asset-lock validate
```

Sample output:

```json
{"asset_key":"thumbnail","errors":[],"item_id":"631ee6653cdf1c0006b63c5b","store_type":"https","valid":true}
{"asset_key":"visual","errors":[],"item_id":"631ee6653cdf1c0006b63c5b","store_type":"https","valid":true}
```

Validation prints JSON lines and exits non-zero when an asset no longer matches
the locked facts.

## Relocate Assets

Copy locked assets into storage you control and write a new asset lock for the
relocated locations:

```bash
mkdir -p "$tmpdir/local-assets"

stacpkg asset-lock from-parquet "$tmpdir/openaerialmap-austria.pkg/assets.lock.parquet" \
  | stacpkg asset-lock relocate \
      --source-prefix https://oin-hotosm-temp.s3.amazonaws.com/ \
      --store-type file \
      --key "$tmpdir/local-assets/" \
      --max-workers 4 \
      --memory-limit-bytes 512MiB \
      --chunk-size-bytes 8MiB \
  | stacpkg asset-lock to-parquet \
      "$tmpdir/openaerialmap-austria.local.assets.lock.parquet"

echo "created $tmpdir/openaerialmap-austria.local.assets.lock.parquet"
```

Sample output:

```text
created /tmp/stacpkg-openaerialmap-austria.ABC123/openaerialmap-austria.local.assets.lock.parquet
```

Validate the relocated files the same way:

```bash
stacpkg asset-lock from-parquet "$tmpdir/openaerialmap-austria.local.assets.lock.parquet" \
  | stacpkg asset-lock validate
```

## Common Flows

- Start from a STAC API search, package selected Items, and keep the exact
  package inputs.
- Verify remote assets before a run, handoff, or audit.
- Relocate referenced assets into S3-compatible, local, or other object-store
  locations.
- Enrich STAC Items with File Info and Alternate Assets fields from an asset
  lock.
- Push and pull packages through OCI registries.

## Docs

- [Documentation](https://stacpkg.versioneer.at/)
- [Tutorial - Create STAC Package](https://stacpkg.versioneer.at/tutorials/create-stac-package/)
- [Tutorial - Relocate Assets](https://stacpkg.versioneer.at/tutorials/relocate-assets/)
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
