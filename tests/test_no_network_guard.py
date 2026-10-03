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