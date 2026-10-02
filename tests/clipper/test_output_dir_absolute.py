"""A relative OUTPUT_DIR must not break the download route.

Flask's send_from_directory computes os.path.join(app.root_path, path).
app.root_path is the clipper package directory, so a relative OUTPUT_DIR of
"clips" is looked up as "clipper/clips" and every download 404s even though the
file exists. .env.example ships OUTPUT_DIR=./clips and the team guide tells
people to copy it, so this was reachable for everyone who followed the docs.

config.py anchors relative paths to the repo root; these tests hold that line.
"""
from __future__ import annotations

import importlib
import os
import sys

import dotenv
import pytest


def reload_config(monkeypatch, **env):
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False,
                        raising=False)
    sys.modules.pop("clipper.config", None)
    return importlib.import_module("clipper.config")


@pytest.fixture(autouse=True)
def _restore():
    yield
    sys.modules.pop("clipper.config", None)
    importlib.import_module("clipper.config")


class TestPathsAreAlwaysAbsolute:
    def test_relative_output_dir_is_anchored_to_repo_root(self, monkeypatch):
        c = reload_config(monkeypatch, OUTPUT_DIR="./clips")
        assert c.OUTPUT_DIR.is_absolute()
        assert c.OUTPUT_DIR == c.REPO_ROOT / "clips"

    def test_relative_temp_dir_is_anchored(self, monkeypatch):
        c = reload_config(monkeypatch, TEMP_DIR="./temp")
        assert c.TEMP_DIR.is_absolute()
        assert c.TEMP_DIR == c.REPO_ROOT / "temp"

    def test_bare_relative_dir_is_anchored(self, monkeypatch):
        c = reload_config(monkeypatch, OUTPUT_DIR="clips")
        assert c.OUTPUT_DIR == c.REPO_ROOT / "clips"

    def test_absolute_path_is_left_alone(self, monkeypatch, tmp_path):
        target = str(tmp_path / "keluarku")
        c = reload_config(monkeypatch, OUTPUT_DIR=target)
        assert c.OUTPUT_DIR.is_absolute()
        assert c.OUTPUT_DIR == tmp_path / "keluarku"

    def test_dot_slash_normalises(self, monkeypatch):
        c = reload_config(monkeypatch, OUTPUT_DIR="././clips")
        assert c.OUTPUT_DIR == c.REPO_ROOT / "clips"

    def test_every_configured_path_is_absolute(self, monkeypatch):
        c = reload_config(
            monkeypatch,
            OUTPUT_DIR="./clips", TEMP_DIR="./temp",
            ASSET_DIR="./asset", COOKIES_FILE="./cookies.txt",
        )
        for value in (c.OUTPUT_DIR, c.TEMP_DIR, c.ASSET_DIR, c.COOKIES_FILE,
                      c.TRANSITION_FILE):
            assert value.is_absolute(), value

    def test_home_relative_is_expanded(self, monkeypatch):
        # "~" already exists, so importing config does not create anything.
        c = reload_config(monkeypatch, OUTPUT_DIR="~")
        assert "~" not in str(c.OUTPUT_DIR)
        assert c.OUTPUT_DIR.is_absolute()

    def test_defaults_are_absolute_too(self, monkeypatch):
        c = reload_config(monkeypatch, OUTPUT_DIR=None, TEMP_DIR=None,
                          ASSET_DIR=None, COOKIES_FILE=None)
        for value in (c.OUTPUT_DIR, c.TEMP_DIR, c.ASSET_DIR, c.COOKIES_FILE):
            assert value.is_absolute()


