# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

import runpy

import pytest

from scripts.generate_usecase_tests import UnsupportedCommand, generate_usecase_artifacts


def _source(tmp_path, name, text):
    sources = tmp_path / "sources"
    sources.mkdir(exist_ok=True)
    (sources / name).write_text(text)


def _generate(tmp_path, **options):
    return generate_usecase_artifacts(
        source_dir=tmp_path / "sources",
        markdown_dir=tmp_path / "pages",
        test_dir=tmp_path / "tests",
        **options,
    )


LOCAL_EXAMPLE = (
    "# Copyright 2026, Versioneer\n"
    "# SPDX-License-Identifier: Apache-2.0\n"
    "# title: Field data\n"
    "# test-setup: openaerialmap-items items.parquet --item-count 1\n"
    "stacpkg items from-parquet items.parquet | stacpkg build field.pkg\n"
    "# test-assert: package-items field.pkg 1\n"
)


def test_local_example_produces_a_page_and_a_test(tmp_path):
    _source(tmp_path, "field-data.sh", LOCAL_EXAMPLE)

    _generate(tmp_path)

    page = (tmp_path / "pages/field-data.md").read_text()
    assert "# Field data" in page
    assert "stacpkg items from-parquet items.parquet | stacpkg build field.pkg" in page
    assert "test-setup" not in page
    assert "test-assert" not in page
    assert "SPDX-License-Identifier" not in page
    assert (tmp_path / "tests/test_generated_field_data.py").is_file()


def test_command_output_appears_on_the_page_after_a_successful_run(tmp_path):
    _source(
        tmp_path,
        "greeting.sh",
        '# title: Greeting\nprintf "Files copied\\n"\ntouch copied\n# test-assert: file-exists copied\n',
    )
    artifact = _generate(tmp_path)[0]
    work = tmp_path / "work"
    work.mkdir()

    runpy.run_path(str(artifact.test))["test_greeting"](work)
    _generate(tmp_path)

    page = artifact.markdown.read_text()
    assert "```text\nFiles copied\n```" in page


def test_service_example_produces_a_page_without_a_local_test(tmp_path):
    _source(
        tmp_path,
        "service.sh",
        """\
# title: Service example
# test: false
curl -fsS https://example.test/selection.json --output selection.json
""",
    )

    _generate(tmp_path)

    assert "curl -fsS" in (tmp_path / "pages/service.md").read_text()
    assert not (tmp_path / "tests/test_generated_service.py").exists()


def test_removed_examples_lose_their_generated_pages_and_tests(tmp_path):
    _source(tmp_path, "field-data.sh", LOCAL_EXAMPLE)
    _generate(tmp_path)
    (tmp_path / "sources/field-data.sh").unlink()
    _source(tmp_path, "replacement.sh", LOCAL_EXAMPLE)

    _generate(tmp_path)

    assert not (tmp_path / "pages/field-data.md").exists()
    assert not (tmp_path / "tests/test_generated_field_data.py").exists()
    assert (tmp_path / "pages/replacement.md").is_file()
    assert (tmp_path / "tests/test_generated_replacement.py").is_file()


def test_source_check_does_not_require_generated_files(tmp_path):
    _source(tmp_path, "field-data.sh", LOCAL_EXAMPLE)

    _generate(tmp_path, check=True, write_tests=False)

    assert not (tmp_path / "pages").exists()
    assert not (tmp_path / "tests").exists()


@pytest.mark.parametrize(
    ("check", "message"),
    [
        ("", "must include at least one"),
        ("# test-assert: unknown-check", "unsupported test directive"),
    ],
    ids=["missing-check", "unknown-check"],
)
def test_local_example_requires_a_supported_result_check(tmp_path, check, message):
    _source(tmp_path, "unchecked.sh", "stacpkg inspect field.pkg\n" + check)

    with pytest.raises(UnsupportedCommand, match=message):
        _generate(tmp_path)
