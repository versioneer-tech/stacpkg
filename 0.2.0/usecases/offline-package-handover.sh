#!/usr/bin/env bash
# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

# title: Offline handover

# Use a local selection with its source files under `openaerialmap-local-assets/`.
# This example moves that source directory after building the package.
# test-setup: openaerialmap-items openaerialmap.items.parquet --item-count 3

# ## Include the assets
stacpkg items from-parquet openaerialmap.items.parquet \
  | stacpkg build source.pkg --include-assets --include-metadata-assets
# test-assert: package-assets source.pkg 6

# ## Copy the package and make the original source unavailable
cp -R source.pkg delivered.pkg
mv openaerialmap-local-assets source-unavailable

# ## Check the delivered files
stacpkg validate delivered.pkg > validation.jsonl
# test-assert: verified-package delivered.pkg
# test-assert: validation-report validation.jsonl 6

tail -n 1 validation.jsonl

stacpkg inspect delivered.pkg --format markdown
