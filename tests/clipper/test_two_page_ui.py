"""Tests for the two-page UI: the harvester kept as a second page.

The harvester's UI is not being rewritten or merged into the clipper's markup -
it stays as it was, because it is a working interface for a different job. What
changed is where it is served from and what it calls.

That last part is the trap. The old page read ``/api/history`` for its run
table. In the merged app that path means *clip* history, so an unmodified page
would have quietly filled the "Riwayat" table with clip rows and the harvest runs
would have vanished. The page now reads ``/api/runs``.

Both pages are reachable from either one, so a user never has to know a URL.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from clipper import app as A

HOST = {"Host": "127.0.0.1:8787"}

STATIC_DIR = Path(A.__file__).resolve().parent / "static"
TEMPLATE_DIR = Path(A.__file__).resolve().parent / "templates"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    A.app.config["TESTING"] = True
    monkeypatch.setattr(A, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(A, "OUTPUT_DIR", tmp_path / "clips")
    with A.app.test_client() as c:
        yield c


def _js(name):
    return (STATIC_DIR / name).read_text(encoding="utf-8")


class TestTheSecondPageExists:
    def test_it_is_served(self, client):
        assert client.get("/download", headers=HOST).status_code == 200

    def test_it_is_not_a_redirect_to_the_clipper(self, client):
        """A page that renders the clipper's markup under a different URL would
        pass a status check while being the wrong page entirely."""
        body = client.get("/download", headers=HOST).get_data(as_text=True)

        assert "Ridikc Video Toolkit" in body
        assert "data-act=\"channel-full\"" in body

    def test_the_clipper_page_is_still_the_root(self, client):
        body = client.get("/", headers=HOST).get_data(as_text=True)

        assert "YouTube Viral Clipper" in body
        assert "data-act=" not in body


class TestAssetsAreNamespaced:
    def test_the_second_page_loads_its_own_script(self, client):
        body = client.get("/download", headers=HOST).get_data(as_text=True)

        assert "download.js" in body
        assert "download.css" in body

    def test_it_does_not_pull_in_the_clipper_script(self, client):
        """Both pages define functions of the same name and a shared script tag
        would put two definitions of get() on one page."""
        body = client.get("/download", headers=HOST).get_data(as_text=True)

        assert "filename='app.js'" not in body

    def test_the_scripts_exist_on_disk(self):
        for name in ("download.js", "download.css"):
            assert (STATIC_DIR / name).is_file()

    def test_the_second_page_script_is_served(self, client):
        assert client.get("/static/download.js", headers=HOST).status_code == 200


class TestTheRunTableAsksForTheRightPath:
    def test_it_reads_runs_not_history(self):
        """The bug this page would otherwise ship: /api/history in the merged
        app is clip history, so the run table would fill with clip rows."""
        assert "/api/runs" in _js("download.js")

    def test_it_does_not_call_the_clip_history_path(self):
        assert '"/api/history' not in _js("download.js")

    def test_it_still_polls_the_shared_status_route(self):
        """One /api/status serves both kinds of job; the page must not have been
        given a second path of its own."""
        assert "/api/status/" in _js("download.js")

    def test_it_uses_the_channel_routes_that_moved(self):
        for route in ("/api/channel-info", "/api/channel-video",
                      "/api/channel-full", "/api/info", "/api/download",
                      "/api/playlist"):
            assert route in _js("download.js")


class TestNavigation:
    def test_the_second_page_offers_the_clipper(self, client):
        body = client.get("/download", headers=HOST).get_data(as_text=True)

        assert 'href="/"' in body

    def test_the_clipper_offers_the_second_page(self, client):
        body = client.get("/", headers=HOST).get_data(as_text=True)

        assert 'href="/download"' in body

    def test_both_pages_carry_the_same_tab_strip(self, client):
        root = client.get("/", headers=HOST).get_data(as_text=True)
        download = client.get("/download", headers=HOST).get_data(as_text=True)

        assert root.count('class="tabs"') == download.count('class="tabs"') == 1

    def test_the_current_tab_is_marked_on_both_pages(self, client):
        root = client.get("/", headers=HOST).get_data(as_text=True)
        download = client.get("/download", headers=HOST).get_data(as_text=True)

        assert 'aria-current="page"' in root
        assert 'aria-current="page"' in download

    def test_the_clipper_does_not_mark_the_other_tab_current(self, client):
        body = client.get("/", headers=HOST).get_data(as_text=True)

        assert body.count('aria-current="page"') == 1


class TestTheOutputDefaultFollowsTheCode:
    def _out_tag(self):
        """The ``<input id="out">`` tag itself.

        Scoped to that one tag on purpose. The old default also appears in the
        comment explaining why it was removed, and a whole-file search for the
        literal would fail on the explanation of the fix.
        """
        body = (TEMPLATE_DIR / "download.html").read_text(encoding="utf-8")
        for line in body.splitlines():
            if 'id="out"' in line:
                return line.strip()
        raise AssertionError("input #out tidak ditemukan")

    def test_the_page_no_longer_hardcodes_the_old_default(self):
        """It used to ship value="./downloads" in the markup, which silently
        disagreed with the CLI and the GUI once both resolved the native
        Downloads folder. The server now supplies it."""
        tag = self._out_tag()
        assert 'value="./downloads"' not in tag
        assert "value=\"{{ output_dir }}\"" in tag

    def test_the_folder_is_rendered_from_the_shared_resolver(self, client):
        body = client.get("/download", headers=HOST).get_data(as_text=True)

        assert "Downloads" in body or "downloads" in body
