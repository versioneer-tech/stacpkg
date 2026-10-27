# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from io import BytesIO

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from stacpkg.arrow_io import read_parquet, write_stream
from stacpkg.geoparquet import (
    extract_collections,
    items_table_to_geoparquet_table,
    write_items_geoparquet,
    write_items_geoparquet_stream,
)


def _collection(collection_id: str, *, title: str | None = None) -> dict[str, object]:
    collection: dict[str, object] = {
        "type": "Collection",
        "stac_version": "1.1.0",
        "stac_extensions": ["https://example.test/collection-extension.json"],
        "id": collection_id,
        "description": f"Collection {collection_id}",
        "license": "proprietary",
        "extent": {
            "spatial": {"bbox": [[-180.0, -90.0, 180.0, 90.0]]},
            "temporal": {"interval": [[None, None]]},
        },
        "links": [],
        "example:extension": {"nested": ["complete", "document"]},
    }
    if title is not None:
        collection["title"] = title
    return collection


def _items(*collection_ids: str | None) -> pa.Table:
    return pa.table(
        {
            "type": ["Feature"] * len(collection_ids),
            "stac_version": ["1.1.0"] * len(collection_ids),
            "id": [f"item-{index}" for index in range(len(collection_ids))],
            "collection": list(collection_ids),
            "geometry": [None] * len(collection_ids),
            "links": [[] for _ in collection_ids],
        }
    )


def _write_metadata_only_parquet(path, payload: bytes) -> None:
    table = _items(None)
    metadata = dict(table.schema.metadata or {})
    metadata[b"stac-geoparquet"] = payload
    pq.write_table(table.replace_schema_metadata(metadata), path)


def test_embeds_complete_collection_and_preserves_geometry_metadata(
    tmp_path,
) -> None:
    collection = _collection("collection-a", title="Complete Collection")
    custom_geo = b'{"columns":{"geometry":{"encoding":"WKB"}},"version":"1.1.0"}'
    items = _items("collection-a").replace_schema_metadata({b"geo": custom_geo})
    output = tmp_path / "items.parquet"

    write_items_geoparquet(items, output, collections=[collection])

    expected = {
        "version": "1.1.0",
        "collections": {"collection-a": collection},
    }
    metadata = pq.ParquetFile(output).schema_arrow.metadata
    assert metadata is not None
    assert json.loads(metadata[b"stac-geoparquet"]) == expected
    assert metadata[b"stac_geoparquet:version"] == b"1.1.0"
    assert metadata[b"geo"] == custom_geo


def test_embeds_and_extracts_multiple_collections_sorted_by_id(tmp_path) -> None:
    collection_b = _collection("collection-b")
    collection_a = _collection("collection-a")
    output = tmp_path / "items.parquet"

    write_items_geoparquet(
        _items("collection-b", "collection-a"),
        output,
        collections=[collection_b, collection_a],
    )

    assert extract_collections(output) == [collection_a, collection_b]


@pytest.mark.parametrize("location", ["asset", "alternate", "link"])
def test_collection_documents_reject_signed_access_grants(tmp_path, location):
    collection = _collection("collection-a")
    signed = "https://publisher.test/data?X-Amz-Signature=xyz"
    if location == "link":
        collection["links"] = [{"rel": "items", "href": signed}]
    elif location == "alternate":
        collection["assets"] = {
            "data": {
                "href": "https://publisher.test/data",
                "alternate": {"download": {"href": signed}},
            }
        }
    else:
        collection["assets"] = {"data": {"href": signed}}

    with pytest.raises(ValueError, match="credentials or signed"):
        write_items_geoparquet(
            _items("collection-a"), tmp_path / "items.parquet", collections=[collection]
        )


