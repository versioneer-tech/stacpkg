#!/usr/bin/env bash
set -euo pipefail

# title: OpenAerialMap Reproducible Data Inputs

# Prerequisite: ensure one local S3 store is running at `http://127.0.0.1:19000`; no registry is required.

# ## Create a small OpenAerialMap source selection
# The workflow uses tiny local OpenAerialMap thumbnail fixtures so rendered examples stay readable and reproducible.
# test-setup: openaerialmap-items openaerialmap.items.parquet --item-count 3

# ## Build the first package from that subset
stacpkg items from-parquet openaerialmap.items.parquet \
  | stacpkg build first.pkg/
# test-assert: package-items first.pkg 3
# test-assert: package-assets first.pkg 3
# test-assert: asset-lock-keys first.pkg/assets.lock.parquet thumbnail
# test-assert: asset-lock-store first.pkg/assets.lock.parquet file

# ## Relocate fixture assets into local MinIO
stacpkg items from-parquet openaerialmap.items.parquet \
  | stacpkg asset-lock derive \
  | stacpkg asset-lock to-parquet source.assets.lock.parquet

stacpkg asset-lock from-parquet source.assets.lock.parquet \
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

# ## Lock relocated source assets with object metadata
stacpkg items from-parquet openaerialmap-s3-store-selection.items.parquet \
  | stacpkg asset-lock derive \
  | stacpkg asset-lock to-parquet source.assets.lock.parquet
# test-assert: parquet-rows source.assets.lock.parquet 3
# test-assert: asset-lock-store source.assets.lock.parquet s3 --container s3store1 --key-prefix reproducible-inputs/

# ## Build a reproducible package from the locked inputs
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
