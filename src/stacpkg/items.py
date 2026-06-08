# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from collections.abc import Iterable

import pyarrow as pa


def _json_object(value: object) -> dict[str, object]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _add_provider_value(values: set[str], value: object) -> None:
    if isinstance(value, str) and value:
        values.add(value)


def _add_provider_entries(values: set[str], providers: object) -> None:
    if not isinstance(providers, Iterable) or isinstance(providers, (str, bytes, dict)):
        return
    for provider in providers:
        if isinstance(provider, dict):
            _add_provider_value(values, provider.get("name"))
        else:
            _add_provider_value(values, provider)


def provider_names(row: dict[str, object]) -> set[str]:
    values: set[str] = set()
    _add_provider_value(values, row.get("provider"))
    _add_provider_value(values, row.get("oam:producer_name"))
    _add_provider_entries(values, row.get("providers"))

    properties = _json_object(row.get("properties_json"))
    _add_provider_value(values, properties.get("provider"))
    _add_provider_value(values, properties.get("oam:producer_name"))
    _add_provider_entries(values, properties.get("providers"))
    return values


def filter_items(
    items: pa.Table,
    *,
    item_ids: set[str] | None = None,
    collections: set[str] | None = None,
    providers: set[str] | None = None,
) -> pa.Table:
    rows: list[dict[str, object]] = []
    for row in items.to_pylist():
        if item_ids and row.get("id") not in item_ids:
            continue
        if collections and row.get("collection") not in collections:
            continue
        if providers and provider_names(row).isdisjoint(providers):
            continue
        rows.append(row)
    return pa.Table.from_pylist(rows, schema=items.schema)
