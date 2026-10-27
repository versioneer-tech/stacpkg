#!/usr/bin/env bash
# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

# title: Relocate selected assets

# Start with a local STAC GeoParquet selection. The test uses the downloaded OpenAerialMap thumbnails.
# test-setup: openaerialmap-items openaerialmap.items.parquet --item-count 3

# ## Copy the selected thumbnails
# The command checks destination contents, then writes the package with new primary hrefs and original alternates.
stacpkg items from-parquet openaerialmap.items.parquet \
  | stacpkg relocate verified.pkg --target copied-assets/ --asset-keys thumbnail
# test-assert: package-items verified.pkg 3
# test-assert: package-assets verified.pkg 3
# test-assert: asset-lock-keys verified.pkg/assets.lock.parquet thumbnail
# test-assert: asset-lock-store verified.pkg/assets.lock.parquet file
# test-assert: parquet-columns verified.pkg/assets.lock.parquet item_id asset_key size_bytes file_checksum
# test-assert: verified-package verified.pkg

# ## Check the package again
# Validation prints asset results and a summary. It saves the checked lock only after every asset passes.
stacpkg validate verified.pkg > validation.jsonl
# test-assert: validation-report validation.jsonl 3

tail -n 1 validation.jsonl

stacpkg inspect verified.pkg --format markdown
