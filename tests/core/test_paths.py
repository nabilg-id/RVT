"""Tests for rch.core.paths - the per-OS Downloads folder.

The point of this module is that a harvester run from any working directory
lands in the same place, and that it never writes into a developer's real
Downloads folder during a test run.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from rch.core import paths as P

REPO_ROOT = P._repo_root()


@pytest.fixture()
def no_override(monkeypatch):
    monkeypatch.delenv("RCH_DOWNLOADS_DIR", raising=False)


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    """A home directory that exists, so the ~/Downloads probe is real."""
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


class TestOverride:
    def test_absolute_override_wins(self, tmp_path, no_override):
        target = tmp_path / "saya-pilih"
        os.environ["RCH_DOWNLOADS_DIR"] = str(target)
        assert P.default_downloads_dir() == target

    def test_relative_override_is_anchored_to_the_repo(self, no_override):
        os.environ["RCH_DOWNLOADS_DIR"] = "./unduhan"
        try:
            result = P.default_downloads_dir()
            assert result.is_absolute()
            assert result.name == "unduhan"
            assert result.parent == REPO_ROOT
        finally:
            os.environ.pop("RCH_DOWNLOADS_DIR", None)

    def test_blank_override_is_ignored(self, no_override, monkeypatch):
        monkeypatch.setattr(P, "_from_environment", lambda: None)
        assert P.default_downloads_dir().is_absolute()

    def test_tilde_in_override_is_expanded(self, monkeypatch, tmp_path):
        home = tmp_path / "h"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("RCH_DOWNLOADS_DIR", "~/Dl")
        result = P.default_downloads_dir()
        assert "~" not in str(result)
        assert result.is_absolute()


class TestPerPlatform:
    def test_posix_uses_home_downloads(self, fake_home, monkeypatch, no_override):
        monkeypatch.setattr(P.sys, "platform", "darwin")
        assert P.default_downloads_dir() == fake_home / "Downloads"

    def test_linux_prefers_xdg(self, fake_home, monkeypatch, no_override):
        xdg = fake_home / "Unduhan"
        xdg.mkdir()
        config = fake_home / ".config"
        config.mkdir()
        (config / "user-dirs.dirs").write_text(
            'XDG_DOWNLOAD_DIR="$HOME/Unduhan"\n', encoding="utf-8"
        )
        monkeypatch.setattr(P.sys, "platform", "linux")
        assert P.default_downloads_dir() == xdg

    def test_linux_falls_back_to_home_downloads(self, fake_home, monkeypatch,
                                               no_override):
        monkeypatch.setattr(P.sys, "platform", "linux")
        assert P.default_downloads_dir() == fake_home / "Downloads"

    def test_windows_uses_shell_then_userprofile(self, fake_home, monkeypatch,
                                                 no_override):
        monkeypatch.setattr(P.sys, "platform", "win32")
        # Force the shell probe to fail so the %USERPROFILE% path is exercised,
        # which is what runs on a Windows machine where the shell call errors.
        monkeypatch.setattr(P, "_windows_downloads", lambda: None)
        assert P.default_downloads_dir() == fake_home / "Downloads"


class TestXdgParsing:
    def test_commented_line_is_ignored(self, fake_home, monkeypatch):
        xdg = fake_home / "Unduhan"
        xdg.mkdir()
        config = fake_home / ".config"
        config.mkdir()
        (config / "user-dirs.dirs").write_text(
            '# XDG_DOWNLOAD_DIR="$HOME/JanganDipakai"\n'
            'XDG_DOWNLOAD_DIR="$HOME/Unduhan"\n',
            encoding="utf-8",
        )
        assert P._linux_xdg_downloads() == xdg

    def test_brace_form_variable_is_expanded(self, fake_home):
        xdg = fake_home / "Unduhan"
        xdg.mkdir()
        config = fake_home / ".config"
        config.mkdir()
        (config / "user-dirs.dirs").write_text(
            'XDG_DOWNLOAD_DIR="${HOME}/Unduhan"\n', encoding="utf-8"
        )
        assert P._linux_xdg_downloads() == xdg

    def test_nonexistent_target_is_rejected(self, fake_home):
        config = fake_home / ".config"
        config.mkdir()
        (config / "user-dirs.dirs").write_text(
            'XDG_DOWNLOAD_DIR="$HOME/TidakAda"\n', encoding="utf-8"
        )
        assert P._linux_xdg_downloads() is None

    def test_corrupt_config_is_not_fatal(self, fake_home):
        config = fake_home / ".config"
        config.mkdir()
        (config / "user-dirs.dirs").write_bytes(b"\xff\xfe\x00broken")
        assert P._linux_xdg_downloads() is None

    def test_missing_config_returns_none(self, fake_home):
        assert P._linux_xdg_downloads() is None


class TestWindowsShellProbe:
    """Runs for real on Windows, and must be inert everywhere else.

    On a Windows machine the shell answers with the genuine Downloads folder,
    which is the whole point of using SHGetKnownFolderPath instead of
    %USERPROFILE%. Off Windows the WinDLL load fails and it has to degrade to
    None rather than raise.
    """

    def test_never_raises(self):
        result = P._windows_downloads()
        assert result is None or isinstance(result, Path)

    @pytest.mark.skipif(sys.platform != "win32", reason="hanya relevan di Windows")
    def test_resolves_to_a_real_directory_on_windows(self):
        result = P._windows_downloads()
        if result is not None:
            assert result.is_absolute()
            assert result.is_dir()

    @pytest.mark.skipif(sys.platform != "win32", reason="hanya relevan di Windows")
    def test_follows_onedrive_when_it_moved_the_folder(self, tmp_path, monkeypatch,
                                                      no_override):
        """The reason for the shell call at all: OneDrive relocates Downloads, so
        the plain USERPROFILE path can point at a folder that does not exist.
        A shell result outside USERPROFILE is therefore accepted rather than
        second-guessed."""
        real = tmp_path / "OneDrive" / "Unduhan"
        real.mkdir(parents=True)
        monkeypatch.setattr(P, "_windows_downloads", lambda: real)
        monkeypatch.setattr(P.sys, "platform", "win32")

        assert P.default_downloads_dir() == real


class TestFallbacks:
    def test_falls_back_to_repo_downloads_when_nothing_is_usable(
        self, tmp_path, monkeypatch, no_override
    ):
        monkeypatch.setattr(P.sys, "platform", "linux")
        monkeypatch.setattr(P, "_linux_xdg_downloads", lambda: None)
        monkeypatch.setattr(Path, "home", classmethod(
            lambda cls: tmp_path / "no-such-home"))
        result = P.default_downloads_dir()
        assert result == REPO_ROOT / "downloads"

    def test_result_is_always_absolute(self, tmp_path, monkeypatch, no_override):
        monkeypatch.setattr(P.sys, "platform", "linux")
        assert P.default_downloads_dir().is_absolute()

    def test_a_raising_probe_does_not_propagate(self, monkeypatch, no_override):
        def _boom():
            raise OSError("shell on fire")

        monkeypatch.setattr(P, "_linux_xdg_downloads", _boom)
        monkeypatch.setattr(P.sys, "platform", "linux")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/nonexistent")))
        assert P.default_downloads_dir().is_absolute()


class TestShellBindingIsIsolated:
    """The ctypes call is the only part that needs Windows, so it is isolated in
    _shell_downloads_path and everything above it is testable anywhere."""

    def test_no_ctypes_means_no_answer(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def _refuse(name, *a, **k):
            if name == "ctypes":
                raise ImportError("no ctypes here")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _refuse)
        assert P._shell_downloads_path() is None

    def test_windows_never_raises(self):
        assert P._shell_downloads_path() is None or isinstance(
            P._shell_downloads_path(), str
        )


class TestWindowsDecision:
    def test_empty_answer_is_not_a_path(self, monkeypatch, tmp_path):
        """A shell answering with nothing must not become Path('')."""
        monkeypatch.setattr(P, "_shell_downloads_path", lambda: None)
        assert P._windows_downloads() is None

    def test_empty_string_is_rejected(self, monkeypatch):
        monkeypatch.setattr(P, "_shell_downloads_path", lambda: "")
        assert P._windows_downloads() is None

    def test_existing_directory_is_accepted(self, monkeypatch, tmp_path):
        monkeypatch.setattr(P, "_shell_downloads_path",
                            lambda: str(tmp_path))
        assert P._windows_downloads() == tmp_path

    def test_missing_directory_is_rejected(self, monkeypatch, tmp_path):
        """OneDrive can leave a stale entry pointing nowhere."""
        gone = tmp_path / "hilang"
        monkeypatch.setattr(P, "_shell_downloads_path", lambda: str(gone))
        assert P._windows_downloads() is None


class TestXdgReadFailure:
    def test_unreadable_config_is_not_fatal(self, fake_home, monkeypatch):
        """A user-dirs.dirs we cannot read must fall through to ~/Downloads."""
        config = fake_home / ".config"
        config.mkdir()
        config_file = config / "user-dirs.dirs"
        config_file.write_text('XDG_DOWNLOAD_DIR="$HOME/Unduhan"\n',
                               encoding="utf-8")

        real_read = Path.read_text

        def _boom(self, *a, **k):
            if self.name == "user-dirs.dirs":
                raise OSError("permission denied")
            return real_read(self, *a, **k)

        monkeypatch.setattr(Path, "read_text", _boom)
        assert P._linux_xdg_downloads() is None


class TestCandidateLoop:
    def test_a_failing_candidate_does_not_stop_the_search(
        self, fake_home, monkeypatch, no_override
    ):
        monkeypatch.setattr(P.sys, "platform", "linux")

        calls = []

        def _first():
            calls.append("xdg")
            raise OSError("boom")

        def _second():
            calls.append("home")
            return fake_home / "Downloads"

        monkeypatch.setattr(P, "_linux_xdg_downloads", _first)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        assert P.default_downloads_dir() == fake_home / "Downloads"
        assert calls == ["xdg"]

    def test_a_probe_returning_none_is_skipped(self, tmp_path, monkeypatch,
                                              no_override):
        monkeypatch.setattr(P.sys, "platform", "win32")
        monkeypatch.setattr(P, "_windows_downloads", lambda: None)
        monkeypatch.delenv("USERPROFILE", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(
            lambda cls: tmp_path / "nope"))

        result = P.default_downloads_dir()
        assert result.is_absolute()
        assert result == REPO_ROOT / "downloads"

    def test_windows_without_userprofile_falls_back(self, monkeypatch,
                                                    no_override, tmp_path):
        monkeypatch.setattr(P.sys, "platform", "win32")
        monkeypatch.setattr(P, "_windows_downloads", lambda: None)
        monkeypatch.delenv("USERPROFILE", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(
            lambda cls: tmp_path / "nope"))

        assert P.default_downloads_dir() == REPO_ROOT / "downloads"


class TestProductDir:
    """Everything the toolkit produces is grouped under one folder inside the
    OS Downloads folder, so the user's Downloads is not littered with loose
    ``clips`` and ``downloads`` folders from two different features."""

    def test_sits_inside_the_native_downloads_folder(self, fake_home, monkeypatch,
                                                    no_override):
        monkeypatch.setattr(P.sys, "platform", "linux")
        monkeypatch.setattr(P, "_linux_xdg_downloads", lambda: None)

        result = P.default_product_dir()

        assert result.parent == fake_home / "Downloads"
        assert result.name == P.PRODUCT_FOLDER

    def test_folder_name_is_the_brand(self):
        assert P.PRODUCT_FOLDER == "Ridikc Video Toolkit"

    def test_override_moves_the_whole_product_folder(self, tmp_path, monkeypatch,
                                                     no_override):
        target = tmp_path / "unduhan-saya"
        monkeypatch.setenv("RCH_DOWNLOADS_DIR", str(target))

        assert P.default_product_dir() == target / P.PRODUCT_FOLDER

    def test_result_is_always_absolute(self, tmp_path, monkeypatch, no_override):
        monkeypatch.setattr(P.sys, "platform", "linux")
        assert P.default_product_dir().is_absolute()

    def test_clip_output_is_a_subfolder_of_it(self, fake_home, monkeypatch,
                                              no_override):
        monkeypatch.setattr(P.sys, "platform", "linux")
        monkeypatch.setattr(P, "_linux_xdg_downloads", lambda: None)

        assert P.default_clip_output_dir() == (
            fake_home / "Downloads" / P.PRODUCT_FOLDER / "clips"
        )

    def test_falls_back_to_repo_when_no_downloads_folder_exists(
        self, tmp_path, monkeypatch, no_override
    ):
        monkeypatch.setattr(P.sys, "platform", "linux")
        monkeypatch.setattr(P, "_linux_xdg_downloads", lambda: None)
        monkeypatch.setattr(Path, "home", classmethod(
            lambda cls: tmp_path / "no-such-home"))

        assert P.default_product_dir() == REPO_ROOT / "downloads" / P.PRODUCT_FOLDER


class TestEnsureDir:
    def test_creates_the_directory(self, tmp_path):
        target = tmp_path / "a" / "b"
        assert P.ensure_dir(target) == target
        assert target.is_dir()

    def test_existing_directory_is_returned_as_is(self, tmp_path):
        assert P.ensure_dir(tmp_path) == tmp_path

    def test_unusable_target_falls_back_into_the_repo(self, tmp_path, monkeypatch):
        blocker = tmp_path / "file-not-a-dir"
        blocker.write_text("x", encoding="utf-8")
        # A path whose parent is a regular file cannot be created.
        result = P.ensure_dir(blocker / "child")
        assert result == REPO_ROOT / "downloads"
        assert result.is_dir()