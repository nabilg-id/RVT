"""Config values that reach strict third-party parsers.

faster-whisper rejects ``"medium "`` with "Invalid model size", which does not
point at the real cause. Windows ``set VAR=tiny && next`` puts a space inside
the value, and hand-edited .env lines carry whitespace too, so every string
config value is stripped on the way in.

config.py reads the environment at import time, so each case sets the
environment first and only then re-imports the module.
"""
from __future__ import annotations

import importlib
import sys

import dotenv
import pytest

from rch.core import paths as paths_mod


def reload_config(monkeypatch, **env):
    """Re-import clipper.config with ``env`` applied.

    ``clipper/.env`` is loaded on import, so it has to be neutralised first or
    a developer's local values leak into the assertions. Only clipper.config is
    purged afterwards: other modules keep the objects they already imported, so
    this cannot leave the rest of the session holding a half-rewired package.
    """
    real_load = dotenv.load_dotenv
    monkeypatch.setattr(
        dotenv, "load_dotenv", lambda *a, **k: False, raising=False
    )

    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)

    sys.modules.pop("clipper.config", None)
    module = importlib.import_module("clipper.config")
    # Restore so the finalizer can load the real .env again.
    monkeypatch.setattr(dotenv, "load_dotenv", real_load, raising=False)
    return module


@pytest.fixture(autouse=True)
def _restore_config():
    yield
    sys.modules.pop("clipper.config", None)
    importlib.import_module("clipper.config")


class TestWhitespaceIsStripped:
    def test_whisper_model_loses_trailing_space(self, monkeypatch):
        # Exactly what `set WHISPER_MODEL=tiny && py -3 ...` produces in cmd.
        c = reload_config(monkeypatch, WHISPER_MODEL="tiny ")
        assert c.WHISPER_MODEL == "tiny"

    def test_whisper_model_loses_leading_space(self, monkeypatch):
        c = reload_config(monkeypatch, WHISPER_MODEL="  medium")
        assert c.WHISPER_MODEL == "medium"

    def test_whisper_model_loses_tab_and_newline(self, monkeypatch):
        c = reload_config(monkeypatch, WHISPER_MODEL="\tbase\n")
        assert c.WHISPER_MODEL == "base"

    def test_openrouter_model_is_stripped(self, monkeypatch):
        c = reload_config(monkeypatch, OPENROUTER_MODEL=" openrouter/free ")
        assert c.OPENROUTER_MODEL == "openrouter/free"

    def test_openrouter_key_is_stripped(self, monkeypatch):
        c = reload_config(monkeypatch, OPENROUTER_API_KEY="  sk-or-v1-abc  ")
        assert c.OPENROUTER_API_KEY == "sk-or-v1-abc"

    def test_host_is_stripped(self, monkeypatch):
        c = reload_config(monkeypatch, RCH_HOST=" 127.0.0.1 ")
        assert c.RCH_HOST == "127.0.0.1"


class TestEmptyBecomesNone:
    """An all-whitespace value is "unset", not a value made of spaces."""

    def test_whitespace_only_language_is_none(self, monkeypatch):
        c = reload_config(monkeypatch, WHISPER_LANGUAGE="   ")
        assert c.WHISPER_LANGUAGE is None

    def test_whitespace_only_key_is_falsy(self, monkeypatch):
        # Must stay falsy, or AISelector builds a client with a blank key.
        c = reload_config(monkeypatch, OPENROUTER_API_KEY="   ")
        assert not c.OPENROUTER_API_KEY

    def test_whitespace_only_user_agent_is_none(self, monkeypatch):
        c = reload_config(monkeypatch, YOUTUBE_USER_AGENT="  ")
        assert c.YOUTUBE_USER_AGENT is None


class TestDefaultsSurvive:
    def test_defaults_are_used_when_set_to_empty(self, monkeypatch):
        c = reload_config(
            monkeypatch,
            WHISPER_MODEL="", WHISPER_LANGUAGE="", OPENROUTER_MODEL="",
            RCH_HOST="", RCH_PORT="",
        )
        assert c.WHISPER_MODEL == "medium"
        assert c.WHISPER_LANGUAGE is None
        assert c.OPENROUTER_MODEL == "openrouter/free"
        assert c.RCH_HOST == "127.0.0.1"
        assert c.RCH_PORT == 8787

    def test_paths_fall_back_to_repo_root(self, monkeypatch):
        c = reload_config(
            monkeypatch,
            OUTPUT_DIR="", TEMP_DIR="", ASSET_DIR="", COOKIES_FILE="",
        )
        assert c.OUTPUT_DIR.name == "clips"
        assert c.TEMP_DIR.name == "temp"
        assert c.ASSET_DIR.name == "asset"
        assert c.COOKIES_FILE.name == "cookies.txt"

    def test_unset_env_uses_the_documented_defaults(self, monkeypatch):
        c = reload_config(
            monkeypatch,
            WHISPER_MODEL=None, WHISPER_LANGUAGE=None, OPENROUTER_MODEL=None,
        )
        assert c.WHISPER_MODEL == "medium"
        assert c.WHISPER_LANGUAGE is None
        assert c.OPENROUTER_MODEL == "openrouter/free"


