#!/usr/bin/env bash
set -euo pipefail

# title: OpenAerialMap S3 Alternate Package

# Prerequisite: ensure unsigned public S3 access is available; no local S3 stores or registry are required.

# ## Sample OpenAerialMap Items And Promote S3 Alternates
# The source fixture stores HTTP/HTTPS asset hrefs plus `alternate.s3.href` values.
# The S3-only fixture is prepared with `stacpkg items promote-alternate --alternate-key s3`
# so this usecase starts from primary public S3 asset hrefs.

# ## Lock And Validate Public S3 Object Metadata
# The test runs this with unsigned public S3 access.
# test-setup: openaerialmap-s3-items openaerialmap-s3-reachable.items.parquet --item-count 3
stacpkg items from-parquet openaerialmap-s3-reachable.items.parquet \
  | stacpkg asset-lock derive \
  | stacpkg asset-lock to-parquet openaerialmap-s3.object.assets.lock.parquet
# test-assert: parquet-rows openaerialmap-s3.object.assets.lock.parquet 6
# test-assert: item-asset-hrefs openaerialmap-s3-reachable.items.parquet s3://oin-hotosm-temp/
# test-assert: asset-lock-keys openaerialmap-s3.object.assets.lock.parquet thumbnail visual
# test-assert: asset-lock-store openaerialmap-s3.object.assets.lock.parquet s3 --container oin-hotosm-temp

# ## Build A Package From The S3-Backed Selection
stacpkg items from-parquet openaerialmap-s3-reachable.items.parquet \
  | stacpkg build openaerialmap-s3.pkg \
  --asset-lock <(stacpkg asset-lock from-parquet openaerialmap-s3.object.assets.lock.parquet)
# test-assert: package-items openaerialmap-s3.pkg 3
# test-assert: package-assets openaerialmap-s3.pkg 6
# test-assert: asset-lock-store openaerialmap-s3.pkg/assets.lock.parquet s3 --container oin-hotosm-temp
