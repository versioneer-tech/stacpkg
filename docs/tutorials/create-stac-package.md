# Create and share a STAC package

Start with an existing STAC selection. To copy assets to a separate target,
use [Relocate assets](relocate-assets.md).

## Keep existing asset locations

```bash
stacpkg items from-json selection.json \
  | stacpkg build selection.pkg --checksum calculate-always
```

Build writes the two tables in the [package format](../architecture/0001-package-and-pipelines.md).
Choose a new package directory. Build makes it available after all files are ready.
The example reads bytes to calculate checksums. The default method, `metadata`,
uses storage metadata. `--no-probe-metadata` skips those initial queries.

## Include asset bytes

```bash
stacpkg items from-json selection.json \
  | stacpkg build offline.pkg --include-assets --include-metadata-assets

cp -R offline.pkg delivered.pkg
stacpkg validate delivered.pkg > validation.jsonl
```

Build copies Item assets under `assets/`, checks their contents, and writes
relative target hrefs. `--include-metadata-assets` also copies the `metadata`
asset key, which is skipped by default. Copying still checks bytes when initial
metadata probing is disabled.

See [Offline handover](../generated/usecases/offline-package-handover.md)
for a complete run and its output.

Validation prints asset results and a summary. It replaces the lock only
after all required assets pass. Failure keeps the prior lock and returns a
non-zero exit code. A first checksum observation establishes a baseline.

## Keep Collection metadata and context

Add `--collection collection.json` to build. Repeat it for several documents;
each file can hold one Collection or an array. Complete documents and extension
fields stay in the Parquet metadata. Add notes or a license with `--includes PATH`.

Extract Collection documents when a consumer needs JSON:

```bash
stacpkg collections extract selection.pkg/items.parquet --output collections.json
```

Extraction keeps stored hrefs and the source dataset description.

## Share through OCI

```bash
stacpkg push offline.pkg registry.example/eo/selection:demo
stacpkg pull registry.example/eo/selection:demo --output-dir restored.pkg
stacpkg validate restored.pkg
```

Pull checks layer digests and sizes. Its output directory must not exist.
It transfers included files; external assets must remain accessible to the
recipient. Registry credentials come from runtime settings. Use `validate` for
asset checks after delivery, as shown above.
