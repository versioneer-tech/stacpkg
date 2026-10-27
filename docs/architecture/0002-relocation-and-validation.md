# ADR-002: Relocation and validation

## Status

Accepted and implemented for Item assets.

## Decision

Use `obstore` for metadata queries and streaming transfers. Resolve relative
hrefs from an explicit base, then an Item self link, then the input document.
Packages with included assets keep source self URLs as `via` links, so their
relative paths use the local Items document when the package moves.
Permit named source alternates
and runtime credentials, including S3 access when the primary HTTPS route is
unsuitable. Saved hrefs must not contain credentials or temporary signed grants.
Check Item and Collection assets, alternates, links, and endpoints.

Plan selected assets and target paths before copying. Reject duplicate
identities and destination collisions. Bound copy buffers and worker counts.
Write the completed package only after all required transfers pass. Failed
runs can leave destination objects.
Apply these rules to both relocation and asset inclusion. Build into a new
directory, reject conflicting included paths, and publish the package only
after all files are ready.

Hash the source stream and compare an independent full-object destination
checksum, or read the destination back. Set target hrefs and retain original
alternates after verification.

Store checksums as lowercase hexadecimal Multihash. Calculate SHA-256 for new
facts. Keep ETags separate. Compare source STAC checksums with calculated or
comparable provider checksums before replacing declarations. Offline
derivation records locations and declared sizes, without importing checksums.

Checksum methods are `metadata`, `use-etag`, `calculate-if-needed`, and
`calculate-always`. Table operations default to `metadata`; package validation
defaults to `calculate-if-needed`. Package relocation always hashes the source
stream and checks the destination. See the
[Asset Lock reference](../reference-guides/asset-lock.md) for provider rules.

Validation checks the full required asset set and Item/lock agreement. Compare
expected values before saving observations. Check both declarations when Item
and lock checksums use different algorithms. A matching checksum permits
changed filesystem validators; report those changes separately and still check
expected size.

Emit asset results and a summary as JSON Lines. Save checked facts atomically
only after complete success. Preserve the prior lock on failure or metadata
changes during validation. A first checksum observation is a baseline.

## Reason

These checks support copies used for edge workloads, repeatable runs, and
isolated environments. File facts stay portable, while check results describe
a particular observation. Checksums need a trusted baseline to detect tampering.
