#!/usr/bin/env bash
set -euo pipefail

# title: OpenAerialMap Package Handover to Recipient Storage
# test: none

# Prerequisite: ensure two local S3 stores are running at `http://127.0.0.1:19000` and `http://127.0.0.1:19010`, and the local OCI registry is running at `localhost:15000`.

# ## Package a provider-filtered OpenAerialMap item selection
stacpkg items from-parquet openaerialmap-provider.items.parquet \
  | stacpkg build 01-source-package/

# ## Relocate OpenAerialMap Assets Into Controlled S3 Storage
stacpkg asset-lock from-parquet source.assets.lock.parquet \
  | stacpkg asset-lock relocate \
  --store-type s3 --store-container s3store1 --key controlled-relocation/ \
  --store-endpoint-url http://127.0.0.1:19000 \
  | stacpkg asset-lock to-parquet s3store1.asset-lock.parquet

# ## Add the controlled relocation as item alternates and build the provider-side package
stacpkg items from-parquet openaerialmap-provider.items.parquet \
  | stacpkg items add-alternate \
  --asset-lock s3store1.asset-lock.arrow \
  --alternate-key controlled --alternate-name controlled \
  | stacpkg items to-parquet controlled.items.parquet

stacpkg items from-parquet controlled.items.parquet \
  | stacpkg build 02-controlled-asset-relocation-package/ \
  --asset-lock s3store1.asset-lock.arrow

# ## Relocate Controlled Assets To The Recipient S3 Store
stacpkg asset-lock from-parquet s3store1.asset-lock.parquet \
  | stacpkg asset-lock relocate \
  --store-type s3 --store-container s3store2 --key recipient-relocation/ \
  --store-endpoint-url http://127.0.0.1:19010 \
  | stacpkg asset-lock to-parquet s3store2.asset-lock.parquet

stacpkg items from-parquet controlled.items.parquet \
  | stacpkg items add-alternate \
  --asset-lock s3store2.asset-lock.arrow \
  --alternate-key controlled --alternate-name controlled \
  | stacpkg items to-parquet recipient.items.parquet

# ## Build the recipient package with handover notes
stacpkg items from-parquet recipient.items.parquet \
  | stacpkg build 03-recipient-package/ \
  --asset-lock s3store2.asset-lock.arrow \
  --includes README.md

# ## Publish and pull the recipient package through OCI
stacpkg push 03-recipient-package/ localhost:15000/stacpkg/openaerialmap-recipient-package:v1 --plain-http --insecure

stacpkg pull localhost:15000/stacpkg/openaerialmap-recipient-package:v1 --output-dir 04-pulled-recipient-package/ --plain-http --insecure
