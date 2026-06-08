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
test: test-unit ## Run the default fast unit test suite.

.PHONY: test-all
test-all: pre-commit docs test-unit test-integration test-e2e-full ## Run all local quality gates, including full e2e/performance.

.PHONY: test-unit
test-unit: ## Run fast unit tests only.
	uv run pytest tests/unit

.PHONY: test-integration
test-integration: ## Run local integration tests with optional cross-tool dependencies.
	uv run --group integration pytest tests/integration -m integration

.PHONY: test-e2e
test-e2e: ## Run CI-sized end-to-end tests against a local kind S3 store setup.
	scripts/kind-s3-store-e2e.sh

.PHONY: test-e2e-full
test-e2e-full: ## Run all end-to-end tests, including performance-sized checks.
	STACPKG_E2E_PYTEST_MARK=e2e scripts/kind-s3-store-e2e.sh

.PHONY: pre-commit
pre-commit: ## Run all pre-commit hooks.
	uv run pre-commit run --all-files

.PHONY: docs
docs: ## Build docs strictly.
	uv run mkdocs build --strict

.PHONY: docs-serve
docs-serve: ## Serve docs locally.
	uv run mkdocs serve
