#!/usr/bin/env bash
set -euo pipefail

# title: OpenAerialMap Provider Package with Asset Bytes
# test: test_openaerialmap_provider_package_with_asset_bytes

# Prerequisite: ensure the local OpenAerialMap fixtures are available; no S3 stores, registry, Docker, or Kubernetes are required.

# ## Create a small OpenAerialMap source selection
# test-setup: openaerialmap-items openaerialmap-2025.items.parquet --item-count 3
# test-assert: parquet-rows openaerialmap-2025.items.parquet 3

# ## Filter the materialized OpenAerialMap provider selection
stacpkg items from-parquet openaerialmap-2025.items.parquet --providers ODM \
  | stacpkg items to-parquet openaerialmap-provider.items.parquet
# test-assert: parquet-rows openaerialmap-provider.items.parquet 1
# test-assert: item-provider-names openaerialmap-provider.items.parquet ODM

# ## Derive a metadata-reuse asset lock
stacpkg items from-parquet openaerialmap-provider.items.parquet \
  | stacpkg asset-lock derive --no-probe-metadata \
  | stacpkg asset-lock to-parquet openaerialmap-provider.metadata.assets.lock.parquet
# test-assert: parquet-rows openaerialmap-provider.metadata.assets.lock.parquet 1
# test-assert: asset-lock-keys openaerialmap-provider.metadata.assets.lock.parquet thumbnail
# test-assert: asset-lock-store openaerialmap-provider.metadata.assets.lock.parquet file

# ## Derive a lock with local object metadata
stacpkg items from-parquet openaerialmap-provider.items.parquet \
  | stacpkg asset-lock derive \
  | stacpkg asset-lock to-parquet openaerialmap-provider.object.assets.lock.parquet
# test-assert: parquet-rows openaerialmap-provider.object.assets.lock.parquet 1
# test-assert: asset-lock-keys openaerialmap-provider.object.assets.lock.parquet thumbnail
# test-assert: asset-lock-store openaerialmap-provider.object.assets.lock.parquet file

# ## Build a self-contained stacpkg package with asset bytes
stacpkg items from-parquet openaerialmap-provider.items.parquet \
  | stacpkg build openaerialmap-provider.pkg \
  --asset-lock <(stacpkg asset-lock from-parquet openaerialmap-provider.object.assets.lock.parquet) \
  --include-assets
# test-assert: package-items openaerialmap-provider.pkg 1
# test-assert: package-assets openaerialmap-provider.pkg 1
# test-assert: asset-lock-store openaerialmap-provider.pkg/assets.lock.parquet file --key-prefix assets/
# test-assert: package-asset-files openaerialmap-provider.pkg 1
# test-assert: no-file openaerialmap-provider.pkg/manifest.json
