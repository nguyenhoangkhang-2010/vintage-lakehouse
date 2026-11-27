"""The repository carries a licence, and the package metadata points at it."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_licence_file_is_the_mit_licence() -> None:
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License\n")
    assert re.search(r"^Copyright \(c\) 20\d\d \S", text, flags=re.MULTILINE)
    assert "Permission is hereby granted, free of charge" in text
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in text


def test_the_package_metadata_points_at_the_licence() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'license = { file = "LICENSE" }' in pyproject
    assert '"License :: OSI Approved :: MIT License"' in pyproject


def test_the_readme_says_the_licence_covers_the_code_and_not_the_data() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "[MIT licence](LICENSE)" in readme
    assert "source code only" in readme
