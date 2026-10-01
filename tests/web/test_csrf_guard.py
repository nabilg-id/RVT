"""Tests for the cross-origin write guard in the web GUI.

The GUI ships no authentication, so an unrelated page in the user's browser
must not be able to POST to ``/api/quit`` or kick off downloads. A browser
always sends ``Origin`` on a cross-origin POST, so requiring it to match
``Host`` closes that hole without locking out curl, the CLI, or LAN access.
"""
import pytest

from rch.web.server import _origin_host, app

HOST = "127.0.0.1:8787"


@pytest.fixture()
def client():
    return app.test_client()


class TestOriginHost:
    def test_extracts_netloc(self):
        assert _origin_host("http://127.0.0.1:8787") == "127.0.0.1:8787"

    def test_lowercases(self):
        assert _origin_host("http://Example.COM:80") == "example.com:80"

    def test_scheme_relative_origin(self):
        assert _origin_host("//localhost:5000") == "localhost:5000"

    def test_unparseable_returns_none(self):
        assert _origin_host("not-a-url") is None

    def test_empty_returns_none(self):
        assert _origin_host("") is None


class TestOriginAllowed:
    def test_absent_origin_allowed(self, client):
        r = client.post("/api/quit", json={}, headers={"Host": HOST})
        assert r.status_code == 200

    def test_same_origin_allowed(self, client):
        r = client.post("/api/quit", json={}, headers={
            "Host": HOST, "Origin": f"http://{HOST}",
        })
        assert r.status_code == 200

    def test_no_origin_allowed(self, client):
        r = client.post("/api/quit", json={}, headers={"Host": HOST})
        assert r.status_code == 200

    def test_foreign_origin_rejected(self, client):
        r = client.post("/api/quit", json={}, headers={
            "Host": HOST, "Origin": "http://evil.example",
        })
        assert r.status_code == 403

    def test_null_origin_rejected(self, client):
        r = client.post("/api/quit", json={}, headers={
            "Host": HOST, "Origin": "null",
        })
        assert r.status_code == 403

    def test_malformed_origin_rejected(self, client):
        r = client.post("/api/quit", json={}, headers={
            "Host": HOST, "Origin": "garbage",
        })
        assert r.status_code == 403

    def test_other_port_rejected(self, client):
        r = client.post("/api/quit", json={}, headers={
            "Host": HOST, "Origin": "http://127.0.0.1:9999",
        })
        assert r.status_code == 403

    def test_rejection_body_is_json(self, client):
        r = client.post("/api/quit", json={}, headers={
            "Host": HOST, "Origin": "http://evil.example",
        })
        assert r.get_json()["error"]


class TestGuardScope:
    def test_get_is_not_guarded(self, client):
        r = client.get("/api/history", headers={"Origin": "http://evil.example"})
        assert r.status_code == 200

    def test_index_is_not_guarded(self, client):
        r = client.get("/", headers={"Origin": "http://evil.example"})
        assert r.status_code == 200

    def test_download_is_guarded(self, client):
        r = client.post("/api/download", json={}, headers={
            "Host": HOST, "Origin": "http://evil.example",
        })
        assert r.status_code == 403

    def test_channel_full_is_guarded(self, client):
        r = client.post("/api/channel-full", json={}, headers={
            "Host": HOST, "Origin": "http://evil.example",
        })
        assert r.status_code == 403

    def test_status_poll_is_not_guarded(self, client):
        r = client.get("/api/status/1", headers={"Origin": "http://evil.example"})
        assert r.status_code in (200, 404)


class TestLanAccessStillWorks:
    def test_lan_host_is_accepted(self, client):
        r = client.post("/api/quit", json={}, headers={
            "Host": "192.168.1.20:8787", "Origin": "http://192.168.1.20:8787",
        })
        assert r.status_code == 200

    def test_missing_host_header_is_rejected(self, client):
        r = client.post("/api/quit", json={}, headers={
            "Origin": "http://evil.example",
        })
        assert r.status_code == 403
