"""Tests for the server lifecycle in :mod:`clipper.app`.

``run_server`` blocks until shutdown, so ``werkzeug.serving.make_server`` is
replaced with a stand-in whose ``serve_forever`` waits on an event. That models
the real socket lifecycle closely enough to pin that ``/api/quit`` shuts down
the live instance rather than rebuilding a second one, and keeps the tests
fast.
"""
from __future__ import annotations

import threading
import time

import pytest

from clipper import app as A


def _wait_for(predicate, tries=100):
    for _ in range(tries):
        if predicate():
            return True
        time.sleep(0.02)
    return False


class FakeServer:
    """Berperilaku seperti ``werkzeug``'s server: ``serve_forever`` memblokir."""

    def __init__(self):
        self.closed = 0
        self.shutdown_calls = 0
        self._stop = threading.Event()
        self.started = threading.Event()

    def serve_forever(self):
        self.started.set()
        self._stop.wait(timeout=5)

    def server_close(self):
        self.closed += 1

    def shutdown(self):
        self.shutdown_calls += 1
        self._stop.set()

    def release(self):
        """Lepas blokir tanpa lewat jalur shutdown (untuk KeyboardInterrupt)."""
        self._stop.set()


@pytest.fixture()
def fake_server(monkeypatch):
    created = []

    def _make(host, port, app):
        srv = FakeServer()
        srv.host, srv.port = host, port
        created.append(srv)
        return srv

    import werkzeug.serving

    monkeypatch.setattr(werkzeug.serving, "make_server", _make)
    return created


@pytest.fixture()
def no_browser(monkeypatch):
    monkeypatch.setattr(A.webbrowser, "open", lambda url: None)


class TestRunServer:
    def test_blocks_until_shutdown_then_closes(self, fake_server, no_browser):
        thread = threading.Thread(
            target=A.run_server, kwargs={"open_browser": False}, daemon=True)
        thread.start()

        srv = fake_server[0] if _wait_for(lambda: bool(fake_server)) else None
        assert srv is not None
        assert _wait_for(srv.started.is_set)

        srv.release()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert srv.closed == 1

    def test_clears_the_live_reference_on_exit(self, fake_server, no_browser):
        thread = threading.Thread(
            target=A.run_server, kwargs={"open_browser": False}, daemon=True)
        thread.start()
        srv = fake_server[0]
        assert _wait_for(srv.started.is_set)
        srv.release()
        thread.join(timeout=5)
        assert A._LIVE_SERVER is None

    def test_keyboard_interrupt_still_closes(self, fake_server, no_browser, monkeypatch):
        def _interrupt(self):
            self.started.set()
            raise KeyboardInterrupt

        monkeypatch.setattr(FakeServer, "serve_forever", _interrupt)
        A.run_server(open_browser=False)
        assert fake_server[0].closed == 1

    def test_opens_browser_when_asked(self, fake_server, monkeypatch):
        opened = []
        monkeypatch.setattr(A.webbrowser, "open", lambda url: opened.append(url))

        thread = threading.Thread(
            target=A.run_server, kwargs={"open_browser": True}, daemon=True)
        thread.start()
        srv = fake_server[0]
        assert _wait_for(srv.started.is_set)
        assert _wait_for(lambda: bool(opened))
        assert opened[0].startswith("http://")

        srv.release()
        thread.join(timeout=5)

    def test_does_not_open_browser_when_not_asked(self, fake_server, monkeypatch):
        opened = []
        monkeypatch.setattr(A.webbrowser, "open", lambda url: opened.append(url))

        thread = threading.Thread(
            target=A.run_server, kwargs={"open_browser": False}, daemon=True)
        thread.start()
        srv = fake_server[0]
        assert _wait_for(srv.started.is_set)
        time.sleep(0.1)
        assert opened == []

        srv.release()
        thread.join(timeout=5)


class TestQuitShutsDownTheLiveServer:
    def test_quit_stops_the_running_server(self, fake_server, no_browser):
        thread = threading.Thread(
            target=A.run_server, kwargs={"open_browser": False}, daemon=True)
        thread.start()
        srv = fake_server[0]
        assert _wait_for(srv.started.is_set)

        with A.app.test_client() as client:
            assert client.post("/api/quit", json={}).status_code == 200

        thread.join(timeout=5)
        assert srv.shutdown_calls >= 1
        assert not thread.is_alive(), "server should stop after /api/quit"

    def test_quit_does_not_build_a_second_server(self, fake_server, no_browser):
        thread = threading.Thread(
            target=A.run_server, kwargs={"open_browser": False}, daemon=True)
        thread.start()
        assert _wait_for(lambda: bool(fake_server))
        srv = fake_server[0]
        assert _wait_for(srv.started.is_set)

        with A.app.test_client() as client:
            client.post("/api/quit", json={})
        thread.join(timeout=5)

        assert len(fake_server) == 1, "/api/quit must not re-open a port"


class TestShutdownHelper:
    def test_without_live_server_is_harmless(self):
        A._LIVE_SERVER = None
        A._shutdown()

    def test_swallows_server_errors(self):
        class Exploding:
            def shutdown(self):
                raise RuntimeError("already dead")

        A._LIVE_SERVER = Exploding()
        A._shutdown()