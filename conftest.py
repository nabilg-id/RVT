"""Keep test runs out of the working tree.

The downloader, the channel workers and the clipper job all record progress in
the shared ledger, which defaults to ``<repo>/temp/video-tracker.jsonl``.
Without redirection every suite run appends to a real file in the repository.
It is harmless to the code but it hides ordering bugs, and because .gitignore
covers ``temp/`` the leak is invisible to ``git status``.

Redirected per test into tmp_path. Tests that care about the real default set
their own path afterwards, which wins because this runs first.
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent

_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".ruff_cache"}


@pytest.fixture(autouse=True)
def isolate_tracker(monkeypatch, tmp_path):
    monkeypatch.setenv("VIDEO_TRACKER_FILE", str(tmp_path / "video-tracker.jsonl"))


@pytest.fixture(scope="session", autouse=True)
def repo_stays_clean():
    """Fail once at the end of the session if the run touched the repo.

    Session-scoped on purpose: snapshotting the whole tree per test would cost
    a full rglob over 1500 tests. Two walks for the whole session is enough to
    catch the leaks this guard exists for - a tracker file, a stray output
    folder - because both are cases where a test wrote outside tmp_path and
    .gitignore was hiding it.

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
    import subprocess

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