"""Keep test runs out of the working tree, and out of the network.

Two classes of leak are guarded here.

**Working tree.** The downloader, the channel workers and the clipper job all
record progress in the shared ledger, which defaults to
``<repo>/temp/video-tracker.jsonl``. Harmless to the code, but it hides ordering
bugs, and because ``.gitignore`` covers ``temp/`` the leak is invisible to
``git status``. Redirected per test into ``tmp_path``.

**Network.** ``rch.youtube.metadata.get_video_info`` and ``rch.youtube.video``
shell out to the ``yt-dlp`` binary. Those are separate processes, so blocking
Python sockets is not enough - one preview test was spending 7.7 seconds per run
talking to YouTube, and a Windows file lock from that download was then failing
unrelated downloader tests. Sockets and the yt-dlp subprocess are both blocked,
so a test that forgets a stub fails immediately and says why.

Blocking alone is not enough, though: the product code degrades gracefully when a
lookup fails, so a test whose stub was forgotten would still pass - quietly
exercising the fallback instead of what it meant to test. Every trip is recorded
and the session fails at the end listing the offenders.

The guard is deliberately narrow. Only external hosts are refused, only yt-dlp
is refused at the subprocess boundary, and anything a test patches itself still
wins because the fixture is set up before the test body runs.
"""
import os
import socket
import subprocess
import tempfile
import traceback
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent

#: Redirect the Downloads resolver before any test module is imported.
#:
#: ``rch.core.paths`` resolves the real operating-system Downloads folder, and
#: ``clipper.config`` creates its output directory at import time. Doing this in
#: an autouse fixture would be too late - collection imports the modules first -
#: so merely running the suite would create a ``Ridikc Video Toolkit`` folder
#: inside the developer's actual Downloads. Module-level code here runs first,
#: and the directory is created up front because the resolver only accepts a
#: folder that already exists.
_TEST_DOWNLOADS = Path(tempfile.mkdtemp(prefix="rvt-test-downloads-")) / "Downloads"
_TEST_DOWNLOADS.mkdir(parents=True, exist_ok=True)
os.environ["RCH_DOWNLOADS_DIR"] = str(_TEST_DOWNLOADS)

_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules",
              ".ruff_cache"}

#: Loopback is fine: the Flask apps run through the test client, and a test that
#: binds a real socket does so on localhost.
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "", "0.0.0.0"}

_YTDLP_NAMES = ("yt-dlp", "youtube-dl", "yt_dlp.py")

#: Directory names whose frames are HTTP plumbing rather than the code under
#: test. Compared as whole path segments so ``http`` matches ``.../http/client.py``
#: on every platform without also matching ``httpcore`` or ``myhttp``.
_TRANSPORT_DIRS = frozenset({
    "http", "urllib", "urllib3", "requests", "huggingface_hub", "httpcore",
    "httpx", "certifi", "charset_normalizer", "idna", "ssl", "socket",
})


def _is_transport_frame(filename):
    """True when a frame lives in the HTTP stack rather than in project code."""
    return any(part in _TRANSPORT_DIRS for part in Path(filename).parts)


class NetworkAccessDuringTests(AssertionError):
    """Raised instead of letting a test reach the internet."""


#: Node id -> why it tried, so the session failure explains itself instead of
#: just naming a test.
_TRIED_TO_REACH_NETWORK: dict = {}
_ALLOWED_TO_REACH_NETWORK: set = set()


def allow_network_for_this_test(request):
    """Opt a test out of the session-end network check.

    Only for tests whose whole purpose is to prove the guard fires, like
    ``tests/test_no_network_guard.py``. The guard still refuses them - that is
    what they are asserting - they are simply not reported as offenders.
    """
    _ALLOWED_TO_REACH_NETWORK.add(request.node.nodeid)


@pytest.fixture(autouse=True)
def isolate_tracker(monkeypatch, tmp_path):
    monkeypatch.setenv("VIDEO_TRACKER_FILE", str(tmp_path / "video-tracker.jsonl"))


