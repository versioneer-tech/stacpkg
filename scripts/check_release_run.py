# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

"""Require successful main-branch checks for the release commit."""

import json
import sys


def main() -> int:
    commit = sys.argv[1]
    try:
        runs = json.load(sys.stdin)["workflow_runs"]
        passed = any(
            run.get("head_sha") == commit
            and run.get("head_branch") == "main"
            and run.get("event") == "push"
            and run.get("status") == "completed"
            and run.get("conclusion") == "success"
            for run in runs
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        passed = False
    if not passed:
        print(f"Release commit {commit} has no successful main-branch Tests run.", file=sys.stderr)
        return 1
    print(f"Release commit {commit} passed main-branch tests.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
