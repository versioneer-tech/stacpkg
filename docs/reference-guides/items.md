# Items

`items.parquet` records the selected STAC Items for a package. One row
represents one STAC Item. Item assets, properties, and links stay with that row.

## Schema

The field list, required flags, and nested `links` and
`assets` shapes are defined by the [STAC GeoParquet specification schema
table](https://radiantearth.github.io/stac-geoparquet-spec/latest/#guidelines).

The spec maps core Item fields such as `id`,
`geometry`, `bbox`, `links`, `assets`, `stac_extensions`, and `collection` to
Parquet columns, and promotes STAC `properties` entries to top-level property
columns.

## Collection metadata

Collection documents are stored under the `stac-geoparquet` Parquet key:

```json
{
  "version": "1.1.0",
  "collections": {
    "<collection-id>": {
      "...": "complete STAC Collection object"
    }
  }
}
```

The map key must equal the Collection object's `id`. Extension fields are
preserved. The `stac-geoparquet.version` value and the
`stac_geoparquet:version` key remain consistent. Existing embedded Collections
flow through Arrow streams, filtering, alternate-asset transformations,
enrichment, and package builds unless explicit Collection documents replace
them.

Package metadata uses `stacpkg:schema-kind=items` and
`stacpkg:schema-version=v1` to identify the table. Standard GeoParquet `geo`
metadata is preserved.

The Python `write_items_geoparquet` and `build_package` functions accept
`collections=`. The CLI accepts repeatable `--collection PATH` inputs.

Collection embedding checks document type, non-empty IDs, mapping keys,
duplicate IDs, and whether supplied documents cover Item collection IDs.
Complete Collection JSON is preserved in the Parquet metadata.
Collection assets, links, and endpoints are checked for credentials and signed
access grants before saving. Descriptions and extents stay as published.

### Collection extraction

`collections extract FILE [--output PATH]` writes a JSON array sorted by ID.
It reads the `collections` map or singular `collection` field. Generate a
separate Collection file after pull when a consumer needs it.
Extraction keeps asset hrefs as stored.

## Field handling

`geometry` is stored as WKB with GeoParquet metadata. `bbox` follows the
GeoParquet bounding-box column shape. `links` are stored as link structs, and
`assets` are stored as nested asset structs using the STAC Asset keys from the
items.

GeoJSON conversion supports Point, LineString, Polygon, and their Multi forms
with XY or XYZ coordinates. Positions within one geometry must use the same
dimensions. Bounding boxes retain Z limits. Links keep request fields and
extension fields. Missing values are null.

STAC `properties` fields become top-level columns. Datetime-like property
values are converted to UTC timestamp values when they can be parsed; values
that cannot be parsed stay as their original values. Property names that
collide with top-level STAC GeoParquet fields are not emitted as separate
property columns.

JSON readers keep fields from all Items in a batch. NDJSON rejects new fields
or changed types after the stream schema is set.

## Relocation and file facts

`relocate` writes copied target hrefs as primary locations and retains original
locations under `alternate.original`. Selected `--asset-keys` define the output
asset set. Other Item fields and Collection schema metadata are preserved.

`items enrich` writes lock `size_bytes` as `file:size` and `file_checksum` as
`file:checksum`. It removes a source checksum when the matching lock row has no
checksum fact. See the [Asset Lock reference](asset-lock.md) for methods and
validation rules.

A package with included asset bytes uses relative target hrefs under `assets/`.
Package validation resolves them against the local `items.parquet` path, so the
package can move between local directories.
Source self URLs are retained as `via` links in packages with included assets.
The Parquet reader uses the same document base when a package becomes input
for another copy. An explicit `--source-base` takes priority.
