#!/usr/bin/env bash
set -euo pipefail

# title: CDSE STAC To GeoParquet
# test: none

# Prerequisite: ensure public CDSE STAC network access and the `gpio` CLI are available; no local S3 stores or registry are required.

# ## Query public CDSE STAC metadata
# The CDSE STAC catalogue search in this example is public and does not use an authorization header.
# CDSE credentials are only needed later for product downloads or protected asset access.
curl -fsS https://stac.dataspace.copernicus.eu/v1/search \
  --header "Accept: application/geo+json" \
  --header "Content-Type: application/json" \
  --data-binary '{"collections":["sentinel-2-l2a"],"bbox":[16.30,48.10,16.45,48.25],"datetime":"2025-06-01T00:00:00Z/2025-06-10T23:59:59Z","query":{"eo:cloud_cover":{"lt":20}},"sortby":[{"field":"properties.eo:cloud_cover","direction":"asc"}],"limit":3}' \
  --output cdse-sentinel2-vienna.itemcollection.json

# ## Convert the STAC response to GeoParquet
stacpkg items from-json cdse-sentinel2-vienna.itemcollection.json \
  | stacpkg items to-parquet cdse-sentinel2-vienna.items.parquet

# ## Inspect and optimize with geoparquet-io
gpio inspect cdse-sentinel2-vienna.items.parquet --json

gpio sort hilbert cdse-sentinel2-vienna.items.parquet \
  cdse-sentinel2-vienna.hilbert.items.parquet --add-bbox

gpio check all cdse-sentinel2-vienna.hilbert.items.parquet
