#!/usr/bin/env bash
set -euo pipefail

# title: CDSE STAC To GeoParquet

# Prerequisite: ensure public CDSE STAC network access and the `gpio` CLI are available; no local S3 stores or registry are required.

# References:

# - CDSE STAC product catalogue: https://documentation.dataspace.copernicus.eu/APIs/STAC.html
# - CDSE token generation for downloads: https://documentation.dataspace.copernicus.eu/APIs/Token.html
# - geoparquet-io: https://geoparquet.io/

# ## Query public CDSE STAC metadata
# The CDSE STAC catalogue search in this example is public and does not use an authorization header.
# CDSE credentials are only needed later for product downloads or protected asset access.
# The fixed query selects three low-cloud Sentinel-2 L2A Items around Vienna for June 2025.
# It fetches STAC Item metadata only; it does not download Sentinel asset bytes.
curl -fsS https://stac.dataspace.copernicus.eu/v1/search \
  --header "Accept: application/geo+json" \
  --header "Content-Type: application/json" \
  --data-binary '{"collections":["sentinel-2-l2a"],"bbox":[16.30,48.10,16.45,48.25],"datetime":"2025-06-01T00:00:00Z/2025-06-10T23:59:59Z","query":{"eo:cloud_cover":{"lt":20}},"sortby":[{"field":"properties.eo:cloud_cover","direction":"asc"}],"limit":3}' \
  --output cdse-sentinel2-vienna.itemcollection.json

# ## Convert the STAC response to GeoParquet
# The resulting table keeps one row per STAC Item, WKB geometry, GeoParquet metadata,
# the STAC `assets` struct, and promoted STAC properties such as `eo:cloud_cover`.
stacpkg items from-json cdse-sentinel2-vienna.itemcollection.json \
  | stacpkg items to-parquet cdse-sentinel2-vienna.items.parquet
# test-assert: parquet-rows cdse-sentinel2-vienna.items.parquet 3
# test-assert: parquet-columns cdse-sentinel2-vienna.items.parquet id geometry assets bbox datetime

# ## Inspect and optimize with geoparquet-io
# For spatial query workflows, create an optimized copy with Hilbert ordering and
# GeoParquet bbox metadata.
gpio inspect cdse-sentinel2-vienna.items.parquet --json

gpio sort hilbert cdse-sentinel2-vienna.items.parquet \
  cdse-sentinel2-vienna.hilbert.items.parquet --add-bbox

gpio check all cdse-sentinel2-vienna.hilbert.items.parquet
# test-assert: parquet-rows cdse-sentinel2-vienna.hilbert.items.parquet 3
# test-assert: parquet-columns cdse-sentinel2-vienna.hilbert.items.parquet id geometry assets bbox datetime

# ## Notes
# This use case intentionally avoids `stacpkg asset-lock derive` because many CDSE asset
# hrefs point to OData downloads or S3 locations that may require credentials for object access.
# For larger searches, page through STAC search results with a STAC client and stream Items
# into `stacpkg items from-ndjson`.