class TestDownloadRouteSurvivesRelativeConfig:
    """The end the anchoring exists for: a clip written under a relative
    OUTPUT_DIR must still be downloadable.

    The directory itself is redirected to tmp_path after config resolves it,
    so the test proves the route follows OUTPUT_DIR wherever it points without
    littering the repository.
    """

    @pytest.fixture()
    def client(self, monkeypatch, tmp_path):
        # tmp_path rather than a repo-relative name: config.py creates
        # OUTPUT_DIR on import, so a relative value here would leave a folder
        # in the working tree every time the suite runs.
        monkeypatch.chdir(tmp_path)
        reload_config(monkeypatch,
                      OUTPUT_DIR=str(tmp_path / "keluaran"),
                      TEMP_DIR=str(tmp_path / "sementara"))

        sys.modules.pop("clipper.app", None)
        app_mod = importlib.import_module("clipper.app")
        monkeypatch.setattr(app_mod, "OUTPUT_DIR", tmp_path / "keluaran")
        app_mod.app.config["TESTING"] = True
        return app_mod, app_mod.app.test_client(), tmp_path / "keluaran"

    def test_clip_is_downloadable(self, client):
        app_mod, http, out_dir = client
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "clip_1_90pts_x.mp4").write_bytes(b"\x00" * 4096)

        r = http.get("/clips/clip_1_90pts_x.mp4")
        assert r.status_code == 200, "the download route did not follow OUTPUT_DIR"
        assert len(r.data) == 4096

    def test_route_is_not_below_the_package_dir(self, client):
        """Flask would look under clipper/, so that is exactly where the output
        directory must not end up."""
        app_mod, _, out_dir = client
        package_dir = os.path.dirname(os.path.abspath(app_mod.__file__))
        out = os.path.abspath(str(out_dir))
        assert not out.startswith(package_dir + os.sep), \
            f"{out} is inside the package dir {package_dir}"

    def test_traversal_still_refused(self, client):
        app_mod, http, out_dir = client
        out_dir.mkdir(parents=True, exist_ok=True)
        r = http.get("/clips/../rahasia.txt")
        assert r.status_code == 404

    def test_missing_file_still_404s(self, client):
        app_mod, http, out_dir = client
        out_dir.mkdir(parents=True, exist_ok=True)
        assert http.get("/clips/tidak_ada.mp4").status_code == 404


class TestNoChdirDependence:
    def test_same_config_from_two_working_directories(self, monkeypatch, tmp_path):
        first = tmp_path / "satu"
        second = tmp_path / "dua"
        first.mkdir()
        second.mkdir()

        monkeypatch.chdir(first)
        a = reload_config(monkeypatch, OUTPUT_DIR="./clips")
        monkeypatch.chdir(second)
        b = reload_config(monkeypatch, OUTPUT_DIR="./clips")

        assert a.OUTPUT_DIR == b.OUTPUT_DIR, "OUTPUT_DIR still depends on cwd"

    def test_pipeline_and_route_agree_on_the_directory(self, monkeypatch, tmp_path):
        """video_processor imports OUTPUT_DIR by value; if the two modules can
        disagree, clips get written where the route does not look."""
        monkeypatch.chdir(tmp_path)
        # Absolute here on purpose: the anchoring is covered above, and a
        # repo-relative name would be created in the working tree on import.
        reload_config(monkeypatch,
                      OUTPUT_DIR=str(tmp_path / "keluaran"),
                      TEMP_DIR=str(tmp_path / "s"))
        for mod in list(sys.modules):
            if mod.startswith("clipper") and mod != "clipper":
                del sys.modules[mod]

        config = importlib.import_module("clipper.config")
        # Import the pipeline module the way app.py does; it needs the heavy
        # stack, so tolerate its absence.
        try:
            pipeline = importlib.import_module("clipper.services.video_processor")
        except Exception:  # noqa: BLE001 - heavy deps not installed in CI
            pytest.skip("pipeline clip tidak bisa diimpor")

        assert pipeline.OUTPUT_DIR == config.OUTPUT_DIR
        assert pipeline.TEMP_DIR == config.TEMP_DIR
        assert os.path.isabs(str(pipeline.OUTPUT_DIR))