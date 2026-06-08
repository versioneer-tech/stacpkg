# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import mimetypes
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from oras.client import OrasClient

from stacpkg.dataset import ASSET_LOCK_PACKAGE_PATH, ITEMS_PACKAGE_PATH

ARTIFACT_TYPE = "application/vnd.stacpkg.package.v1+json"
OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
EMPTY_CONFIG_MEDIA_TYPE = "application/vnd.oci.empty.v1+json"
EMPTY_CONFIG_DIGEST = "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
EMPTY_CONFIG_SIZE = 2
ITEMS_MEDIA_TYPE = "application/vnd.stacpkg.items.v1.parquet"
ASSET_LOCK_MEDIA_TYPE = "application/vnd.stacpkg.asset-lock.v1.parquet"
FILES_ZIP_MEDIA_TYPE = "application/vnd.stacpkg.files.v1+zip"
ASSET_MEDIA_TYPE = "application/vnd.stacpkg.asset.v1"
ASSET_ZIP_MEDIA_TYPE = "application/vnd.stacpkg.asset.v1+zip"
TITLE_ANNOTATION = "org.opencontainers.image.title"
CONFIG_NAME = "empty-config.json"


@dataclass(frozen=True)
class _LayerFile:
    path: Path
    media_type: str
    title: str


def _oras_client(
    *,
    plain_http: bool = False,
    insecure: bool = False,
) -> OrasClient:
    return OrasClient(insecure=plain_http, tls_verify=not insecure)


def _media_type(path: Path) -> str:
    if path.name.endswith(".parquet"):
        return "application/vnd.apache.parquet"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _safe_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"unsafe package path: {value}")
    return path.as_posix()


def _safe_output_path(output_dir: Path, relative_path: str) -> Path:
    relative_path = _safe_relative_path(relative_path)
    output_root = output_dir.resolve()
    target = (output_dir / relative_path).resolve()
    if output_root != target and output_root not in target.parents:
        raise ValueError(f"package path escapes output directory: {relative_path}")
    return target


def _write_zip(source_dir: Path, archive_path: Path, *, package_root: Path) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w") as archive:
        for path in sorted(child for child in source_dir.rglob("*") if child.is_file()):
            relative_path = path.relative_to(package_root).as_posix()
            info = zipfile.ZipInfo(_safe_relative_path(relative_path))
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (path.stat().st_mode & 0xFFFF) << 16
            archive.writestr(info, path.read_bytes())


def _extract_zip(archive_path: Path, output_dir: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            target = _safe_output_path(output_dir, member.filename)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)


def _package_layers(package_dir: Path, temp_dir: Path) -> list[_LayerFile]:
    items_path = package_dir / ITEMS_PACKAGE_PATH
    assets_path = package_dir / ASSET_LOCK_PACKAGE_PATH
    missing = [path.name for path in (items_path, assets_path) if not path.exists()]
    if missing:
        raise ValueError(
            f"not a stacpkg package directory: {package_dir} missing {', '.join(missing)}"
        )

    layers = [
        _LayerFile(items_path, ITEMS_MEDIA_TYPE, ITEMS_PACKAGE_PATH),
        _LayerFile(assets_path, ASSET_LOCK_MEDIA_TYPE, ASSET_LOCK_PACKAGE_PATH),
    ]
    for entry in sorted(package_dir.iterdir()):
        if entry.name in {ITEMS_PACKAGE_PATH, ASSET_LOCK_PACKAGE_PATH}:
            continue
        if entry.name == "manifest.json":
            continue
        relative_path = entry.relative_to(package_dir).as_posix()
        if entry.name == "assets" and entry.is_dir():
            for asset_path in sorted(child for child in entry.rglob("*") if child.is_file()):
                layers.append(
                    _LayerFile(
                        asset_path,
                        ASSET_MEDIA_TYPE,
                        asset_path.relative_to(package_dir).as_posix(),
                    )
                )
            continue
        if entry.is_dir():
            archive_path = temp_dir / f"{entry.name}.zip"
            _write_zip(entry, archive_path, package_root=package_dir)
            layers.append(_LayerFile(archive_path, FILES_ZIP_MEDIA_TYPE, relative_path))
            continue
        if entry.is_file():
            layers.append(_LayerFile(entry, _media_type(entry), relative_path))
    return layers


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _layer_descriptor(layer: _LayerFile) -> dict[str, object]:
    return {
        "mediaType": layer.media_type,
        "digest": f"sha256:{_sha256(layer.path)}",
        "size": layer.path.stat().st_size,
        "annotations": {
            TITLE_ANNOTATION: _safe_relative_path(layer.title),
        },
    }


