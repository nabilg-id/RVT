"""Prove the guard works.

Two tests, each deliberately leaking in one of the two ways the suite has
leaked before. If either passes, the guard is not doing its job.

These trip the guard on purpose, so they opt out of the session-end check.
"""
import socket
import subprocess

import pytest

from conftest import allow_network_for_this_test


@pytest.fixture(autouse=True)
def _expected_to_trip_the_guard(request):
    allow_network_for_this_test(request)


class TestGuardCatchesSockets:
    def test_external_connection_is_refused(self):
        with pytest.raises(AssertionError, match="socket"):
            sock = socket.socket()
            try:
                sock.connect(("example.com", 80))
            finally:
                sock.close()

    def test_dns_lookup_is_refused(self):
        with pytest.raises(AssertionError, match="resolved"):
            socket.getaddrinfo("youtube.com", 443)

    def test_loopback_is_still_allowed(self):
        """A test binding or dialing localhost must keep working."""
        assert socket.getaddrinfo("127.0.0.1", 80) is not None


class TestGuardCatchesSubprocess:
    def test_ytdlp_spawn_is_refused(self):
        with pytest.raises(AssertionError, match="yt-dlp"):
            subprocess.run(["yt-dlp", "--version"], capture_output=True)

    def test_popen_ytdlp_is_refused(self):
        with pytest.raises(AssertionError, match="yt-dlp"):
            subprocess.Popen(["yt-dlp", "--version"])

    def test_other_subprocesses_still_run(self):
        """Only yt-dlp is blocked; the suite legitimately runs python -m."""
        import sys

        out = subprocess.run(
            [sys.executable, "-c", "print('halo')"],
            capture_output=True, text=True, timeout=60,
        )
        assert out.returncode == 0
        assert "halo" in out.stdout

    def test_popen_is_still_usable_as_a_base_class(self):
        """The guard must not cost libraries their ability to subclass Popen.

        yt_dlp defines ``class Popen(subprocess.Popen)`` at import time. A
        function installed in that slot raises ``TypeError: function()
        argument 'code' must be code, not str`` the moment such an import runs
        inside a test, which made ``clipper.services.youtube_downloader`` and
        ``video_processor`` unimportable and hid a third of the suite behind a
        skip. Guarding has to keep the type a type.
        """
        class Derived(subprocess.Popen):
            pass

        assert isinstance(Derived, type)

    def test_a_subclass_still_cannot_spawn_ytdlp(self):
        """Subclassing must not become a way around the guard."""

        class Sneaky(subprocess.Popen):
            def __init__(self, *a, **k):
                super().__init__(["yt-dlp", "--version"])

        with pytest.raises(AssertionError, match="yt-dlp"):
            Sneaky()

    def test_a_subclass_can_still_spawn_ordinary_commands(self):
        """The guard stays narrow: only yt-dlp is refused."""
        import sys

        class Ordinary(subprocess.Popen):
            pass

        proc = Ordinary(
            [sys.executable, "-c", "print('halo')"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        out, _ = proc.communicate(timeout=60)
        assert proc.returncode == 0
        assert "halo" in out