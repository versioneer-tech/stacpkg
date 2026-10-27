# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

"""Check offline handover with the installed distribution."""

from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile

import pyarrow.parquet as pq
import stacpkg


def main():
    with tempfile.TemporaryDirectory(prefix="xyz-stacpkg-smoke-") as directory:
        check(Path(directory))


def check(work):
    source = work / "source"
    source.mkdir()
    payload = b"xyz asset bytes\n"
    (source / "image space.png").write_bytes(payload)
    notes = work / "notes"
    notes.mkdir()
    (notes / "README.txt").write_text("Offline handover.\n")
    collection = {
        "type": "Collection",
        "id": "xyz",
        "description": "Local selection",
        "license": "proprietary",
        "extent": {
            "spatial": {"bbox": [[16, 48, 16, 48]]},
            "temporal": {"interval": [["2025-01-01T00:00:00Z", None]]},
        },
        "links": [],
        "custom:source": "xyz",
    }
    (work / "collection.json").write_text(json.dumps(collection))
    item = {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": "xyz",
        "collection": "xyz",
        "geometry": {"type": "Point", "coordinates": [16, 48]},
        "bbox": [16, 48, 16, 48],
        "properties": {"datetime": "2025-01-01T00:00:00Z", "custom:value": 7},
        "links": [{"rel": "self", "href": (source / "item.json").as_uri()}],
        "assets": {
            "thumbnail": {
                "href": "image space.png",
                "type": "application/octet-stream",
                "file:checksum": "1220" + hashlib.sha256(payload).hexdigest(),
            }
        },
    }
    (source / "item.json").write_text(json.dumps(item))
    command = str(Path(sys.executable).with_name("stacpkg"))

    def run(*arguments, data=None, expected=0):
        result = subprocess.run(
            [command, *map(str, arguments)], input=data, capture_output=True, cwd=work
        )
        assert result.returncode == expected, (
            arguments,
            result.returncode,
            result.stdout,
            result.stderr,
        )
        return result.stdout

    stream = run("items", "from-json", source / "item.json")
    run(
        "build",
        work / "offline.pkg",
        "--include-assets",
        "--includes",
        notes,
        "--collection",
        work / "collection.json",
        data=stream,
    )
    shutil.copytree(work / "offline.pkg", work / "delivered.pkg")
    shutil.rmtree(source)
    report = run("validate", work / "delivered.pkg")
    assert json.loads(report.splitlines()[-1])["valid"] is True
    extracted = json.loads(run("collections", "extract", work / "delivered.pkg/items.parquet"))
    assert extracted == [collection]
    assert (work / "delivered.pkg/notes/README.txt").read_bytes() == (
        notes / "README.txt"
    ).read_bytes()
    stream = run("items", "from-parquet", work / "delivered.pkg/items.parquet")
    run("relocate", work / "second.pkg", "--target", work / "copied assets", data=stream)
    assert json.loads(run("validate", work / "second.pkg").splitlines()[-1])["valid"] is True
    assert json.loads(run("inspect", work / "second.pkg", "--format", "json"))
    lock_path = work / "second.pkg/assets.lock.parquet"
    lock = pq.read_table(lock_path).to_pylist()
    assert len(lock) == 1
    assert Path(lock[0]["key"]).read_bytes() == payload
    before = lock_path.read_bytes()
    asset = Path(lock[0]["key"])
    asset.write_bytes(b"X" + payload[1:])
    report = run("validate", work / "second.pkg", expected=1)
    assert json.loads(report.splitlines()[-1])["valid"] is False
    assert lock_path.read_bytes() == before
    asset.unlink()
    report = run("validate", work / "second.pkg", expected=1)
    assert json.loads(report.splitlines()[-1])["valid"] is False
    assert lock_path.read_bytes() == before
    print("Installed wheel:", stacpkg.__file__)
    print(
        "CLI smoke: offline build, source removal, Collection extraction, notes, relocation, validation and inspection passed."
    )
    print("Changed and missing assets failed validation and preserved the prior lock.")


if __name__ == "__main__":
    main()