@pytest.fixture(autouse=True)
def block_network(monkeypatch, request):
    """Refuse outbound connections and yt-dlp invocations."""
    nodeid = request.node.nodeid
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo
    real_run = subprocess.run
    real_popen = subprocess.Popen

    def _host_of(address):
        if isinstance(address, (tuple, list)) and address:
            return str(address[0])
        return str(address)

    def _record(reason):
        # The stack matters as much as the reason. A leak is almost always a
        # background thread or a helper two modules away, so naming only the
        # test that happened to be running when it fired is not enough to find
        # it. Frames inside the test framework and this conftest are dropped to
        # keep the report readable.
        stack = [
            f"{frame.name} ({Path(frame.filename).name}:{frame.lineno})"
            for frame in traceback.extract_stack()
            if "conftest.py" not in frame.filename
            and "pluggy" not in frame.filename
            and "_pytest" not in frame.filename
            # Transport internals say "a request went out", not "who sent it".
            # Dropping them keeps room for the frames that name the culprit.
            # Matched on path segments rather than file names, because the
            # requests package keeps its code in _client.py / connection_pool.py
            # and stdlib http keeps its client in http/client.py.
            and not _is_transport_frame(frame.filename)
        ]
        detail = f"{reason}\n      via {' <- '.join(reversed(stack[-16:]))}"
        _TRIED_TO_REACH_NETWORK.setdefault(nodeid, detail)
        return NetworkAccessDuringTests(detail)

    def _guarded_connect(self, address):
        host = _host_of(address)
        if host not in _LOCAL_HOSTS:
            raise _record(
                f"test opened a socket to {host!r}; stub the network instead"
            )
        return real_connect(self, address)

    def _guarded_getaddrinfo(host, *args, **kwargs):
        if str(host) not in _LOCAL_HOSTS:
            raise _record(f"test resolved {host!r}; stub the network instead")
        return real_getaddrinfo(host, *args, **kwargs)

    def _guard_argv(args):
        if isinstance(args, (list, tuple)):
            joined = " ".join(str(a) for a in args)
        else:
            joined = str(args)
        if any(name in joined for name in _YTDLP_NAMES):
            raise _record(
                f"test spawned yt-dlp: {joined[:140]!r}; inject a fake "
                f"download_video/run_ytdlp instead"
            )

    def _guarded_run(args, *rest, **kwargs):
        _guard_argv(args)
        return real_run(args, *rest, **kwargs)

    class _GuardedPopen(real_popen):
        """The real Popen, refusing to start yt-dlp.

        A plain function would be the obvious way to wrap this, but
        ``subprocess.Popen`` is subclassed by libraries at import time - yt_dlp
        does ``class Popen(subprocess.Popen)`` - and a function in that slot
        raises ``TypeError: function() argument 'code' must be code, not str``
        the moment such an import happens inside a test. That is not a cosmetic
        failure: it made ``clipper.services.youtube_downloader`` and
        ``video_processor`` unimportable during the run, which pushed the
        downloader and the whole clip pipeline behind a skip and hid real
        regressions behind a green tick. Staying a subclass keeps the slot a
        type while still refusing the one command we care about.
        """

        def __init__(self, args, *rest, **kwargs):
            _guard_argv(args)
            super().__init__(args, *rest, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", _guarded_getaddrinfo)
    monkeypatch.setattr(subprocess, "run", _guarded_run)
    monkeypatch.setattr(subprocess, "Popen", _GuardedPopen)


@pytest.fixture(scope="session", autouse=True)
def no_test_reached_the_network():
    """Fail at the end of the run if any test tried to leave the machine."""
    yield
    offenders = {
        nodeid: reason
        for nodeid, reason in _TRIED_TO_REACH_NETWORK.items()
        if nodeid not in _ALLOWED_TO_REACH_NETWORK
    }
    assert not offenders, (
        "these tests reached for the network; stub it instead:\n"
        + "\n".join(f"  {nodeid}\n      {reason}" for nodeid, reason in sorted(offenders.items()))
    )


@pytest.fixture(scope="session", autouse=True)
def repo_stays_clean():
    """Fail once at the end of the session if the run touched the repo.

    Session-scoped on purpose: snapshotting the whole tree per test would cost a
    full rglob over 1800 tests. Two walks for the whole session is enough to
    catch the leaks this guard exists for - a tracker file, a stray output
    folder - because both are cases where a test wrote outside ``tmp_path`` and
    ``.gitignore`` was hiding it.

    Gitignored paths are excluded, since build and cache output landing in the
    tree is normal and not a defect.
    """
    before = _snapshot()
    yield
    leaked = sorted(_snapshot() - before)
    assert not leaked, f"the test run wrote into the working tree: {leaked}"


def _snapshot():
    found = set()
    for path in REPO_ROOT.rglob("*"):
        parts = path.relative_to(REPO_ROOT).parts
        if not parts or parts[0] in _SKIP_DIRS:
            continue
        if path.is_file() and not path.name.endswith(".pyc"):
            found.add(str(path.relative_to(REPO_ROOT)))
    return {p for p in found if not _is_ignored(p)}


def _is_ignored(rel_path):
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", rel_path],
            cwd=str(REPO_ROOT),
            capture_output=True,
            timeout=10,
        )
    except Exception:  # noqa: BLE001 - a missing git must not fail the suite
        return True
    return result.returncode == 0