# CDSE STAC Search To GeoParquet

This example queries the Copernicus Data Space Ecosystem STAC API, writes the
selected Items as STAC GeoParquet, and checks the result with `geoparquet-io`.
It is useful when a live STAC query is the source of a local, queryable table.

The metadata search shown here does not require CDSE credentials. The CDSE STAC
catalogue is exposed at `https://stac.dataspace.copernicus.eu/v1/`, and the
same documentation shows POST searches against `/search`. CDSE credentials are
needed later when you download products through OData or access protected
assets; the CDSE token documentation specifically covers product download
tokens for the OData API.

References:

- CDSE STAC product catalogue: <https://documentation.dataspace.copernicus.eu/APIs/STAC.html>
- CDSE token generation for downloads: <https://documentation.dataspace.copernicus.eu/APIs/Token.html>
- `geoparquet-io`: <https://geoparquet.io/>

## Query CDSE STAC

This query selects three low-cloud Sentinel-2 L2A Items around Vienna for a
fixed June 2025 window. It fetches STAC Item metadata only; it does not download
Sentinel assets.

```bash
tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/stacpkg-cdse-sentinel2.XXXXXX")
cdse_items_json="$tmpdir/cdse-sentinel2-vienna.itemcollection.json"
cdse_items_gpq="$tmpdir/cdse-sentinel2-vienna.items.parquet"
cdse_optimized_gpq="$tmpdir/cdse-sentinel2-vienna.hilbert.items.parquet"

curl -fsS https://stac.dataspace.copernicus.eu/v1/search \
  --header "Accept: application/geo+json" \
  --header "Content-Type: application/json" \
  --data-binary "{
    \"collections\": [\"sentinel-2-l2a\"],
    \"bbox\": [16.30, 48.10, 16.45, 48.25],
    \"datetime\": \"2025-06-01T00:00:00Z/2025-06-10T23:59:59Z\",
    \"query\": {\"eo:cloud_cover\": {\"lt\": 20}},
    \"sortby\": [{\"field\": \"properties.eo:cloud_cover\", \"direction\": \"asc\"}],
    \"limit\": 3
  }" \
  --output "$cdse_items_json"
```

## Write STAC GeoParquet

Convert the STAC ItemCollection response to a GeoParquet-backed items table:

```bash
stacpkg items from-json "$cdse_items_json" \
  | stacpkg items to-parquet "$cdse_items_gpq"

echo "created $cdse_items_gpq"
```

Sample output:

```text
created /tmp/stacpkg-cdse-sentinel2.ABC123/cdse-sentinel2-vienna.items.parquet
```

The resulting table keeps one row per STAC Item, WKB geometry, GeoParquet
metadata, the STAC `assets` struct, and promoted STAC properties such as
`eo:cloud_cover`, `platform`, `processing:level`, and `grid:code`.

## Check With geoparquet-io

Install `geoparquet-io` if `gpio` is not already available:

```bash
uv tool install geoparquet-io
```

Inspect the generated table:

```bash
gpio inspect "$cdse_items_gpq" --json
```

The inspected metadata should include:

```json
{
  "rows": 3,
  "geoparquet_version": "1.0.0",
  "crs": "EPSG:4326 (default)"
}
```

For spatial query workflows, create an optimized copy with Hilbert ordering and
GeoParquet bbox metadata:

```bash
gpio sort hilbert "$cdse_items_gpq" "$cdse_optimized_gpq" --add-bbox
gpio check all "$cdse_optimized_gpq"
```

`gpio sort hilbert` writes a GeoParquet 1.1 output file in the current
`geoparquet-io` 0.3.x line used by the repository integration checks.

## Notes

- This example intentionally avoids `stacpkg asset-lock derive` because many
  CDSE asset hrefs point to OData downloads or S3 locations that may require
  credentials for object access.
- The STAC metadata query is public. Product download, asset validation, and
  asset relocation are separate steps with their own credential requirements.
- For larger searches, page through STAC search results with a STAC client and
  stream Items into `stacpkg items from-ndjson`.
