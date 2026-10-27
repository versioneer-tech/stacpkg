# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

SHELL = /usr/bin/env bash -o pipefail
.SHELLFLAGS = -ec

.PHONY: help
help: ## Display this help.
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make <target>\n"} /^[a-zA-Z_0-9-]+:.*?##/ { printf "  %-18s %s\n", $$1, $$2 } /^##@/ { printf "\n%s\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ Development

.PHONY: sync
sync: ## Install all uv dependency groups.
	uv sync --all-groups

.PHONY: test
test: test-unit ## Run unit tests.

.PHONY: test-all
.NOTPARALLEL: test-all
test-all: pre-commit docs test-unit test-usecases test-integration test-distribution test-e2e-full ## Run all checks, including live service tests.

.PHONY: test-unit
test-unit: ## Run unit tests with local files.
	uv run pytest tests/unit

.PHONY: test-integration
test-integration: ## Check local data exchange with other tools.
	uv run --group integration pytest tests/integration -m integration

.PHONY: test-usecases
test-usecases: ## Generate and run the documented CLI examples.
	uv run pytest tests/usecases -m usecase

.PHONY: test-distribution
test-distribution: ## Build distributions and check the wheel in an isolated environment.
	scripts/check-distribution.sh

.PHONY: test-e2e
test-e2e: ## Test S3 copies and OCI handover in the dedicated Kind cluster.
	scripts/kind-s3-store-e2e.sh

.PHONY: test-e2e-full
test-e2e-full: ## Run service tests, including copy performance.
	STACPKG_E2E_PYTEST_MARK=e2e scripts/kind-s3-store-e2e.sh

.PHONY: pre-commit
pre-commit: ## Run all pre-commit hooks.
	uv run pre-commit run --all-files

.PHONY: docs
docs: test-usecases ## Run local examples and build docs strictly.
	uv run python scripts/generate_usecase_tests.py --no-tests
	uv run --group docs mkdocs build --strict

.PHONY: docs-serve
docs-serve: test-usecases ## Run local examples and serve docs locally.
	uv run python scripts/generate_usecase_tests.py --no-tests
	uv run --group docs mkdocs serve
