#!/usr/bin/env python3
# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

import argparse
import pathlib
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime

COPYRIGHT_HEADER_VALIDATION_REGEX = re.compile(r"^Copyright")
COPYRIGHT_HEADER_FILE_REGEX = re.compile(r"^# (?:# )?Copyright(.\n?)*(?=\n\n)", re.MULTILINE)
GO_COPYRIGHT_HEADER_FILE_REGEX = re.compile(r"^// (?:# )?Copyright(.\n?)*(?=\n\n)", re.MULTILINE)
GO_APACHE_BLOCK_HEADER_REGEX = re.compile(r"^/\*\nCopyright \d{4}\.\n\n.*?\*/\n*", re.DOTALL)
GO_BUILD_TAGS_REGEX = re.compile(r"^(?://go:build .+\n(?:// \+build .+\n)?\n*)")
SHEBANG_REGEX = re.compile(r"^#!.*\n")
EXCESS_BLANK_LINES_REGEX = re.compile(r"\n{3,}")


def load_copyright_header(filepath: str) -> str:
    _filepath = pathlib.Path(filepath)
    copyright_header_template = _filepath.read_text()
    copyright_header = copyright_header_template.format(year=datetime.now(tz=UTC).year)
    if COPYRIGHT_HEADER_VALIDATION_REGEX.search(copyright_header):
        return copyright_header.rstrip()
    msg = 'Copyright header should start by "Copyright".'
    raise ValueError(msg)


def comment_header(copyright_header: str, comment_prefix: str) -> str:
    return "\n".join(
        f"{comment_prefix} {line}".rstrip() for line in copyright_header.rstrip().split("\n")
    ).rstrip()


def remove_header_match(text: str, match: re.Match[str]) -> str:
    before = text[: match.start()].rstrip("\n")
    after = text[match.end() :].lstrip("\n")
    if before and after:
        return f"{before}\n\n{after}"
    return before or after


def add_go_copyright_header(filepath: pathlib.Path, /, copyright_header: str) -> bool:
    go_copyright_header = comment_header(copyright_header, "//")
    file_content = filepath.read_text()
    build_tags = ""
    body = file_content

    if m := GO_BUILD_TAGS_REGEX.match(file_content):
        build_tags = m.group().strip() + "\n\n"
        body = file_content[m.end() :].lstrip()

    changed = False
    while True:
        if m := GO_APACHE_BLOCK_HEADER_REGEX.match(body):
            body = body[m.end() :].lstrip()
            changed = True
        elif m := GO_COPYRIGHT_HEADER_FILE_REGEX.match(body):
            existing_header = m.group()
            body = body[m.end() :].lstrip()
            changed = changed or existing_header != go_copyright_header
        else:
            break

    new_file_content = f"{build_tags}{go_copyright_header}\n\n{body}"
    if new_file_content == file_content and not changed:
        return False
    filepath.write_text(new_file_content)
    return True


def add_copyright_header(filepath_: str, /, copyright_header: str) -> bool:
    filepath = pathlib.Path(filepath_)
    if filepath.suffix == ".go":
        return add_go_copyright_header(filepath, copyright_header)

    copyright_header = comment_header(copyright_header, "#")
    file_content = filepath.read_text()
    body = file_content
    shebang = ""

    if m := SHEBANG_REGEX.match(body):
        shebang = m.group().rstrip() + "\n"
        body = body[m.end() :].lstrip()

    changed = False
    while True:
        if m := COPYRIGHT_HEADER_FILE_REGEX.search(body):
            existing_header = m.group()
            body = remove_header_match(body, m).lstrip()
            changed = changed or existing_header != copyright_header
        else:
            break
    if filepath.suffix != ".py":
        body = EXCESS_BLANK_LINES_REGEX.sub("\n\n", body)

    body_stripped = body.strip()
    if not body_stripped:
        filepath.write_text(f"{shebang}{copyright_header}\n")
    elif body_stripped == copyright_header and not shebang:
        return changed
    else:
        new_file_content = f"{shebang}{copyright_header}\n\n{body}"
        if new_file_content == file_content and not changed:
            return False
        filepath.write_text(new_file_content)
    return True


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "filenames",
        nargs="*",
        help="List of files changed.",
    )
    parser.add_argument(
        "--file",
        default="copyright.txt",
        help="Path to a file containing the copyright header.",
    )
    args = parser.parse_args(argv)

    passed = True
    copyright_header = load_copyright_header(filepath=args.file)
    for filepath in args.filenames:
        if add_copyright_header(filepath, copyright_header):
            passed = False
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
