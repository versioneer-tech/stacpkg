# Test Layout

Use the repository `Makefile` as the source of truth for runnable test, docs, and
quality commands. Start with `make help`; keep command recipes there instead of
duplicating them in this README.

[`tests/`](./) is split by runtime boundary:

| Folder | Purpose |
| --- | --- |
| [`unit/`](unit/) | Fast unit tests and small fixture-backed checks. No network, Docker, kind, Kubernetes, S3 store, or registry. |
| [`usecases/`](usecases/) | Tests for `docs/usecases` workflows. Materialized Python tests are named `test_generated_*.py`, ignored by git, and created before pytest collection. |
| [`integration/`](integration/) | Local cross-library checks using optional tools such as `rustac`, DuckDB, and `geoparquet-io`/`gpio`. No deployed services. |
| [`e2e/`](e2e/) | Shared e2e helpers plus performance and other infra-backed tests that are not docs/usecase workflows. |
| [`setup/`](setup/) | Kubernetes manifests for the local e2e MinIO stores and OCI registry. |
| [`data/`](data/) | Static OpenAerialMap fixtures and fixture helper constants. |

## E2E Harness

The kind harness creates or reuses the `stacpkg-s3-store` cluster and namespace,
applies the manifests in [`tests/setup/`](setup/), starts port-forwards for the
MinIO stores and registry, exports the expected environment variables, and then
runs pytest.

Use case docs and generated tests are generated from shell sources in
[`docs/usecases/`](../docs/usecases/) matching `*.sh`. The generated Markdown
pages are written under ignored `docs/generated/usecases/` during docs builds,
and `pytest tests/usecases` creates matching Python tests that execute the shell
commands with local fixtures and hidden `test-assert` checks before collection.

Generated usecase tests are kept fast and local. Infra-backed service tests live
under [`tests/e2e/`](e2e/); no `test_usecase_*.py` files should live under
[`tests/usecases/`](usecases/).

## OpenAerialMap Fixtures

The canonical fixture selection is OpenAerialMap Central Europe for calendar
year 2025. Fixture paths and selected item IDs live in
[`tests/data/openaerialmap_data.py`](data/openaerialmap_data.py).

The central-Europe asset-lock fixtures intentionally omit the `metadata` asset
key. Higher-level integration and e2e tests should exercise ordinary data assets
only; explicit `metadata` asset-key inclusion belongs in focused unit tests.

Fast fixture integrity checks live in
[`tests/unit/test_openaerialmap_fixture.py`](unit/test_openaerialmap_fixture.py).
When regenerating fixtures from live services, keep the fixture constants, row
counts, and asset-lock expectations aligned with those tests.
