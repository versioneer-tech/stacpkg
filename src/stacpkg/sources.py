# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import copy
import json
import posixpath
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

import pyarrow as pa

from stacpkg.enrich import _field_with_type, _merge_struct_type


def check_public_href(href: str) -> str:
    """Reject credentials and temporary access grants in saved locations."""
    parsed = urlsplit(href)
    secret_keys = {
        "x-amz-signature",
        "x-amz-credential",
        "x-amz-security-token",
        "x-goog-signature",
        "x-goog-credential",
        "sig",
        "token",
        "access_token",
    }
    if (
        parsed.username
        or parsed.password
        or any(key.lower() in secret_keys for key, _ in parse_qsl(parsed.query))
    ):
        raise ValueError("asset locations must not contain credentials or signed access grants")
    return href


def check_document_hrefs(document: object) -> None:
    """Check saved hrefs and endpoints in a STAC document."""
    if isinstance(document, dict):
        for key, value in document.items():
            if key in {"href", "store_endpoint_url"} and isinstance(value, str):
                check_public_href(value)
            else:
                check_document_hrefs(value)
    elif isinstance(document, list):
        for value in document:
            check_document_hrefs(value)


def resolve_href(href: str, source_base: str | Path | None = None) -> str:
    """Resolve an asset against a document or a directory ending in a slash."""
    if not isinstance(href, str) or not href.strip():
        raise ValueError("asset href must be a non-empty string")
    if urlsplit(href).scheme == "file":
        parsed = urlsplit(href)
        if parsed.netloc not in {"", "localhost"} or parsed.query or parsed.fragment:
            raise ValueError("file href must name a local path without a query or fragment")
        return Path(unquote(parsed.path)).as_uri()
    if urlsplit(href).scheme:
        return check_public_href(href)
    if href.startswith("/") and source_base is None:
        return Path(href).as_uri()
    if source_base is None:
        raise ValueError("relative asset href requires a source base or an absolute self link")
    base = str(source_base)
    if not urlsplit(base).scheme:
        path = Path(base).resolve()
        base = path.as_uri() + ("/" if path.is_dir() or base.endswith("/") else "")
    parsed = urlsplit(base)
    reference = urlsplit(href)
    if reference.netloc:
        return check_public_href(
            urlunsplit(
                (
                    parsed.scheme,
                    reference.netloc,
                    reference.path,
                    reference.query,
                    reference.fragment,
                )
            )
        )
    directory = parsed.path if parsed.path.endswith("/") else posixpath.dirname(parsed.path)
    path = (
        posixpath.normpath(posixpath.join(directory, reference.path))
        if reference.path
        else parsed.path
    )
    if reference.path.endswith("/") and not path.endswith("/"):
        path += "/"
    if not path.startswith("/"):
        path = "/" + path
    query = reference.query if reference.path or "?" in href else parsed.query
    resolved = urlunsplit((parsed.scheme, parsed.netloc, path, query, reference.fragment))
    return resolve_href(resolved) if parsed.scheme == "file" else check_public_href(resolved)


def resolve_item_assets(
    items: pa.Table,
    *,
    source_base: str | Path | None = None,
    source_alternate: str | None = None,
    asset_keys: set[str] | None = None,
    include_metadata_assets: bool = False,
    document_href: str | Path | None = None,
) -> pa.Table:
    """Resolve primary hrefs or explicitly select one alternate for every asset."""
    rows = []
    for original in items.to_pylist():
        row = copy.deepcopy(original)
        compact = "assets_json" in row
        assets = json.loads(str(row.get("assets_json") or "{}")) if compact else row.get("assets")
        links = json.loads(str(row.get("links_json") or "[]")) if compact else row.get("links")
        check_document_hrefs(links)
        check_document_hrefs(assets)
        base = source_base or row.get("source_href") or document_href
        for link in links or []:
            if link.get("rel") == "self" and link.get("href"):
                if source_base is None:
                    base = resolve_href(link["href"], base)
                break
        for link in links or []:
            if link.get("href"):
                link["href"] = resolve_href(link["href"], base)
        for key, asset in (assets or {}).items():
            if not isinstance(asset, dict) or not asset.get("href"):
                continue
            selected = asset
            selected_for_copy = (
                key in asset_keys if asset_keys else (key != "metadata" or include_metadata_assets)
            )
            if source_alternate and selected_for_copy:
                selected = (asset.get("alternate") or {}).get(source_alternate)
                if not isinstance(selected, dict) or not selected.get("href"):
                    raise ValueError(
                        f"missing alternate {source_alternate!r} for {row.get('id')}/{key}"
                    )
            asset["href"] = resolve_href(selected.get("href"), base)
            if source_alternate and selected_for_copy:
                asset["store_endpoint_url"] = selected.get("store_endpoint_url")
            for alternate in (asset.get("alternate") or {}).values():
                if isinstance(alternate, dict) and alternate.get("href"):
                    alternate["href"] = resolve_href(alternate["href"], base)
                    if alternate.get("store_endpoint_url"):
                        check_public_href(alternate["store_endpoint_url"])
            if asset.get("store_endpoint_url"):
                check_public_href(asset["store_endpoint_url"])
        if compact:
            row["assets_json"] = json.dumps(assets, sort_keys=True, separators=(",", ":"))
            row["links_json"] = json.dumps(links, sort_keys=True, separators=(",", ":"))
        else:
            row["assets"] = assets
            row["links"] = links
        rows.append(row)
    schema = items.schema
    index = schema.get_field_index("assets")
    if source_alternate and index != -1 and pa.types.is_struct(schema.field(index).type):
        field = schema.field(index)
        asset_fields = [
            _field_with_type(
                asset, _merge_struct_type(asset.type, [pa.field("store_endpoint_url", pa.string())])
            )
            if pa.types.is_struct(asset.type)
            else asset
            for asset in field.type
        ]
        schema = schema.set(index, _field_with_type(field, pa.struct(asset_fields)))
    return pa.Table.from_pylist(rows, schema=schema)


def portable_item_links(items: pa.Table) -> pa.Table:
    """Keep the source document as a via link in a portable package."""
    rows = items.to_pylist()
    for row in rows:
        compact = "links_json" in row
        links = json.loads(row.get("links_json") or "[]") if compact else row.get("links")
        for link in links or []:
            if link.get("rel") == "self":
                link["rel"] = "via"
        if compact:
            row["links_json"] = json.dumps(links, sort_keys=True, separators=(",", ":"))
        else:
            row["links"] = links
    return pa.Table.from_pylist(rows, schema=items.schema)


def select_item_assets(items: pa.Table, asset_keys: set[str]) -> pa.Table:
    """Keep named assets and preserve the other Item and schema fields."""
    rows = items.to_pylist()
    for row in rows:
        if "assets_json" in row:
            assets = json.loads(str(row.get("assets_json") or "{}"))
            row["assets_json"] = json.dumps(
                {key: asset for key, asset in assets.items() if key in asset_keys}
            )
        else:
            row["assets"] = {
                key: asset for key, asset in (row.get("assets") or {}).items() if key in asset_keys
            }
    schema = items.schema
    index = schema.get_field_index("assets")
    if index != -1 and pa.types.is_struct(schema.field(index).type):
        field = schema.field(index)
        schema = schema.set(
            index,
            _field_with_type(
                field, pa.struct([asset for asset in field.type if asset.name in asset_keys])
            ),
        )
    return pa.Table.from_pylist(rows, schema=schema)
