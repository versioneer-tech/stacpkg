#!/usr/bin/env bash
# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

work="$(mktemp -d /tmp/xyz-stacpkg-distribution.XXXXXX)"
trap 'rm -rf "$work"' EXIT
uv build --out-dir "$work/dist"
uv venv --python 3.13 "$work/venv"
uv pip install --python "$work/venv/bin/python" "$work"/dist/*.whl
"$work/venv/bin/python" -I scripts/check_distribution.py