class TestNumericValues:
    def test_numbers_still_parse_with_padding(self, monkeypatch):
        c = reload_config(monkeypatch, RCH_PORT=" 9000 ", RCH_HISTORY_LIMIT=" 5 ")
        assert c.RCH_PORT == 9000
        assert c.RCH_HISTORY_LIMIT == 5

    def test_empty_numeric_falls_back_rather_than_raising(self, monkeypatch):
        # int("") would raise; a blank value should behave as unset.
        c = reload_config(monkeypatch, RCH_PORT="   ", RCH_HISTORY_LIMIT="")
        assert c.RCH_PORT == 8787
        assert c.RCH_HISTORY_LIMIT == 20


class TestUnusableOutputDirectoryDoesNotKillTheApp:
    """An output folder that cannot be created must not stop the import.

    Since the default output moved out of the repo into the OS Downloads folder,
    this mkdir can fail for reasons the user cannot do anything about: OneDrive
    not signed in, a folder whose ACL denies writing, a full disk, a network
    share that is offline. It runs unguarded at import time, so the exception did
    not fail one download - it killed ``clipper.config``, which takes the GUI and
    the CLI down with it, both on a traceback.

    ``rch.core.paths.ensure_dir`` exists precisely to absorb this, and was not
    called from production code anywhere.
    """

    def test_import_survives_an_uncreatable_output_dir(self, tmp_path,
                                                      monkeypatch):
        blocker = tmp_path / "a-regular-file"
        blocker.write_text("not a directory", encoding="utf-8")

        # ensure_dir's fallback is <repo>/downloads. Pointed at tmp_path so the
        # test does not litter the working tree - and so a leftover folder
        # cannot make the assertion pass by accident.
        monkeypatch.setattr(paths_mod, "_repo_root", lambda: tmp_path)

        # No exception: that is the whole assertion.
        c = reload_config(monkeypatch, OUTPUT_DIR=str(blocker / "clips"))

        assert c.OUTPUT_DIR.is_dir(), "the fallback must be a usable directory"

    def test_the_fallback_is_actually_writable(self, tmp_path, monkeypatch):
        blocker = tmp_path / "a-regular-file"
        blocker.write_text("not a directory", encoding="utf-8")
        monkeypatch.setattr(paths_mod, "_repo_root", lambda: tmp_path)

        c = reload_config(monkeypatch, OUTPUT_DIR=str(blocker / "clips"))

        probe = c.OUTPUT_DIR / "probe.mp4"
        probe.write_bytes(b"\x00" * 8)
        assert probe.is_file()

    def test_an_uncreatable_temp_dir_also_survives(self, tmp_path, monkeypatch):
        blocker = tmp_path / "a-regular-file"
        blocker.write_text("not a directory", encoding="utf-8")
        monkeypatch.setattr(paths_mod, "_repo_root", lambda: tmp_path)

        c = reload_config(monkeypatch, TEMP_DIR=str(blocker / "temp"))

        assert c.TEMP_DIR.is_dir()

    def test_a_usable_output_dir_is_still_honoured(self, tmp_path, monkeypatch):
        """Guard against over-correcting into always using the fallback."""
        wanted = tmp_path / "keluaran"

        c = reload_config(monkeypatch, OUTPUT_DIR=str(wanted))

        assert c.OUTPUT_DIR == wanted


class TestHelperUnits:
    def test_env_str_returns_default(self, monkeypatch):
        c = reload_config(monkeypatch)
        assert c._env_str("RCH_DEFINITELY_UNSET_XYZ") == ""
        assert c._env_str("RCH_DEFINITELY_UNSET_XYZ", "fallback") == "fallback"

    def test_env_opt_maps_empty_to_none(self, monkeypatch):
        c = reload_config(monkeypatch, RCH_EMPTY_XYZ="")
        assert c._env_opt("RCH_EMPTY_XYZ") is None

    def test_env_str_survives_getenv_returning_none(self, monkeypatch):
        c = reload_config(monkeypatch)
        monkeypatch.setattr(c.os, "getenv", lambda name, default=None: None)
        assert c._env_str("ANYTHING_AT_ALL") == ""
        assert c._env_opt("ANYTHING_AT_ALL") is None


class TestStrippingFeedsWhisperAValidSize:
    """The reason for stripping: the value must pass faster-whisper's own
    validation, which is what raised the original error."""

    VALID = {
        "tiny.en", "tiny", "base.en", "base", "small.en", "small",
        "medium.en", "medium", "large-v1", "large-v2", "large-v3", "large",
        "distil-large-v2", "distil-medium.en", "distil-small.en",
        "distil-large-v3", "distil-large-v3.5", "large-v3-turbo", "turbo",
    }

    @pytest.mark.parametrize("raw", ["tiny ", " tiny", "\ttiny\n", " tiny "])
    def test_padded_sizes_are_accepted(self, monkeypatch, raw):
        c = reload_config(monkeypatch, WHISPER_MODEL=raw)
        assert c.WHISPER_MODEL in self.VALID

    def test_unpadded_size_would_have_been_rejected(self, monkeypatch):
        """Guards that this test is actually testing the stripping: the raw
        padded value is not itself a valid size."""
        assert "tiny " not in self.VALID