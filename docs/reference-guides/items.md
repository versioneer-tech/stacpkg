# Items

`items.parquet` records the selected STAC Items for a package. One row
represents one STAC Item, preserving the item metadata that downstream tools
query, enrich, package, and hand over.

## Schema

`stacpkg` treats `items.parquet` as a STAC GeoParquet table, not a custom item
schema. The authoritative field list, required flags, and nested `links` and
`assets` shapes are defined by the [STAC GeoParquet specification schema
table](https://radiantearth.github.io/stac-geoparquet-spec/latest/#guidelines).

The spec defines one row per STAC Item. It maps core item fields such as `id`,
`geometry`, `bbox`, `links`, `assets`, `stac_extensions`, and `collection` to
Parquet columns, and promotes STAC `properties` entries to top-level property
columns.

`stacpkg` currently writes STAC GeoParquet metadata version `1.0.0` and local
package-table metadata with `stacpkg:schema-kind=items` and
`stacpkg:schema-version=v1`. The local metadata helps `stacpkg` identify the
table inside a package; it does not replace the STAC GeoParquet field contract.

## Field Handling

`geometry` is stored as WKB with GeoParquet metadata. `bbox` follows the
GeoParquet bounding-box column shape. `links` are stored as link structs, and
`assets` are stored as nested asset structs using the STAC Asset keys from the
items.

STAC `properties` fields become top-level columns. Datetime-like property
values are converted to UTC timestamp values when they can be parsed; values
that cannot be parsed stay as their original values. Property names that
collide with top-level STAC GeoParquet fields are not emitted as separate
property columns.
