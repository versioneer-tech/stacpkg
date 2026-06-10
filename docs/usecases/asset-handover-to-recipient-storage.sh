#!/usr/bin/env bash
set -euo pipefail

# title: OpenAerialMap Asset Handover to Recipient Storage
# test: none

# Prerequisite: ensure two local S3 stores are running at `http://127.0.0.1:19000` and `http://127.0.0.1:19010`; no registry is required.

# ## Filter A Small OpenAerialMap Provider Subset
# The full 2025 fixture is intentionally larger than this handover test needs.
# Use `--providers` to keep a deterministic subset before deriving or relocating asset locks.
stacpkg items from-parquet openaerialmap-2025.items.parquet --providers ODM \
  | stacpkg items to-parquet openaerialmap-provider.items.parquet

# ## Lock The Provider Assets
stacpkg items from-parquet openaerialmap-provider.items.parquet \
  | stacpkg asset-lock derive \
  | stacpkg asset-lock to-parquet source.assets.lock.parquet

# ## Relocate Assets Into Local Kind MinIO
stacpkg asset-lock from-parquet source.assets.lock.parquet \
  | stacpkg asset-lock relocate \
  --store-type s3 --store-container s3store1 --key controlled-relocation/ \
  --store-endpoint-url http://127.0.0.1:19000 \
  | stacpkg asset-lock to-parquet s3store1.asset-lock.parquet

# ## Promote Relocated MinIO Hrefs Back Into STAC Items
stacpkg items from-parquet openaerialmap-provider.items.parquet \
  | stacpkg items add-alternate \
  --asset-lock s3store1.asset-lock.arrow \
  --alternate-key original --alternate-name s3store1 \
  | stacpkg items promote-alternate \
  --alternate-key original --mode switch \
  | stacpkg items to-parquet s3store1.items.parquet

# ## Relocate Assets To A Recipient MinIO Store
stacpkg asset-lock from-parquet s3store1.asset-lock.parquet \
  | stacpkg asset-lock relocate \
  --store-type s3 --store-container s3store2 --key recipient-relocation/ \
  --store-endpoint-url http://127.0.0.1:19010 \
  | stacpkg asset-lock to-parquet s3store2.asset-lock.parquet
