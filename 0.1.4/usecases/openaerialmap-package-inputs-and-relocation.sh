#!/usr/bin/env bash
set -euo pipefail

# title: OpenAerialMap Package Inputs and Relocation

# Prerequisite: ensure the local OpenAerialMap fixtures are available, unsigned public S3 access is available, and one local S3 store is running at `http://127.0.0.1:19000`; no registry is required.

# ## Prepare reusable OpenAerialMap source selections
# The local fixture keeps examples reproducible, while the public S3 fixture shows how provider alternates can become package inputs without copying bytes.
# test-setup: openaerialmap-items openaerialmap.items.parquet --item-count 3
# test-setup: openaerialmap-s3-items openaerialmap-public-s3.items.parquet --item-count 3

# ## Filter a provider subset and build it with embedded local asset bytes
stacpkg items from-parquet openaerialmap.items.parquet --providers ODM \
  | stacpkg items to-parquet openaerialmap-provider.items.parquet
# test-assert: parquet-rows openaerialmap-provider.items.parquet 1
# test-assert: item-provider-names openaerialmap-provider.items.parquet ODM

stacpkg items from-parquet openaerialmap-provider.items.parquet \
  | stacpkg asset-lock derive --no-probe-metadata \
  | stacpkg asset-lock to-parquet openaerialmap-provider.metadata.assets.lock.parquet
# test-assert: parquet-rows openaerialmap-provider.metadata.assets.lock.parquet 1
# test-assert: asset-lock-keys openaerialmap-provider.metadata.assets.lock.parquet thumbnail
# test-assert: asset-lock-store openaerialmap-provider.metadata.assets.lock.parquet file

stacpkg items from-parquet openaerialmap-provider.items.parquet \
  | stacpkg asset-lock derive \
  | stacpkg asset-lock to-parquet openaerialmap-provider.object.assets.lock.parquet
# test-assert: parquet-rows openaerialmap-provider.object.assets.lock.parquet 1
# test-assert: asset-lock-keys openaerialmap-provider.object.assets.lock.parquet thumbnail
# test-assert: asset-lock-store openaerialmap-provider.object.assets.lock.parquet file

stacpkg items from-parquet openaerialmap-provider.items.parquet \
  | stacpkg build openaerialmap-provider-assets.pkg \
  --asset-lock <(stacpkg asset-lock from-parquet openaerialmap-provider.object.assets.lock.parquet) \
  --include-assets
# test-assert: package-items openaerialmap-provider-assets.pkg 1
# test-assert: package-assets openaerialmap-provider-assets.pkg 1
# test-assert: asset-lock-store openaerialmap-provider-assets.pkg/assets.lock.parquet file --key-prefix assets/
# test-assert: package-asset-files openaerialmap-provider-assets.pkg 1
# test-assert: no-file openaerialmap-provider-assets.pkg/manifest.json

# ## Package public S3 alternates without copying asset bytes
stacpkg items from-parquet openaerialmap-public-s3.items.parquet \
  | stacpkg asset-lock derive \
  | stacpkg asset-lock to-parquet openaerialmap-public-s3.assets.lock.parquet
# test-assert: parquet-rows openaerialmap-public-s3.assets.lock.parquet 6
# test-assert: item-asset-hrefs openaerialmap-public-s3.items.parquet s3://oin-hotosm-temp/
# test-assert: asset-lock-keys openaerialmap-public-s3.assets.lock.parquet thumbnail visual
# test-assert: asset-lock-store openaerialmap-public-s3.assets.lock.parquet s3 --container oin-hotosm-temp

stacpkg items from-parquet openaerialmap-public-s3.items.parquet \
  | stacpkg build openaerialmap-public-s3.pkg \
  --asset-lock <(stacpkg asset-lock from-parquet openaerialmap-public-s3.assets.lock.parquet)
# test-assert: package-items openaerialmap-public-s3.pkg 3
# test-assert: package-assets openaerialmap-public-s3.pkg 6
# test-assert: asset-lock-store openaerialmap-public-s3.pkg/assets.lock.parquet s3 --container oin-hotosm-temp

# ## Relocate fixture assets into local MinIO
stacpkg items from-parquet openaerialmap.items.parquet \
  | stacpkg asset-lock derive \
  | stacpkg asset-lock to-parquet openaerialmap-local.assets.lock.parquet

stacpkg asset-lock from-parquet openaerialmap-local.assets.lock.parquet \
  | stacpkg asset-lock relocate \
  --store-type s3 --store-container s3store1 --key reproducible-inputs/ \
  --store-endpoint-url http://127.0.0.1:19000 \
  | stacpkg asset-lock to-parquet openaerialmap-s3.asset-lock.parquet
# test-assert: parquet-rows openaerialmap-s3.asset-lock.parquet 3
# test-assert: asset-lock-keys openaerialmap-s3.asset-lock.parquet thumbnail
# test-assert: asset-lock-store openaerialmap-s3.asset-lock.parquet s3 --container s3store1 --key-prefix reproducible-inputs/

stacpkg items from-parquet openaerialmap.items.parquet \
  | stacpkg items add-alternate \
  --asset-lock <(stacpkg asset-lock from-parquet openaerialmap-s3.asset-lock.parquet) \
  --alternate-key original --alternate-name s3 \
  | stacpkg items promote-alternate \
  --alternate-key original --mode switch \
  | stacpkg items to-parquet openaerialmap-s3-store-selection.items.parquet
# test-assert: parquet-rows openaerialmap-s3-store-selection.items.parquet 3
# test-assert: item-asset-hrefs openaerialmap-s3-store-selection.items.parquet s3://s3store1/reproducible-inputs/ --asset-key thumbnail

# ## Lock and package relocated source assets with object metadata
stacpkg items from-parquet openaerialmap-s3-store-selection.items.parquet \
  | stacpkg asset-lock derive \
  | stacpkg asset-lock to-parquet source.assets.lock.parquet
# test-assert: parquet-rows source.assets.lock.parquet 3
# test-assert: asset-lock-store source.assets.lock.parquet s3 --container s3store1 --key-prefix reproducible-inputs/

stacpkg items from-parquet openaerialmap-s3-store-selection.items.parquet \
  | stacpkg build package/ \
  --asset-lock <(stacpkg asset-lock from-parquet source.assets.lock.parquet)
# test-assert: package-items package 3
# test-assert: package-assets package 3
# test-assert: asset-lock-store package/assets.lock.parquet s3 --container s3store1 --key-prefix reproducible-inputs/

# ## Validate, enrich, and inspect the package
stacpkg asset-lock from-parquet package/assets.lock.parquet \
  | stacpkg asset-lock validate

stacpkg items from-parquet package/items.parquet \
  | stacpkg items enrich --asset-lock <(stacpkg asset-lock from-parquet package/assets.lock.parquet) \
  | stacpkg items to-parquet enriched.items.parquet
# test-assert: parquet-rows enriched.items.parquet 3
# test-assert: item-asset-hrefs enriched.items.parquet s3://s3store1/reproducible-inputs/ --asset-key thumbnail

stacpkg inspect package/ --format markdown
