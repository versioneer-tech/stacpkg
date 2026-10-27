# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize(
    ("cluster", "message"),
    [
        ("stacpkg-s3-store", "cluster creation requires approval"),
        ("xyz", "only the dedicated stacpkg-s3-store cluster is supported"),
    ],
)
def test_service_runner_rejects_unavailable_or_other_cluster(tmp_path, cluster, message):
    marker = tmp_path / "mutation"
    for tool in ("kind", "kubectl", "htpasswd"):
        script = tmp_path / tool
        body = 'if [[ "$*" == "get clusters" ]]; then exit 0; fi\n' if tool == "kind" else ""
        script.write_text(f"#!/bin/bash\n{body}touch '{marker}'\nexit 1\n")
        script.chmod(0o700)
    result = subprocess.run(
        ["/bin/bash", str(Path(__file__).resolve().parents[2] / "scripts/kind-s3-store-e2e.sh")],
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "STACPKG_E2E_KIND_CLUSTER": cluster,
            "STACPKG_E2E_REGISTRY_PASSWORD": "xyz",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert message in result.stdout, result.stderr
    assert not marker.exists()