def _empty_config_descriptor() -> dict[str, object]:
    return {
        "mediaType": EMPTY_CONFIG_MEDIA_TYPE,
        "digest": EMPTY_CONFIG_DIGEST,
        "size": EMPTY_CONFIG_SIZE,
    }


def push_package(
    package_dir: str | Path,
    target: str,
    *,
    plain_http: bool = False,
    insecure: bool = False,
) -> None:
    with tempfile.TemporaryDirectory(prefix="stacpkg-oci-push-") as temp:
        temp_dir = Path(temp)
        layers = _package_layers(Path(package_dir), temp_dir)
        config_path = temp_dir / CONFIG_NAME
        config_path.write_text("{}", encoding="utf-8")

        client = _oras_client(plain_http=plain_http, insecure=insecure)
        container = client.get_container(target)
        client.auth.load_configs(container)

        layer_descriptors = []
        for layer in layers:
            descriptor = _layer_descriptor(layer)
            response = client.upload_blob(str(layer.path), container, descriptor)
            client._check_200_response(response)
            layer_descriptors.append(descriptor)

        config_descriptor = _empty_config_descriptor()
        response = client.upload_blob(str(config_path), container, config_descriptor)
        client._check_200_response(response)

        manifest = {
            "schemaVersion": 2,
            "mediaType": OCI_MANIFEST_MEDIA_TYPE,
            "artifactType": ARTIFACT_TYPE,
            "config": config_descriptor,
            "layers": layer_descriptors,
        }
        response = client.upload_manifest(manifest, container)
        client._check_200_response(response)


def pull_package(
    source: str,
    output_dir: str | Path,
    *,
    plain_http: bool = False,
    insecure: bool = False,
) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="stacpkg-oci-pull-") as temp:
        temp_dir = Path(temp)
        client = _oras_client(plain_http=plain_http, insecure=insecure)
        manifest = client.get_manifest(source)

        for index, layer in enumerate(manifest.get("layers", [])):
            media_type = layer.get("mediaType")
            annotations = layer.get("annotations") or {}
            title = annotations.get(TITLE_ANNOTATION)
            if media_type == ITEMS_MEDIA_TYPE:
                target_path = output_dir / ITEMS_PACKAGE_PATH
            elif media_type == ASSET_LOCK_MEDIA_TYPE:
                target_path = output_dir / ASSET_LOCK_PACKAGE_PATH
            elif media_type in {FILES_ZIP_MEDIA_TYPE, ASSET_ZIP_MEDIA_TYPE}:
                archive_path = temp_dir / f"layer-{index}.zip"
                client.download_blob(source, layer["digest"], str(archive_path))
                _extract_zip(archive_path, output_dir)
                continue
            elif media_type == ASSET_MEDIA_TYPE or title:
                if not title:
                    raise RuntimeError(f"OCI layer {index} is missing {TITLE_ANNOTATION}")
                target_path = _safe_output_path(output_dir, title)
            else:
                raise RuntimeError(f"unsupported OCI package layer media type: {media_type}")

            target_path.parent.mkdir(parents=True, exist_ok=True)
            client.download_blob(source, layer["digest"], str(target_path))

    if not (output_dir / ITEMS_PACKAGE_PATH).exists():
        raise RuntimeError(f"OCI artifact did not contain {ITEMS_PACKAGE_PATH}")
    if not (output_dir / ASSET_LOCK_PACKAGE_PATH).exists():
        raise RuntimeError(f"OCI artifact did not contain {ASSET_LOCK_PACKAGE_PATH}")
