# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import threading
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

import obstore as obs
import pytest
from obstore.store import from_url

from stacpkg.object_store import _bucket_config_env_name, _configured_s3_config

LOGGER = logging.getLogger(__name__)
S3STORE1_BUCKET = "stacpkg-e2e-s3store1"
S3STORE2_BUCKET = "stacpkg-e2e-s3store2"

# AWS CLI examples for the library helpers below:
# aws --endpoint-url "$ENDPOINT" s3api create-bucket --bucket "$BUCKET"
# aws --endpoint-url "$ENDPOINT" s3api put-object --bucket "$BUCKET" --key "$KEY" --body "$FILE"
# aws --endpoint-url "$ENDPOINT" s3api head-object --bucket "$BUCKET" --key "$KEY"


def endpoint_env() -> dict[str, str]:
    s3store1_endpoint = os.environ.get("STACPKG_E2E_S3STORE1_ENDPOINT")
    s3store2_endpoint = os.environ.get("STACPKG_E2E_S3STORE2_ENDPOINT")
    if not s3store1_endpoint or not s3store2_endpoint:
        pytest.skip(
            "set STACPKG_E2E_S3STORE1_ENDPOINT and STACPKG_E2E_S3STORE2_ENDPOINT "
            "to run the kind S3 store e2e test"
        )
    env = os.environ.copy()
    missing_credentials = [
        bucket
        for bucket in (S3STORE1_BUCKET, S3STORE2_BUCKET)
        if not _has_s3_credentials(env, bucket)
    ]
    if missing_credentials:
        example_bucket = missing_credentials[0]
        pytest.skip(
            "set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY, or set "
            f"{_bucket_config_env_name(example_bucket, 'ACCESS_KEY_ID')} and "
            f"{_bucket_config_env_name(example_bucket, 'SECRET_ACCESS_KEY')} "
            "for each S3 store e2e bucket"
        )

    env.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    env.setdefault("AWS_EC2_METADATA_DISABLED", "true")
    env.setdefault("AWS_VIRTUAL_HOSTED_STYLE_REQUEST", "false")
    env["STACPKG_OBSTORE_ALLOW_HTTP"] = "true"
    env["STACPKG_S3_ENDPOINT_STACPKG_E2E_S3STORE1"] = s3store1_endpoint
    env["STACPKG_S3_ENDPOINT_STACPKG_E2E_S3STORE2"] = s3store2_endpoint
    os.environ.update(
        {
            "AWS_DEFAULT_REGION": env["AWS_DEFAULT_REGION"],
            "AWS_EC2_METADATA_DISABLED": env["AWS_EC2_METADATA_DISABLED"],
            "AWS_VIRTUAL_HOSTED_STYLE_REQUEST": env["AWS_VIRTUAL_HOSTED_STYLE_REQUEST"],
            "STACPKG_OBSTORE_ALLOW_HTTP": env["STACPKG_OBSTORE_ALLOW_HTTP"],
            "STACPKG_S3_ENDPOINT_STACPKG_E2E_S3STORE1": s3store1_endpoint,
            "STACPKG_S3_ENDPOINT_STACPKG_E2E_S3STORE2": s3store2_endpoint,
        }
    )
    if env.get("AWS_ACCESS_KEY_ID"):
        os.environ["AWS_ACCESS_KEY_ID"] = env["AWS_ACCESS_KEY_ID"]
    if env.get("AWS_SECRET_ACCESS_KEY"):
        os.environ["AWS_SECRET_ACCESS_KEY"] = env["AWS_SECRET_ACCESS_KEY"]
    if env.get("AWS_SESSION_TOKEN"):
        os.environ["AWS_SESSION_TOKEN"] = env["AWS_SESSION_TOKEN"]
    LOGGER.info(
        "configured e2e endpoints: s3store1=%s s3store2=%s",
        s3store1_endpoint,
        s3store2_endpoint,
    )
    return env


def registry_target(repository: str, tag: str) -> str:
    registry = os.environ.get("STACPKG_E2E_REGISTRY")
    if not registry:
        pytest.skip("set STACPKG_E2E_REGISTRY to run OCI registry e2e tests")
    return f"{registry}/{repository}:{tag}"


def _has_s3_credentials(env: dict[str, str], bucket: str) -> bool:
    config = _configured_s3_config(bucket) or {}
    return bool(config.get("access_key_id") and config.get("secret_access_key")) or bool(
        env.get("AWS_ACCESS_KEY_ID") and env.get("AWS_SECRET_ACCESS_KEY")
    )


