"""Tests for the ``POST /api/playlist`` endpoint.

The web GUI could not reach the playlist scraper at all: the endpoint did not
exist, so the one capability that needs no yt-dlp and no API key was CLI-only.
This closes that gap.

Security: the endpoint takes an arbitrary URL from the request body. It is a
local single-user tool bound to loopback, and the CSRF guard in
``tests/web/test_csrf_guard.py`` already covers cross-origin writes, so these
tests focus on input validation and response shape.
"""
from __future__ import annotations

import copy

import pytest

from rch.web import server as srv

PLAYLIST_URL = "https://www.youtube.com/playlist?list=PL123"

_ENVELOPE = {
    "status": True,
    "result": {
        "id": "PL123",
        "url": PLAYLIST_URL,
        "type": "playlist",
        "title": "My Mix",
        "author": "DJ",
        "thumbnail": "https://img/banner.jpg",
        "itemCount": 2,
        "items": [
            {"videoId": "vid1", "title": "Video Satu", "lengthText": "3:21",
             "thumbnail": "https://img/vid1.jpg"},
            {"videoId": "vid2", "title": "Video Dua", "lengthText": "10:05",
             "thumbnail": "https://img/vid2.jpg"},
        ],
    },
}


@pytest.fixture
def client():
    srv.app.config.update(TESTING=True)
    with srv.app.test_client() as c:
        yield c


@pytest.fixture
def scraped(monkeypatch):
    """Patch the scraper; every test gets an isolated deep copy of the payload."""
    seen = {}
    envelope = {"value": copy.deepcopy(_ENVELOPE)}

    def fake_scrape(url):
        seen["url"] = url
        return envelope["value"]

    monkeypatch.setattr("rch.youtube.playlist.scrape", fake_scrape)
    return seen, envelope


class TestApiPlaylist:
    def test_returns_the_scraper_payload(self, client, scraped):
        res = client.post("/api/playlist", json={"url": PLAYLIST_URL})

        assert res.status_code == 200
        body = res.get_json()
        assert body["status"] is True
        assert body["result"]["title"] == "My Mix"

    def test_link_key_is_accepted_as_an_alias(self, client, scraped):
        seen, _envelope = scraped

        client.post("/api/playlist", json={"link": PLAYLIST_URL})

        assert seen["url"] == PLAYLIST_URL

    def test_missing_url_is_a_400(self, client):
        res = client.post("/api/playlist", json={})

        assert res.status_code == 400
        assert res.get_json()["status"] is False

    def test_blank_url_is_a_400(self, client):
        assert client.post("/api/playlist", json={"url": "   "}).status_code == 400

    def test_malformed_json_body_is_a_400(self, client):
        res = client.post("/api/playlist", data="not json",
                          content_type="application/json")

        assert res.status_code == 400

    def test_scraper_failure_is_a_400(self, client, scraped):
        """A failed scrape is a bad request, not a server fault."""
        _seen, envelope = scraped
        envelope["value"] = {"status": False, "message": "ytInitialData not found"}

        res = client.post("/api/playlist", json={"url": PLAYLIST_URL})

        assert res.status_code == 400
        assert res.get_json()["message"] == "ytInitialData not found"

    def test_scraper_exception_becomes_a_500_without_a_traceback(self, client, monkeypatch):
        def boom(_url):
            raise RuntimeError("network down")

        monkeypatch.setattr("rch.youtube.playlist.scrape", boom)

        res = client.post("/api/playlist", json={"url": PLAYLIST_URL})
        body = res.get_data(as_text=True)

        assert res.status_code == 500
        assert res.get_json() == {"status": False, "message": "network down"}
        assert "Traceback" not in body

    def test_limit_is_forwarded(self, client, monkeypatch):
        seen = {}

        def fake_metadata(url, options=None):
            seen["options"] = options
            return _ENVELOPE

        monkeypatch.setattr("rch.youtube.playlist.playlist_metadata", fake_metadata)

        client.post("/api/playlist", json={"url": PLAYLIST_URL, "limit": 5})

        assert seen["options"] == {"limit": 5}

    def test_absent_limit_is_forwarded_as_none(self, client, monkeypatch):
        seen = {}

        def fake_metadata(url, options=None):
            seen["options"] = options
            return _ENVELOPE

        monkeypatch.setattr("rch.youtube.playlist.playlist_metadata", fake_metadata)

        client.post("/api/playlist", json={"url": PLAYLIST_URL})

        assert seen["options"] == {"limit": None}

    def test_non_integer_limit_is_ignored_not_fatal(self, client, monkeypatch):
        """A JSON body can carry anything; a bad limit must not 500 the endpoint."""
        seen = {}

        def fake_metadata(url, options=None):
            seen["options"] = options
            return _ENVELOPE

        monkeypatch.setattr("rch.youtube.playlist.playlist_metadata", fake_metadata)

        res = client.post("/api/playlist", json={"url": PLAYLIST_URL, "limit": "abc"})

        assert res.status_code == 200
        assert seen["options"] == {"limit": None}

    def test_response_is_json(self, client, scraped):
        res = client.post("/api/playlist", json={"url": PLAYLIST_URL})

        assert res.headers["Content-Type"].startswith("application/json")

    def test_the_gui_exposes_a_playlist_button(self, client):
        """A backend endpoint nobody can click is not a feature."""
        body = client.get("/").get_data(as_text=True)

        assert 'data-act="playlist"' in body