@pytest.mark.parametrize(
    ("collection", "message"),
    [
        ({"type": "Catalog", "id": "collection-a"}, "type must be 'Collection'"),
        ({"type": "Collection"}, "non-empty string id"),
        ({"type": "Collection", "id": "  "}, "non-empty string id"),
    ],
)
def test_rejects_invalid_collection_documents(collection, message) -> None:
    with pytest.raises(ValueError, match=message):
        items_table_to_geoparquet_table(_items(None), collections=[collection])


def test_rejects_duplicate_collection_ids() -> None:
    with pytest.raises(ValueError, match="duplicate STAC Collection id 'collection-a'"):
        items_table_to_geoparquet_table(
            _items("collection-a"),
            collections=[_collection("collection-a"), _collection("collection-a")],
        )


def test_rejects_collection_mapping_key_id_mismatch() -> None:
    with pytest.raises(
        ValueError,
        match="mapping key 'wrong-key' does not match Collection id 'collection-a'",
    ):
        items_table_to_geoparquet_table(
            _items("collection-a"),
            collections={"wrong-key": _collection("collection-a")},
        )


def test_rejects_missing_collection_for_item_identifier() -> None:
    with pytest.raises(
        ValueError,
        match="missing supplied STAC Collection documents for Item collection IDs: collection-b",
    ):
        items_table_to_geoparquet_table(
            _items("collection-a", "collection-b", None),
            collections=[_collection("collection-a")],
        )


def test_preserves_embedded_collections_through_parquet_round_trip_and_replaces_explicitly(
    tmp_path,
) -> None:
    original = _collection("collection-a", title="Original")
    replacement = _collection("collection-a", title="Replacement")
    first = tmp_path / "first.parquet"
    preserved = tmp_path / "preserved.parquet"
    replaced = tmp_path / "replaced.parquet"
    write_items_geoparquet(_items("collection-a"), first, collections=[original])

    write_items_geoparquet(read_parquet(first), preserved)
    write_items_geoparquet(read_parquet(first), replaced, collections=[replacement])

    assert extract_collections(preserved) == [original]
    assert extract_collections(replaced) == [replacement]


def test_empty_collection_input_does_not_overwrite_existing_embedded_collections() -> None:
    collection = _collection("collection-a")
    embedded = items_table_to_geoparquet_table(
        _items("collection-a"),
        collections=[collection],
    )

    preserved = items_table_to_geoparquet_table(embedded, collections=[])

    metadata = preserved.schema.metadata
    assert metadata is not None
    assert json.loads(metadata[b"stac-geoparquet"])["collections"] == {"collection-a": collection}


def test_stream_writer_preserves_embedded_collections_without_explicit_replacement(
    tmp_path,
) -> None:
    collection = _collection("collection-a")
    table = items_table_to_geoparquet_table(
        _items("collection-a"),
        collections=[collection],
    )
    source = BytesIO()
    write_stream(table, source)
    source.seek(0)
    output = tmp_path / "streamed.parquet"

    write_items_geoparquet_stream(source, output)

    assert extract_collections(output) == [collection]


def test_extracts_singular_collection_metadata(tmp_path) -> None:
    collection = _collection("collection-a")
    output = tmp_path / "items.parquet"
    _write_metadata_only_parquet(
        output,
        json.dumps({"version": "1.0.0", "collection": collection}).encode(),
    )

    assert extract_collections(output) == [collection]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (None, "missing stac-geoparquet metadata"),
        (b"{not-json", "malformed stac-geoparquet metadata"),
        (b'{"collections":[],"version":"1.1.0"}', "collections must be an object"),
        (b'{"version":"1.1.0"}', "contains no STAC Collection objects"),
    ],
)
def test_extractor_reports_missing_malformed_and_empty_metadata(tmp_path, payload, message) -> None:
    output = tmp_path / "invalid.parquet"
    if payload is None:
        pq.write_table(_items(None), output)
    else:
        _write_metadata_only_parquet(output, payload)

    with pytest.raises(ValueError, match=message):
        extract_collections(output)
