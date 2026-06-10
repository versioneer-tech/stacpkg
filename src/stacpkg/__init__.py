# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

"""Arrow-native STAC asset lock packaging."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("stacpkg")
except PackageNotFoundError:
    __version__ = "0+unknown"
