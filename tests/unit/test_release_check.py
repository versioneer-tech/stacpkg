# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, 0),
        ({"head_sha": "other"}, 1),
        ({"head_branch": "feature"}, 1),
        ({"event": "pull_request"}, 1),
        ({"conclusion": "failure"}, 1),
        ({"conclusion": "cancelled"}, 1),
        ({"status": "in_progress"}, 1),
    ],
)
def test_release_requires_successful_main_push(changes, expected):
    run = {
        "head_sha": "xyz",
        "head_branch": "main",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
    }
    run.update(changes)
    result = _check(json.dumps({"workflow_runs": [run]}))
    assert result.returncode == expected, result.stderr


@pytest.mark.parametrize("payload", ['{"workflow_runs": []}', "{}", "invalid"])
def test_release_fails_without_check_evidence(payload):
    result = _check(payload)
    assert result.returncode == 1
    assert "no successful main-branch Tests run" in result.stderr


def _check(payload):
    return subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "scripts/check_release_run.py"),
            "xyz",
        ],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
