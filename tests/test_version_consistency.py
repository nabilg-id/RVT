"""The three version declarations must agree.

The release workflow reads ``rch/__init__.py``, ``clipper/__init__.py``, and
``pyproject.toml`` and refuses to publish when they disagree with the git tag.
That check only runs after a push; this one runs in the suite, so a forgotten
bump fails locally instead of turning into a red release.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import tomllib

import clipper
import rch

REPO_ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)["project"]["version"]


class TestVersionsAgree:
    def test_rch_matches_pyproject(self):
        assert rch.__version__ == _pyproject_version()

    def test_clipper_matches_pyproject(self):
        assert clipper.__version__ == _pyproject_version()

    def test_all_three_are_identical(self):
        versions = {rch.__version__, clipper.__version__, _pyproject_version()}
        assert len(versions) == 1, f"version drift: {versions}"

    def test_version_looks_like_a_release(self):
        assert re.fullmatch(r"\d+\.\d+\.\d+", rch.__version__)

    def test_project_name_is_not_stale(self):
        with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
            name = tomllib.load(fh)["project"]["name"]
        assert "harvester" not in name.lower(), (
            "nama paket masih menyebut harvester padahal repo ini sudah clipper"
        )


class TestRequiredFilesExist:
    @pytest.mark.parametrize(
        "rel",
        [
            "requirements.txt",
            "requirements-dev.txt",
            "pyproject.toml",
            "ruff.toml",
            "pytest.ini",
            "clipper/app.py",
            "clipper/progress.py",
            "clipper/main.py",
            "clipper/templates/index.html",
            "clipper/static/app.js",
            "clipper/static/style.css",
            "clipper/.env.example",
            "launchers/vclip.bat",
            "launchers/vclip.sh",
            "docs/CLIPPER.md",
        ],
    )
    def test_file_is_present(self, rel):
        assert (REPO_ROOT / rel).exists(), f"{rel} hilang"

    def test_setup_py_is_gone(self):
        """setup.py was replaced by pyproject.toml; the release workflow read it."""
        assert not (REPO_ROOT / "setup.py").exists()