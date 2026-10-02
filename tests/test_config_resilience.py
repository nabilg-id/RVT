"""A malformed config must degrade to defaults, not break every entrypoint.

``CONFIG`` is built at module import time. A typo in ``.rchrc.json`` used to
raise ``ValueError`` out of the import, so ``rch --version`` and the web GUI
both died before printing anything. The config file is user-owned, so bad
input there is a normal condition to tolerate, not an exception.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import rch.config as config_mod

_NUMERIC_DEFAULTS = {
    "sleep_requests": 0.5,
    "sleep_interval": 0.5,
    "max_sleep_interval": 2,
    "retries": 3,
    "concurrency": 2,
}


@pytest.fixture()
def reloaded(tmp_path, monkeypatch):
    """Re-import rch.config with cwd pointed at an empty directory."""
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.delenv("HOME", raising=False)

    def _reload():
        return importlib.reload(config_mod)

    return _reload


def _write_rcrc(workdir, payload):
    (workdir / ".rchrc.json").write_text(json.dumps(payload), encoding="utf-8")


class TestNumericCoercion:
    @pytest.mark.parametrize("key", sorted(_NUMERIC_DEFAULTS))
    def test_garbage_value_falls_back_to_default(self, reloaded, tmp_path, key):
        _write_rcrc(tmp_path / "cwd", {key: "not-a-number"})

        cfg = reloaded().CONFIG

        assert cfg[key] == _NUMERIC_DEFAULTS[key]

    @pytest.mark.parametrize("key", sorted(_NUMERIC_DEFAULTS))
    def test_numeric_string_is_still_honoured(self, reloaded, tmp_path, key):
        _write_rcrc(tmp_path / "cwd", {key: "9"})

        assert reloaded().CONFIG[key] == 9

    @pytest.mark.parametrize("key", sorted(_NUMERIC_DEFAULTS))
    def test_none_value_falls_back_to_default(self, reloaded, tmp_path, key):
        _write_rcrc(tmp_path / "cwd", {key: None})

        assert reloaded().CONFIG[key] == _NUMERIC_DEFAULTS[key]

    @pytest.mark.parametrize("key", sorted(_NUMERIC_DEFAULTS))
    def test_list_value_falls_back_to_default(self, reloaded, tmp_path, key):
        _write_rcrc(tmp_path / "cwd", {key: [1, 2]})

        assert reloaded().CONFIG[key] == _NUMERIC_DEFAULTS[key]

    @pytest.mark.parametrize("key", sorted(_NUMERIC_DEFAULTS))
    def test_empty_string_falls_back_to_default(self, reloaded, tmp_path, key):
        _write_rcrc(tmp_path / "cwd", {key: ""})

        assert reloaded().CONFIG[key] == _NUMERIC_DEFAULTS[key]

    def test_float_string_is_accepted_for_float_keys(self, reloaded, tmp_path):
        _write_rcrc(tmp_path / "cwd", {"sleep_requests": "1.5"})

        assert reloaded().CONFIG["sleep_requests"] == 1.5


class TestStringKeys:
    def test_quality_garbage_does_not_crash(self, reloaded, tmp_path):
        _write_rcrc(tmp_path / "cwd", {"quality": {"nested": True}})

        assert reloaded().CONFIG["quality"] == "720p"

    def test_user_agent_garbage_does_not_crash(self, reloaded, tmp_path):
        _write_rcrc(tmp_path / "cwd", {"user_agent": 12345})

        assert isinstance(reloaded().CONFIG["user_agent"], str)


class TestVersionSurvivesBadConfig:
    """The CLI must still start when ``.rchrc.json`` holds a bad value.

    ``CONFIG`` is built at import, so a ``ValueError`` there breaks
    ``--version`` and ``--help`` too, not just the download commands.
    """

    def _run(self, tmp_path, *args):
        repo_root = Path(__file__).resolve().parent.parent
        return subprocess.run(
            [sys.executable, "-m", "rch", *args],
            capture_output=True, text=True, timeout=60,
            cwd=str(tmp_path),
            env={
                **os.environ,
                "PYTHONPATH": str(repo_root),
                "HOME": str(tmp_path),
                "USERPROFILE": str(tmp_path),
            },
        )

    def test_rch_version_runs_with_a_corrupt_rchrc(self, tmp_path):
        (tmp_path / ".rchrc.json").write_text('{"retries": "abc"}', encoding="utf-8")

        proc = self._run(tmp_path, "--version")

        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip()

    def test_rch_help_runs_with_a_corrupt_rchrc(self, tmp_path):
        (tmp_path / ".rchrc.json").write_text('{"concurrency": "x"}', encoding="utf-8")

        proc = self._run(tmp_path, "--help")

        assert proc.returncode == 0, proc.stderr
        assert "Usage" in proc.stdout
