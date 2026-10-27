# Tests

Run the local checks:

```bash
make pre-commit docs test-unit test-integration test-distribution
```

`make docs` runs the local use cases before building their pages.

| Folder | Purpose |
| --- | --- |
| `unit/` | Copy, validation, metadata, and CLI behavior with local files. |
| `usecases/` | Installed CLI examples for relocation and offline handover. |
| `integration/` | GeoParquet and Arrow exchange with gpio, rustac, and DuckDB. |
| `e2e/` | S3 copies and OCI handover with live services. |
| `data/`, `setup/` | OpenAerialMap fixtures and service manifests. |

Test a result that matters to a user: copied bytes, preserved metadata, or a
clear failure that leaves saved data intact. Avoid checks of source text,
internal calls, or behavior that another test already covers.

## Examples

`docs/usecases/*.sh` supplies the use-case pages and shell tests. After editing
a source, run `uv run python scripts/generate_usecase_tests.py`, inspect the
generated test, then run `make test-usecases`. Setup and result-check directives
stay out of the page. Generated files are ignored by Git. Pages include output
from successful runs, with temporary paths shown as `/workshop`.

The S3 and OCI recipe has `# test: false` because it needs configured services.
The service suite checks its transfer behavior separately.

## Service checks

After the local checks, use Docker and the dedicated Kind cluster
`stacpkg-s3-store`, with context `kind-stacpkg-s3-store`. Install Kind, kubectl,
curl, OpenSSL, and `htpasswd`. The harness deploys two MinIO stores and a Basic
auth registry in namespace `stacpkg-s3-store`. It uses public images and test
credentials; no cloud account is needed.

```bash
make test-e2e       # S3 copies, failed transfers, and offline OCI handover
make test-e2e-full  # Also time a copy between the two S3 stores
```

The tests compare delivered bytes with the originals and reject missing
sources and wrong checksums. The performance check has a 120-second limit;
set `STACPKG_TEST_S3STORE_MAX_SECONDS` to change it.

The harness uses a temporary kubeconfig and removes port-forwards after the
run. It leaves the cluster and services for inspection. It does not create a
cluster or change the current context. Inspect existing Docker nodes before
creating a cluster; resume usable nodes. Get approval before creating,
stopping, or deleting a local cluster. Report service results separately from
local results.

## Release checks

The Tests workflow runs these local checks before the full service suite.
CI creates its own disposable Kind cluster. The distribution check builds the
source archive and wheel, then tests handover and validation with the installed
wheel in a fresh environment.

A release tag must point to the exact commit of a successful Tests run from
a push to `main`. Package builds and both publication paths wait for this
gate. If that run is still in progress, let it finish, then rerun the release
workflow. The tag workflow does not repeat the service suite.

Prerelease tags publish their own docs. Only stable tags update the `latest`
alias and default page.
