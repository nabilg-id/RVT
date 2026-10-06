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


class TestSetupInstallsTheProjectNotJustItsDependencies:
    """The installer has to install the project itself.

    ``requirements.txt`` installs dependencies only. The ``rch`` and ``vclip``
    commands come from ``[project.scripts]`` in pyproject.toml, so without
    ``pip install -e .`` a machine can have every dependency present and still
    not have ``rch``. The installer's own Desktop shortcut then looks for an
    ``rch.exe`` that was never created, and the README's entire usage section is
    written against a command that does not exist. Nothing about the happy path
    of the suite would catch it, because the tests import the packages rather
    than invoking the console scripts.
    """

    @pytest.mark.parametrize("script", ["setup-gui.bat", "setup-gui.sh"])
    def test_it_installs_the_project(self, script):
        body = (REPO / script).read_text(encoding="utf-8")

        assert "pip install -e ." in body, (
            f"{script} never installs the project, so rch/vclip are never "
            f"created"
        )

    def test_the_project_install_happens_before_the_launchers_are_written(self):
        """Order matters: the CLI shortcut calls rch.exe, so writing it first
        would produce a shortcut that fails on first click.

        Measured against the step markers rather than the file names, because
        ``RCH-CLI.bat`` is named in an explanatory comment well before it is
        actually written - matching the name alone would compare against the
        comment and prove nothing.
        """
        body = (REPO / "setup-gui.bat").read_text(encoding="utf-8")

        install = body.find("pip install -e .")
        launcher_step = body.find("[5/5]")

        assert install != -1, "the project is never installed"
        assert launcher_step != -1, "the launcher step marker is missing"
        assert install < launcher_step, (
            "the Desktop shortcut is written before rch.exe exists"
        )

    @pytest.mark.parametrize("script", ["setup-gui.bat", "setup-gui.sh"])
    def test_a_failed_project_install_is_reported_not_swallowed(self, script):
        body = (REPO / script).read_text(encoding="utf-8")

        tail = body.split("pip install -e .", 1)[1][:400]
        assert "PERINGATAN" in tail or "warning" in tail.lower(), (
            f"{script} can lose rch/vclip silently"
        )


class TestTheRepositoryPathIsDiscoverable:
    def test_the_launcher_has_no_hardcoded_machine_path(self):
        """A path baked into the repo would work on this machine and nowhere
        else. RCH_REPO is passed in by the installer."""
        body = _read("vclip.bat")

        assert not re.search(r"[A-Za-z]:\\", body)

    def test_the_launcher_prefers_the_passed_path(self):
        body = _read("vclip.bat")

        assert "%RCH_REPO%" in body