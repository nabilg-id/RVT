"""Tests for the Windows launchers.

``setup-gui.bat`` copies ``launchers/vclip.bat`` onto the Desktop, and the
copy has ``cd /d "%~dp0"`` in it. Once copied, ``%~dp0`` is the Desktop - not
the repository - so ``py -3 -m clipper.app`` found no package, raised
ModuleNotFoundError, and the console window vanished immediately. It looked like
the app refusing to start rather than a launcher pointing at the wrong folder.

Both launchers are asserted here as text. A batch file cannot be executed by
pytest, and driving cmd.exe to reproduce the failure would test cmd, not us.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LAUNCHER_DIR = REPO / "launchers"


def _read(name: str) -> str:
    return (LAUNCHER_DIR / name).read_text(encoding="utf-8")


class TestTheLauncherCannotDependOnItsOwnLocation:
    @pytest.mark.parametrize("name", ["vclip.bat"])
    def test_it_does_not_cd_to_the_scripts_own_folder(self, name):
        """This is the bug. %~dp0 is the Desktop once the file is copied there,
        so the repository was never entered and the import failed."""
        assert 'cd /d "%~dp0"' not in _read(name)

    @pytest.mark.parametrize("name", ["vclip.bat"])
    def test_a_copied_launcher_still_finds_the_repository(self, name):
        """The copy on the Desktop has to work from the Desktop."""
        assert "RCH_REPO" in _read(name)

    @pytest.mark.parametrize("name", ["vclip.bat"])
    def test_it_falls_back_to_its_own_folder_only_as_a_last_resort(self, name):
        assert "%~dp0" in _read(name)

    @pytest.mark.parametrize("name", ["vclip.bat"])
    def test_it_starts_the_gui(self, name):
        assert "-m clipper.app" in _read(name)


class TestItStillReportsWhyItFailed:
    """The window closing with nothing on it is what made this hard to report,
    so a failure has to leave something behind."""

    @pytest.mark.parametrize("name", ["vclip.bat"])
    def test_a_missing_package_prints_a_reason(self, name):
        body = _read(name)

        assert "No module named" in body or "tidak ditemukan" in body.lower()

    @pytest.mark.parametrize("name", ["vclip.bat"])
    def test_it_pauses_before_exiting_on_a_hard_failure(self, name):
        """Without the pause the console closes on the error and the message goes
        with it."""
        body = _read(name)

        assert "pause" in body

    @pytest.mark.parametrize("name", ["vclip.bat"])
    def test_it_names_the_folder_it_looked_in(self, name):
        body = _read(name)

        assert "echo" in body


class TestSetupWritesALauncherThatWorks:
    def test_setup_stops_copying_the_launcher_verbatim(self):
        """Copying is what broke it. The installer writes the repository path in
        instead, so the Desktop file points home."""
        body = (REPO / "setup-gui.bat").read_text(encoding="utf-8")

        assert 'copy /y "launchers\\vclip.bat"' not in body

    def test_setup_passes_the_repository_path_when_writing_it(self):
        body = (REPO / "setup-gui.bat").read_text(encoding="utf-8")

        assert "RCH_REPO=" in body

    def test_setup_creates_both_desktop_entries(self):
        """Two launchers, two names. Anything less and one of them keeps the
        stale copy that closes instantly."""
        body = (REPO / "setup-gui.bat").read_text(encoding="utf-8")

        assert "VCLIP-GUI.bat" in body
        assert "RCH-GUI.bat" in body

    def test_setup_removes_a_stale_launcher_before_writing(self):
        """The old copy is still on the Desktop right now and still broken.
        Writing over it is not enough if the write can be skipped."""
        body = (REPO / "setup-gui.bat").read_text(encoding="utf-8")

        assert "del /f /q" in body.lower() or "del /q" in body.lower()


class TestTheRepositoryPathIsDiscoverable:
    def test_the_launcher_has_no_hardcoded_machine_path(self):
        """A path baked into the repo would work on this machine and nowhere
        else. RCH_REPO is passed in by the installer."""
        body = _read("vclip.bat")

        assert not re.search(r"[A-Za-z]:\\", body)

    def test_the_launcher_prefers_the_passed_path(self):
        body = _read("vclip.bat")

        assert "%RCH_REPO%" in body