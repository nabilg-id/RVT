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

    def _make(host, port, app, **kwargs):
        srv = FakeServer()
        srv.host, srv.port = host, port
        # Recorded so a test can assert how the real server was asked to behave,
        # rather than inferring it from a class hierarchy.
        srv.kwargs = kwargs
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


class TestServerHandlesRequestsConcurrently:
    """A slow request must not lock the whole app.

    The routes that talk to YouTube block for as long as their timeout:
    ``/api/preview`` waits on a yt-dlp subprocess for up to 90 seconds, and
    ``/api/download`` runs a whole download. On a single-threaded server one of
    those stalls every other request, so the other page will not load, a running
    harvest's progress bar freezes, and - worst - the Cancel button does nothing,
    because it cannot even be reached. The user is left watching a dead UI with
    no way to stop the job.

    Measured rather than asserted structurally: the point is whether a second
    request is served while the first is still running.
    """

    def test_the_server_is_asked_to_be_threaded(self, fake_server, no_browser):
        """Cheap guard next to the behavioural test below, so the option cannot
        be dropped without a timing-based failure being the only signal."""
        threading.Thread(target=A.run_server, kwargs={"open_browser": False},
                         daemon=True).start()

        srv = fake_server[0] if _wait_for(lambda: bool(fake_server)) else None
        assert srv is not None
        assert srv.kwargs.get("threaded") is True

        srv.release()

    def test_a_slow_route_does_not_block_the_others(self, no_browser, monkeypatch):
        import http.client
        import json
        import time

        # /api/preview imports get_video_info inside the function body, so the
        # patch has to land on the module it imports from - patching clipper.app
        # would do nothing at all.
        import rch.youtube.metadata as meta

        def _slow(*args, **kwargs):
            time.sleep(4)
            return {"duration": 212.0, "uploadDate": "20260101"}

        monkeypatch.setattr(meta, "get_video_info", _slow)
        import rch.youtube.thumbnail as thumb_mod

        monkeypatch.setattr(thumb_mod, "thumbnail", lambda url: {
            "status": True, "result": {
                "title": "T", "thumbnails": {"maxresdefault": "http://x/y.jpg"}}})

        srv = A.create_server("127.0.0.1", 0)
        port = srv.server_port
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()

        def _slow_call():
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
            try:
                payload = json.dumps({"url": "https://youtu.be/dQw4w9WgXcQ"})
                conn.request("POST", "/api/preview", body=payload, headers={
                    "Content-Type": "application/json"})
                conn.getresponse().read()
            finally:
                conn.close()

        try:
            blocker = threading.Thread(target=_slow_call, daemon=True)
            blocker.start()
            # Give the blocking request time to occupy the only worker slot.
            time.sleep(1.0)

            started = time.monotonic()
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("GET", "/api/videos")
            response = conn.getresponse()
            response.read()
            elapsed = time.monotonic() - started
            conn.close()

            assert response.status == 200
            assert elapsed < 2.5, (
                f"a second request waited {elapsed:.1f}s behind a slow one; the "
                f"server is serving one request at a time"
            )
        finally:
            srv.shutdown()
            thread.join(timeout=5)
            srv.server_close()


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