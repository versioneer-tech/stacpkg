# Relocate Assets

This guide continues from
[Create STAC Package](create-stac-package.md). It starts after the package
tutorial has already selected the OpenAerialMap Items, built the package, derived
the HTTPS asset lock, and added the S3 alternates. The new work here is to
relocate those locked assets into local storage, add local alternates to the STAC
Items, and validate the local lock.

Run the commands in the same shell you used for the package tutorial so
`$tmpdir` remains available. If you are starting from a new shell, set `tmpdir`
to the temporary directory created in that tutorial.

## Starting Point

Complete the package tutorial through
[Enrich The STAC Items](create-stac-package.md#enrich-the-stac-items) first. At
that point the OpenAerialMap sample has already done this:

- selected the two Austria Items from the `openaerialmap` collection;
- built a package with the selected Items and four non-metadata asset rows;
- validated the original HTTPS asset metadata in the package asset lock;
- created an S3-shaped asset lock without copying bytes;
- enriched the STAC Items with `alternate.s3.href` values.

The relocation flow starts from this structure:

```text
$tmpdir/
  openaerialmap-austria.pkg/
    items.parquet
    assets.lock.parquet
  openaerialmap-austria.s3.assets.lock.parquet
  openaerialmap-austria.enriched.items.parquet
```

Use the package asset lock as the authoritative source for relocation, and use
the enriched items table as the STAC view that already contains S3 alternates.

## Relocate Assets To Local Storage

Relocate bytes from the locked package hrefs into a local mirror:

```bash
mkdir -p "$tmpdir/local-assets"

stacpkg asset-lock from-parquet "$tmpdir/openaerialmap-austria.pkg/assets.lock.parquet" \
  | stacpkg asset-lock relocate \
      --source-prefix https://oin-hotosm-temp.s3.amazonaws.com/ \
      --store-type file \
      --key "$tmpdir/local-assets/" \
      --max-workers 4 \
      --memory-limit-bytes 512MiB \
      --chunk-size-bytes 8MiB \
  | stacpkg asset-lock to-parquet \
      "$tmpdir/openaerialmap-austria.local.asset-lock.parquet"

echo "created $tmpdir/openaerialmap-austria.local.asset-lock.parquet"
```

Sample output:

```text
created /tmp/stacpkg-openaerialmap-austria.ABC123/openaerialmap-austria.local.asset-lock.parquet
```

The relocated rows point at local files and carry relocated sizes:

| item_id | asset_key | store_type | key | size_bytes |
| --- | --- | --- | --- | --- |
| `631ee6653cdf1c0006b63c5b` | `thumbnail` | `file` | `/tmp/.../local-assets/631ee6653cdf1c0006b63c5b/thumbnail.png` | `793014` |
| `631ee6653cdf1c0006b63c5b` | `visual` | `file` | `/tmp/.../local-assets/631ee6653cdf1c0006b63c5b/visual.tif` | `9913534` |

## Enrich With Local Alternates

Add the relocated local hrefs as alternates, then promote them to primary hrefs in
a temporary items table. This temporary table is only used to derive local file
metadata:

```bash
stacpkg items from-parquet "$tmpdir/openaerialmap-austria.enriched.items.parquet" \
  | stacpkg items add-alternate \
      --asset-lock <(stacpkg asset-lock from-parquet \
        "$tmpdir/openaerialmap-austria.local.asset-lock.parquet") \
      --alternate-key original \
      --alternate-name local \
  | stacpkg items promote-alternate \
      --alternate-key original \
      --mode switch \
  | stacpkg items to-parquet "$tmpdir/openaerialmap-austria.local-primary.items.parquet"

echo "created $tmpdir/openaerialmap-austria.local-primary.items.parquet"
```

Sample output:

```text
created /tmp/stacpkg-openaerialmap-austria.ABC123/openaerialmap-austria.local-primary.items.parquet
```

Derive object metadata facts from the local files so local validation compares
file-backend facts:

```bash
stacpkg items from-parquet "$tmpdir/openaerialmap-austria.local-primary.items.parquet" \
  | stacpkg asset-lock derive \
  | stacpkg asset-lock to-parquet "$tmpdir/openaerialmap-austria.local.assets.lock.parquet"

echo "created $tmpdir/openaerialmap-austria.local.assets.lock.parquet"
```

Sample output:

```text
created /tmp/stacpkg-openaerialmap-austria.ABC123/openaerialmap-austria.local.assets.lock.parquet
```

Now enrich the original remote items table with local alternates as well:

```bash
stacpkg items from-parquet "$tmpdir/openaerialmap-austria.enriched.items.parquet" \
  | stacpkg items enrich \
      --asset-lock <(stacpkg asset-lock from-parquet \
        "$tmpdir/openaerialmap-austria.local.assets.lock.parquet") \
      --alternate-key local \
  | stacpkg items to-parquet "$tmpdir/openaerialmap-austria.s3-local.items.parquet"

echo "created $tmpdir/openaerialmap-austria.s3-local.items.parquet"
```

Sample output:

```text
created /tmp/stacpkg-openaerialmap-austria.ABC123/openaerialmap-austria.s3-local.items.parquet
```

The first thumbnail now has remote primary access plus S3 and local alternates:

```json
{
  "href": "https://oin-hotosm-temp.s3.amazonaws.com/631ee60c3cdf1c0006b63c56/0/631ee60c3cdf1c0006b63c57.png",
  "alternate": {
    "s3": {
      "href": "s3://oin-hotosm-temp/631ee60c3cdf1c0006b63c56/0/631ee60c3cdf1c0006b63c57.png"
    },
    "local": {
      "href": "file:///tmp/stacpkg-openaerialmap-austria.ABC123/local-assets/631ee6653cdf1c0006b63c5b/thumbnail.png"
    }
  }
}
```

## Validate Remote And Local

The package tutorial already created the dry-run S3 lock. Validate it with
unsigned public S3 access when you want to compare the remote alternate lock with
the relocated local lock:

```bash
export AWS_SKIP_SIGNATURE=true
export AWS_EC2_METADATA_DISABLED=true
export AWS_DEFAULT_REGION=us-east-1

stacpkg asset-lock from-parquet "$tmpdir/openaerialmap-austria.s3.assets.lock.parquet" \
  | stacpkg asset-lock validate
```

Sample output:

```json
{"asset_key":"thumbnail","errors":[],"item_id":"631ee6653cdf1c0006b63c5b","key":"631ee60c3cdf1c0006b63c56/0/631ee60c3cdf1c0006b63c57.png","store_container":"oin-hotosm-temp","store_endpoint_url":"https://s3.amazonaws.com","store_type":"s3","valid":true}
```

Validate the local file lock:

```bash
stacpkg asset-lock from-parquet "$tmpdir/openaerialmap-austria.local.assets.lock.parquet" \
  | stacpkg asset-lock validate
```

Sample output:

```json
{"asset_key":"thumbnail","errors":[],"item_id":"631ee6653cdf1c0006b63c5b","key":"/tmp/stacpkg-openaerialmap-austria.ABC123/local-assets/631ee6653cdf1c0006b63c5b/thumbnail.png","store_container":null,"store_type":"file","valid":true}
```

The remote validation checks the public S3 objects. The local validation checks
the relocated files under `$tmpdir/local-assets`.
