#!/usr/bin/env bash
# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

# title: S3 and OCI handover
# test: false

# Start with `openaerialmap.items.parquet` and accessible source assets.
# Prepare two S3 buckets and an OCI registry with runtime credentials.
# This example uses local endpoints at ports 19000, 19010, and 15000.
# Use new package directories and destination prefixes for each run.

# ## Copy to provider storage
stacpkg items from-parquet openaerialmap.items.parquet \
  | stacpkg relocate provider.pkg \
      --target s3://s3store1/selection/ \
      --target-endpoint-url http://127.0.0.1:19000 \
      --include-metadata-assets

# ## Deliver to recipient storage
# The provider package records the source endpoint and verified file facts.
stacpkg items from-parquet provider.pkg/items.parquet \
  | stacpkg relocate recipient.pkg \
      --target s3://s3store2/selection/ \
      --target-endpoint-url http://127.0.0.1:19010 \
      --include-metadata-assets

stacpkg validate recipient.pkg

# ## Send metadata through OCI
# The metadata points to assets in recipient storage.
# Registry credentials must be available through the runtime authentication setup.
stacpkg push recipient.pkg localhost:15000/stacpkg/selection:demo \
  --auth-backend basic --plain-http

stacpkg pull localhost:15000/stacpkg/selection:demo \
  --output-dir received.pkg --auth-backend basic --plain-http

stacpkg validate received.pkg
