# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import binascii
import re

CHECKSUM_STRATEGIES = (
    "metadata",
    "use-etag",
    "calculate-if-needed",
    "calculate-always",
)
_CHECKSUM_STRATEGY_ALIASES = {strategy: strategy for strategy in CHECKSUM_STRATEGIES} | {
    strategy.replace("-", "_"): strategy for strategy in CHECKSUM_STRATEGIES
}

_HEX_RE = re.compile(r"^[0-9a-f]+$")
_DIGEST_LENGTHS = {
    "md5": 16,
    "sha1": 20,
    "sha256": 32,
    "sha512": 64,
}
_ALIASES = {
    "md5": "md5",
    "sha1": "sha1",
    "sha-1": "sha1",
    "sha256": "sha256",
    "sha-256": "sha256",
    "sha2-256": "sha256",
    "sha512": "sha512",
    "sha-512": "sha512",
    "sha2-512": "sha512",
}
_MULTIHASH_CODES = {
    "sha1": 0x11,
    "sha256": 0x12,
    "sha512": 0x13,
    "md5": 0xD5,
}


def normalize_checksum_strategy(value: str) -> str:
    strategy = _CHECKSUM_STRATEGY_ALIASES.get(value.strip().lower())
    if strategy is None:
        raise ValueError(
            f"unsupported checksum strategy: {value}. "
            f"Expected one of: {', '.join(CHECKSUM_STRATEGIES)}"
        )
    return strategy


def _varint(value: int) -> bytes:
    parts = []
    while value >= 0x80:
        parts.append((value & 0x7F) | 0x80)
        value >>= 7
    parts.append(value)
    return bytes(parts)


def _algorithm(value: str) -> str | None:
    return _ALIASES.get(value.strip().lower())


def _is_hex(value: str) -> bool:
    return bool(value) and len(value) % 2 == 0 and bool(_HEX_RE.fullmatch(value))


def multihash_from_digest(algorithm: str, digest: bytes) -> str | None:
    normalized = _algorithm(algorithm)
    if normalized is None or len(digest) != _DIGEST_LENGTHS[normalized]:
        return None
    prefix = _varint(_MULTIHASH_CODES[normalized]) + _varint(len(digest))
    return f"{prefix.hex()}{digest.hex()}"


def multihash_from_hex_digest(algorithm: str, value: str) -> str | None:
    text = value.strip().lower()
    if not _is_hex(text):
        return None
    return multihash_from_digest(algorithm, bytes.fromhex(text))


def multihash_from_base64_digest(algorithm: str, value: str) -> str | None:
    try:
        digest = base64.b64decode(value.strip(), validate=True)
    except binascii.Error:
        return multihash_from_hex_digest(algorithm, value)
    return multihash_from_digest(algorithm, digest)


def normalize_file_checksum(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None

    text = value.strip().lower()
    if _parse_multihash(text) is not None:
        return text

    for separator in (":", "="):
        if separator in text:
            algorithm, digest = text.split(separator, 1)
            return multihash_from_hex_digest(algorithm, digest)
    return None


def file_checksum_algorithm(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = _parse_multihash(value.strip().lower())
    return parsed[0] if parsed else None


def etag_to_file_checksum(value: object) -> str | None:
    if not isinstance(value, str) or value.startswith("W/"):
        return None
    text = value.strip().strip('"').lower()
    if "-" in text:
        return None
    return multihash_from_hex_digest("md5", text)


def _parse_multihash(value: str) -> tuple[str, str] | None:
    if not _is_hex(value):
        return None
    for algorithm in ("sha1", "sha256", "sha512", "md5"):
        expected = multihash_from_hex_digest(
            algorithm,
            "00" * _DIGEST_LENGTHS[algorithm],
        )
        if expected is None:
            continue
        prefix_length = len(expected) - (_DIGEST_LENGTHS[algorithm] * 2)
        prefix = expected[:prefix_length]
        expected_length = prefix_length + (_DIGEST_LENGTHS[algorithm] * 2)
        if len(value) == expected_length and value.startswith(prefix):
            return algorithm, value[prefix_length:]
    return None
