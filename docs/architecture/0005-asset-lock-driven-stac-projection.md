# ADR-005: Asset-Lock Driven STAC Projection

| Status | Date | Implementation |
| --- | --- | --- |
| Accepted | 2026-06-07 | Implemented by current item projection and enrichment commands in `0.1.0`. |

## Context

Asset relocation creates two related records:

- `assets.lock.parquet` records asset locations and object facts, keyed by
  `item_id` and `asset_key`;
- `items.parquet` records STAC Item metadata, including the STAC Asset objects
  that clients read.

The package needs a way to write relocated or alternate asset locations back
into STAC metadata without losing the original asset lock. Copying bytes and
editing STAC metadata are different steps. Keeping them separate makes retries,
review, validation, and handover easier to understand.

## Decision

Treat STAC asset projection as a metadata view built from asset-lock rows.

When a command writes lock locations into STAC Items, it matches rows by the
full identity pair:

```text
item_id
asset_key
```

It does not match by `asset_key` alone. Asset keys such as `thumbnail`,
`visual`, or `metadata` are only unique inside one Item. A lock row can change a
STAC Asset only when the current Item has the same item id and asset key. If no
matching lock row exists, the STAC Asset is left unchanged.

The current CLI exposes projection through small item commands:

- `items add-alternate` writes hrefs reconstructed from an asset lock into
  `asset.alternate[KEY].href`.
- `items enrich` writes lock facts such as `size_bytes` to STAC File Info
  fields, and can also write lock hrefs as alternates with `--alternate-key`.
- `items promote-alternate` promotes an existing alternate href to the primary
  `asset.href`.
- `items remove-alternate` removes one alternate map entry.

This keeps primary href changes explicit. A workflow that wants relocated
locations as primary hrefs first writes or receives those locations as
alternates, then promotes the chosen alternate.

`store_endpoint_url`, when present in the lock row, is projected with the href
so S3-compatible locations remain clear when bucket and key are not enough.

Assets whose key is `metadata` are excluded from asset-lock derivation by
default. These sidecars often repeat information already preserved in
`items.parquet`. Use `--include-metadata-assets` or an explicit
`--asset-keys metadata` filter when those sidecars are real assets that must be
locked, relocated, validated, or projected.

Validation results are not projected into STAC metadata. They are command
output for the current validation run, not lasting item fields.

## Alternatives Considered

- **Rewrite STAC Items during relocation:** Fewer commands for simple cases, but
  it couples byte transfer to metadata changes and makes retries harder to
  reason about.
- **Match by asset key only:** Simpler lookup, but it can project one Item's
  copied asset into another Item with the same asset key.
- **Always replace primary hrefs:** Simple output, but it hides the original
  provider reference. Alternates let users keep both references and promote a
  chosen one when needed.

## Consequences

Relocation workflows stay explicit:

```text
asset-lock derive -> asset-lock relocate -> items add-alternate/enrich
```

Users can keep original hrefs visible, add controlled or recipient hrefs as
alternates, and promote a chosen alternate when the receiving environment should
use that location as primary.

The asset lock remains the durable source for asset locations and facts. STAC
metadata remains the client-facing view.