def _signing_key(secret_key: str, date: str, region: str) -> bytes:
    date_key = hmac.new(f"AWS4{secret_key}".encode(), date.encode(), hashlib.sha256).digest()
    region_key = hmac.new(date_key, region.encode(), hashlib.sha256).digest()
    service_key = hmac.new(region_key, b"s3", hashlib.sha256).digest()
    return hmac.new(service_key, b"aws4_request", hashlib.sha256).digest()


def _signed_s3_request(
    endpoint: str,
    env: dict[str, str],
    method: str,
    bucket: str,
    *,
    key: str | None = None,
    body: bytes = b"",
) -> bytes:
    config = _configured_s3_config(bucket) or {}
    access_key = config.get("access_key_id") or env["AWS_ACCESS_KEY_ID"]
    secret_key = config.get("secret_access_key") or env["AWS_SECRET_ACCESS_KEY"]
    region = (
        config.get("region")
        or config.get("default_region")
        or env.get("AWS_REGION")
        or env.get("AWS_DEFAULT_REGION", "us-east-1")
    )
    endpoint = endpoint.rstrip("/")
    path = f"/{quote(bucket, safe='')}"
    if key is not None:
        path = f"{path}/{quote(key, safe='/~')}"
    url = f"{endpoint}{path}"
    parsed = urlparse(url)
    payload_hash = hashlib.sha256(body).hexdigest()

    from datetime import UTC, datetime

    now = datetime.now(UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = now.strftime("%Y%m%d")
    headers = {
        "host": parsed.netloc,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    session_token = config.get("session_token") or env.get("AWS_SESSION_TOKEN")
    if session_token:
        headers["x-amz-security-token"] = session_token

    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in sorted(headers))
    canonical_request = "\n".join(
        [
            method,
            parsed.path or "/",
            parsed.query,
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )
    credential_scope = f"{date}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )
    signature = hmac.new(
        _signing_key(secret_key, date, region),
        string_to_sign.encode(),
        hashlib.sha256,
    ).hexdigest()
    authorization = (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )
    request_headers = {**headers, "authorization": authorization}
    request = Request(
        url,
        data=None if method == "HEAD" else body,
        headers=request_headers,
        method=method,
    )
    with urlopen(request, timeout=60) as response:
        return response.read()


def _s3_store(endpoint: str, bucket: str):
    client_options = None
    if endpoint.startswith("http://"):
        client_options = {"allow_http": True}
    config = _configured_s3_config(bucket) or {}
    config = {**config, "endpoint": endpoint}
    return from_url(f"s3://{bucket}", config=config, client_options=client_options)


def create_bucket(endpoint: str, env: dict[str, str], bucket: str) -> None:
    LOGGER.debug("ensuring bucket exists: endpoint=%s bucket=%s", endpoint, bucket)
    try:
        _signed_s3_request(endpoint, env, "PUT", bucket)
    except HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        if error.code != 409 or (
            "BucketAlreadyOwnedByYou" not in details and "BucketAlreadyExists" not in details
        ):
            raise
        LOGGER.debug("bucket already exists: %s", bucket)
    else:
        LOGGER.info("created bucket: %s", bucket)


def put_object(
    endpoint: str,
    env: dict[str, str],
    bucket: str,
    key: str,
    body: bytes | Path,
) -> None:
    del env
    payload = body.read_bytes() if isinstance(body, Path) else body
    LOGGER.debug(
        "putting object with obstore: endpoint=%s bucket=%s key=%s size=%s",
        endpoint,
        bucket,
        key,
        len(payload),
    )
    obs.put(_s3_store(endpoint, bucket), key, payload, mode="overwrite")


@contextmanager
def remote_asset_server(root: Path):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    LOGGER.debug("started local remote-asset HTTP server: root=%s url=%s", root, url)
    try:
        yield url
    finally:
        LOGGER.debug("stopping local remote-asset HTTP server: url=%s", url)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def head_size(endpoint: str, env: dict[str, str], bucket: str, key: str) -> int:
    del env
    LOGGER.debug("checking object size: endpoint=%s bucket=%s key=%s", endpoint, bucket, key)
    head = obs.head(_s3_store(endpoint, bucket), key)
    size = int(head["size"])
    LOGGER.debug(
        "object size verified by head request: bucket=%s key=%s size=%s", bucket, key, size
    )
    return size
