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


class TestReadmeServesANonDeveloperFirst:
    """The README is what a new user reads first, and it has to work for them.

    It was written the other way round: a Quick Start whose second line used a
    command the first line had not installed, ``rch``, described as if it were
    the normal path while the installer was the real one. Someone who does not
    know what pip is could follow it exactly and still get nothing that runs.

    These pin the shape rather than the wording: a plain-language path first,
    technical detail behind a marker, and no command promised that the install
    does not produce.
    """

    def _readme(self) -> str:
        return (REPO / "README.md").read_text(encoding="utf-8")

    #: The headings, not the phrases. "Detail teknis" also appears in the
    #: Features <summary>, which sits above the plain-language start and made
    #: an earlier version of this slice compare the wrong two offsets.
    START = "Mulai dari sini (pengguna baru)"
    TECH = "Detail teknis (untuk developer)"

    def _plain_section(self) -> str:
        body = self._readme()
        return body[body.find(self.START): body.find(self.TECH)]

    def test_it_opens_with_a_path_for_someone_who_is_not_a_developer(self):
        body = self._readme()

        assert self.START in body, "there is no plain-language starting point"
        assert self.TECH in body, "the developer section marker is missing"
        assert body.find(self.START) < body.find(self.TECH), (
            "technical detail appears before the plain-language start"
        )

    def test_the_plain_start_names_the_installer_and_the_shortcut(self):
        """Two concrete things a non-developer can act on."""
        section = self._plain_section()

        assert "setup-gui.bat" in section, "the installer is never named"
        assert "VCLIP-GUI" in section, "the Desktop shortcut is never named"

    def test_no_quick_start_uses_a_command_the_install_does_not_create(self):
        """`rch` is only real after `pip install -e .`.

        Before that fix the Quick Start installed requirements.txt alone and
        then told the reader to run `rch web`, which could not exist. The
        plain-language path has to start the app by something that works from
        a plain double-click.
        """
        section = self._plain_section()

        assert "VCLIP-GUI" in section
        assert "pip install" not in section, (
            "the plain-language start asks a non-developer to run pip"
        )

    def test_the_install_section_states_both_pip_commands(self):
        body = self._readme()

        assert "pip install -r requirements.txt" in body
        assert "pip install -e ." in body

    def test_it_does_not_claim_a_coverage_number_it_has_not_measured(self):
        """It said '1369 test, coverage 99%' long after the suite grew past
        2000 and settled near 97. A stale number reads as a promise."""
        body = self._readme()

        assert "1369" not in body, "the test count in the README is stale"
        assert "99%" not in body, "the coverage claim in the README is stale"

    def test_the_env_is_documented_as_optional(self):
        """It was listed as mandatory, which would have had a new user hunting
        for an API key before the download features worked without one."""
        body = self._readme()

        assert "**Wajib**" not in body, "the README still demands a key"


class TestTheMacosInstallerIsNotASecondCopy:
    """setup-gui.command used to be a full duplicate of setup-gui.sh.

    It drifted - it kept the old four-step flow and never learned about
    `pip install -e .` - so a mac user who double-clicked it got every
    dependency installed and no ``rch`` at all. That is the same failure the
    Windows installer was just fixed for, sitting in the file mac users are
    most likely to click.

    It now delegates, so there is one installer to keep correct.
    """

    def test_it_delegates_instead_of_duplicating(self):
        body = (REPO / "setup-gui.command").read_text(encoding="utf-8")

        assert "setup-gui.sh" in body, (
            "setup-gui.command must hand over to setup-gui.sh"
        )
        assert "pip install -r requirements.txt" not in body, (
            "setup-gui.command still installs dependencies itself, so it can "
            "drift again"
        )

    @pytest.mark.parametrize("script", ["setup-gui.sh", "setup-gui.command"])
    def test_nothing_claims_to_be_the_only_installer(self, script):
        body = (REPO / script).read_text(encoding="utf-8")
        assert "installer macOS / Linux" in body

    def test_a_missing_shell_script_says_so(self):
        """The wrapper's only failure mode is the file not being there, and a
        silently exiting terminal gives the user nothing."""
        body = (REPO / "setup-gui.command").read_text(encoding="utf-8")

        assert "tidak ditemukan" in body
        assert "exit 1" in body


class TestTheEnvExampleDoesNotOverrideTheOutputDefault:
    """.env.example shipped OUTPUT_DIR=./clips.

    Copying the template to .env therefore placed clips in the project folder
    instead of the OS Downloads folder, quietly undoing the default the README
    documents - and a user who followed the configuration step would not notice,
    because the files still appeared, just in the wrong place.
    """

    def _example(self) -> str:
        return (REPO / "clipper" / ".env.example").read_text(encoding="utf-8")

    def test_output_dir_is_not_set_to_a_repo_relative_path(self):
        active = [
            line.strip()
            for line in self._example().splitlines()
            if line.strip().startswith("OUTPUT_DIR=")
        ]

        assert active == [], (
            f".env.example assigns OUTPUT_DIR ({active}), which overrides the "
            f"Downloads default for anyone who copies it"
        )

    def test_the_line_is_still_there_to_be_uncommented(self):
        assert "# OUTPUT_DIR=" in self._example(), (
            "the option should stay discoverable, just not active"
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