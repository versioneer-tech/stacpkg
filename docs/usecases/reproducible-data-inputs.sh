#!/usr/bin/env bash
set -euo pipefail

# title: OpenAerialMap Reproducible Data Inputs
# test: none

# Prerequisite: ensure one local S3 store is running at `http://127.0.0.1:19000`; no registry is required.

# ## Create a small OpenAerialMap source selection
# The workflow uses tiny local OpenAerialMap thumbnail fixtures so rendered examples stay readable and reproducible.

# ## Build the first package from that subset
stacpkg items from-parquet openaerialmap.items.parquet \
  | stacpkg build first.pkg/

# ## Relocate fixture assets into local MinIO
stacpkg items from-parquet openaerialmap.items.parquet \
  | stacpkg asset-lock derive \
  | stacpkg asset-lock to-parquet source.assets.lock.parquet

stacpkg asset-lock from-parquet source.assets.lock.parquet \
  | stacpkg asset-lock relocate \
  --store-type s3 --store-container s3store1 --key reproducible-inputs/ \
  --store-endpoint-url http://127.0.0.1:19000 \
  | stacpkg asset-lock to-parquet openaerialmap-s3.asset-lock.parquet

stacpkg items from-parquet openaerialmap.items.parquet \
  | stacpkg items add-alternate \
  --asset-lock openaerialmap-s3.asset-lock.arrow \
  --alternate-key original --alternate-name s3 \
  | stacpkg items promote-alternate \
  --alternate-key original --mode switch \
  | stacpkg items to-parquet openaerialmap-s3-store-selection.items.parquet

# ## Lock relocated source assets with object metadata
stacpkg items from-parquet openaerialmap-s3-store-selection.items.parquet \
  | stacpkg asset-lock derive \
  | stacpkg asset-lock to-parquet source.assets.lock.parquet

# ## Build a reproducible package from the locked inputs
stacpkg items from-parquet openaerialmap-s3-store-selection.items.parquet \
  | stacpkg build package/ \
  --asset-lock source.assets.lock.arrow

# ## Validate, enrich, and inspect the package
stacpkg asset-lock from-parquet package/assets.lock.parquet \
  | stacpkg asset-lock validate

stacpkg items from-parquet package/items.parquet \
  | stacpkg items enrich --asset-lock package/assets.lock.arrow \
  | stacpkg items to-parquet enriched.items.parquet

stacpkg inspect package/ --format markdown
