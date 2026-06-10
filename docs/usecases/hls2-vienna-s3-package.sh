#!/usr/bin/env bash
set -euo pipefail

# title: HLS2 Vienna S3 Package

# Prerequisite: ensure public Planetary Computer access, `rustac`, and one local S3 store at `http://127.0.0.1:19000` are available; no registry is required.

run_id="${STACPKG_USECASE_RUN_ID:-manual}"

# ## Search HLS2 L30 and S30 over Vienna with rustac
# L30 uses the Planetary Computer STAC API directly. S30 uses rustac against the signed
# Planetary Computer GeoParquet weekly export, then resolves the matching STAC Item by id.
rustac --output-format ndjson search https://planetarycomputer.microsoft.com/api/stac/v1 \
  - --collections hls2-l30 --bbox 16.2,48.1,16.5,48.3 \
  --datetime 2025-06-01T00:00:00Z/2025-06-30T23:59:59Z --max-items 1 \
  > hls2-vienna.ndjson

# ## Materialize the selected STAC Items as GeoParquet
cat hls2-vienna.ndjson \
  | stacpkg items from-ndjson \
  | stacpkg items to-parquet hls2-vienna.items.parquet
# test-assert: parquet-rows hls2-vienna.items.parquet 3
# test-assert: parquet-columns hls2-vienna.items.parquet id geometry assets bbox datetime

# ## Stream the selected STAC Items into an asset lock
cat hls2-vienna.ndjson \
  | stacpkg items from-ndjson \
  | stacpkg asset-lock derive --asset-keys B02 --asset-keys B03 --asset-keys B04 \
  --asset-keys thumbnail --asset-keys tilejson --asset-keys rendered_preview \
  | stacpkg asset-lock to-parquet hls2-vienna.source.assets.lock.parquet
# test-assert: parquet-rows hls2-vienna.source.assets.lock.parquet 3
# test-assert: asset-lock-keys hls2-vienna.source.assets.lock.parquet thumbnail
# test-assert: asset-lock-store hls2-vienna.source.assets.lock.parquet file

# ## Build a source package without temporary SAS query strings
stacpkg items from-parquet hls2-vienna.items.parquet \
  | stacpkg build 01-hls2-vienna-source-package/ \
  --asset-lock <(stacpkg asset-lock from-parquet hls2-vienna.source.assets.lock.parquet)
# test-assert: package-items 01-hls2-vienna-source-package 3
# test-assert: package-assets 01-hls2-vienna-source-package 3
# test-assert: asset-lock-store 01-hls2-vienna-source-package/assets.lock.parquet file

# ## Relocate HLS2 assets into local MinIO
stacpkg asset-lock from-parquet hls2-vienna.source.assets.lock.parquet \
  | stacpkg asset-lock relocate --store-type s3 \
  --store-container stacpkg-e2e-s3store1 \
  --store-endpoint-url http://127.0.0.1:19000 \
  --key "hls2-vienna-runs/${run_id}/" \
  | stacpkg asset-lock to-parquet hls2-vienna.s3.assets.lock.parquet
# test-assert: parquet-rows hls2-vienna.s3.assets.lock.parquet 3
# test-assert: asset-lock-store hls2-vienna.s3.assets.lock.parquet s3 --container stacpkg-e2e-s3store1 --key-prefix hls2-vienna-runs/pytest/

# ## Bundle the HLS2 items with local S3 primary asset hrefs
stacpkg items from-parquet hls2-vienna.items.parquet \
  | stacpkg items add-alternate \
  --asset-lock <(stacpkg asset-lock from-parquet hls2-vienna.s3.assets.lock.parquet) \
  --alternate-key original --alternate-name original \
  | stacpkg items promote-alternate --alternate-key original --mode switch \
  | stacpkg build 02-hls2-vienna-local-s3-package/ \
  --asset-lock <(stacpkg asset-lock from-parquet hls2-vienna.s3.assets.lock.parquet)
# test-assert: package-items 02-hls2-vienna-local-s3-package 3
# test-assert: package-assets 02-hls2-vienna-local-s3-package 3
# test-assert: asset-lock-store 02-hls2-vienna-local-s3-package/assets.lock.parquet s3 --container stacpkg-e2e-s3store1 --key-prefix hls2-vienna-runs/pytest/
# test-assert: item-asset-hrefs 02-hls2-vienna-local-s3-package/items.parquet s3://stacpkg-e2e-s3store1/hls2-vienna-runs/pytest/ --asset-key thumbnail
