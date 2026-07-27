#!/usr/bin/env bash
set -euo pipefail

# title: OpenAerialMap Package Handover to Recipient Storage

# Prerequisite: ensure two local S3 stores are running at `http://127.0.0.1:19000` and `http://127.0.0.1:19010`, the Basic-auth OCI registry is running at `localhost:15000`, and its test credentials are available through `ORAS_USER` and `ORAS_PASS`.

# ## Package a provider-filtered OpenAerialMap item selection
# test-setup: openaerialmap-provider-items openaerialmap-provider.items.parquet --item-count 3
# test-setup: openaerialmap-provider-asset-lock source.assets.lock.parquet --item-count 3
# test-setup: file README.md --text "Recipient handover notes"
stacpkg items from-parquet openaerialmap-provider.items.parquet \
  | stacpkg build 01-source-package/
# test-assert: item-provider-names openaerialmap-provider.items.parquet ODM
# test-assert: package-items 01-source-package 1
# test-assert: package-assets 01-source-package 1
# test-assert: asset-lock-keys 01-source-package/assets.lock.parquet thumbnail
# test-assert: asset-lock-store 01-source-package/assets.lock.parquet file

# ## Relocate OpenAerialMap Assets Into Controlled S3 Storage
stacpkg asset-lock from-parquet source.assets.lock.parquet \
  | stacpkg asset-lock relocate \
  --store-type s3 --store-container s3store1 --key controlled-relocation/ \
  --store-endpoint-url http://127.0.0.1:19000 \
  | stacpkg asset-lock to-parquet s3store1.asset-lock.parquet
# test-assert: parquet-rows s3store1.asset-lock.parquet 1
# test-assert: asset-lock-store s3store1.asset-lock.parquet s3 --container s3store1 --key-prefix controlled-relocation/

# ## Add the controlled relocation as item alternates and build the provider-side package
stacpkg items from-parquet openaerialmap-provider.items.parquet \
  | stacpkg items add-alternate \
  --asset-lock <(stacpkg asset-lock from-parquet s3store1.asset-lock.parquet) \
  --alternate-key controlled --alternate-name controlled \
  | stacpkg items to-parquet controlled.items.parquet
# test-assert: parquet-rows controlled.items.parquet 1
# test-assert: item-alternate-hrefs controlled.items.parquet controlled s3://s3store1/controlled-relocation/ --asset-key thumbnail

stacpkg items from-parquet controlled.items.parquet \
  | stacpkg build 02-controlled-asset-relocation-package/ \
  --asset-lock <(stacpkg asset-lock from-parquet s3store1.asset-lock.parquet)
# test-assert: package-items 02-controlled-asset-relocation-package 1
# test-assert: package-assets 02-controlled-asset-relocation-package 1
# test-assert: asset-lock-store 02-controlled-asset-relocation-package/assets.lock.parquet s3 --container s3store1 --key-prefix controlled-relocation/

# ## Relocate Controlled Assets To The Recipient S3 Store
stacpkg asset-lock from-parquet s3store1.asset-lock.parquet \
  | stacpkg asset-lock relocate \
  --store-type s3 --store-container s3store2 --key recipient-relocation/ \
  --store-endpoint-url http://127.0.0.1:19010 \
  | stacpkg asset-lock to-parquet s3store2.asset-lock.parquet
# test-assert: parquet-rows s3store2.asset-lock.parquet 1
# test-assert: asset-lock-store s3store2.asset-lock.parquet s3 --container s3store2 --key-prefix recipient-relocation/

stacpkg items from-parquet controlled.items.parquet \
  | stacpkg items add-alternate \
  --asset-lock <(stacpkg asset-lock from-parquet s3store2.asset-lock.parquet) \
  --alternate-key controlled --alternate-name controlled \
  | stacpkg items to-parquet recipient.items.parquet
# test-assert: parquet-rows recipient.items.parquet 1
# test-assert: item-alternate-hrefs recipient.items.parquet controlled s3://s3store2/recipient-relocation/ --asset-key thumbnail

# ## Build the recipient package with handover notes
stacpkg items from-parquet recipient.items.parquet \
  | stacpkg build 03-recipient-package/ \
  --asset-lock <(stacpkg asset-lock from-parquet s3store2.asset-lock.parquet) \
  --includes README.md
# test-assert: package-items 03-recipient-package 1
# test-assert: package-assets 03-recipient-package 1
# test-assert: asset-lock-store 03-recipient-package/assets.lock.parquet s3 --container s3store2 --key-prefix recipient-relocation/
# test-assert: package-file 03-recipient-package README.md

# ## Publish and pull the recipient package through OCI
stacpkg push 03-recipient-package/ localhost:15000/stacpkg/openaerialmap-recipient-package:v1 --auth-backend basic --plain-http

stacpkg pull localhost:15000/stacpkg/openaerialmap-recipient-package:v1 --output-dir 04-pulled-recipient-package/ --auth-backend basic --plain-http
# test-assert: package-items 04-pulled-recipient-package 1
# test-assert: package-assets 04-pulled-recipient-package 1
# test-assert: package-file 04-pulled-recipient-package README.md
# test-assert: parquet-equals 03-recipient-package/items.parquet 04-pulled-recipient-package/items.parquet
# test-assert: parquet-equals 03-recipient-package/assets.lock.parquet 04-pulled-recipient-package/assets.lock.parquet
