"""Clip jobs must leave a trace in the shared per-video ledger.

Before this, a clip was recorded with only a 60-character truncated title, so
there was no way to tell which YouTube video a finished clip came from, or that
a queued video was waiting at all.
"""
from __future__ import annotations

import json
import sys
import time
import types

import pytest

from clipper import app as A
from clipper.styles.caption_styles import CAPTION_STYLES

HOST = {"Host": "127.0.0.1:8787"}
VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
VIDEO_ID = "dQw4w9WgXcQ"


class FakeProcessor:
    def __init__(self, caption_style="clean_white"):
        self.caption_maker = types.SimpleNamespace(
            styles=CAPTION_STYLES, selected_style=caption_style
        )

    def process_video(self, url, num_clips, min_dur, max_dur):
        print("🎬 Processing 1 viral clips...")
        return (["clips/clip_1_88pts_dQw4w9WgXcQ.mp4"], "Judul Video")


class EmptyProcessor(FakeProcessor):
    def process_video(self, *a, **k):
        return ([], "Judul Kosong")


class BoomProcessor(FakeProcessor):
    def process_video(self, *a, **k):
        raise RuntimeError("pipeline meledak")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    A.app.config["TESTING"] = True
    monkeypatch.setattr(A, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(A, "OUTPUT_DIR", tmp_path / "clips")
    # See the note in _drain_jobs: clearing up front keeps this teardown from
    # waiting on a thread that belongs to an earlier test.
    with A._JOB_LOCK:
        A._JOBS.clear()
    with A.app.test_client() as c:
        yield c
        with A._JOB_LOCK:
            mine = set(A._JOBS)
    _drain_jobs(mine)
    with A._JOB_LOCK:
        A._JOBS.clear()


def _drain_jobs(job_ids, timeout=10.0):
    """Wait for this test's own worker threads before tearing down.

    A clip job runs on a background thread that reads module state the next test
    is about to change. Letting one outlive the test turns this file's stubs
    into a coin flip. Scoping to the ids this test created matters: waiting on
    every job in _JOBS means one stuck thread costs a ten-second timeout in
    every later test too.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        with A._JOB_LOCK:
            pending = [j for jid, j in A._JOBS.items()
                       if jid in job_ids and j.status == "running"]
        if not pending:
            return
        time.sleep(0.02)
    raise AssertionError("a clip job thread outlived its test")


def _install(monkeypatch, processor_cls):
    """Swap a fake VideoProcessor into ``sys.modules`` for one test.

    A ``sys.modules`` entry rather than a patch of the real module, and that is
    not a shortcut: importing ``clipper.services.video_processor`` pulls in
    yt-dlp, which does not import at all on Python 3.14 (it trips
    ``TypeError: function() argument 'code' must be code, not str``). The stub
    is the only way to run these tests without the media stack.

    Because ``clipper/app.py`` imports VideoProcessor inside the worker
    function, the import happens on a background thread, and a stub can be torn
    down before that thread reaches it - which is how a real VideoProcessor
    once got built here and reached huggingface.co for a Whisper model. The
    ``client`` fixture's ``_drain_jobs`` is what closes that window: it holds
    teardown until no job is still running.
    """
    mod = types.ModuleType("clipper.services.video_processor")
    mod.VideoProcessor = processor_cls
    monkeypatch.setitem(sys.modules, "clipper.services.video_processor", mod)
    return mod


def _run(client, monkeypatch, processor_cls, url=VIDEO_URL):
    """Start a job and wait for it to settle, as the browser would."""
    _install(monkeypatch, processor_cls)
    started = client.post("/api/clip", json={
        "url": url, "numClips": 1, "minDur": 10, "maxDur": 20,
        "style": "clean_white",
    }).get_json()
    job_id = started["jobId"]

    import time

    for _ in range(200):
        payload = client.get(f"/api/status/{job_id}").get_json()
        if payload["status"] != "running":
            return payload
        time.sleep(0.02)
    raise AssertionError("job tidak selesai")


def _state():
    from rch.core import tracker as T

    return T.read_state()


class TestClipLifecycleEvents:
    def test_start_records_running(self, client, monkeypatch):
        payload = _run(client, monkeypatch, FakeProcessor)
        assert payload["status"] == "done"
        entry = _state()[VIDEO_ID]
        assert entry["clip"]["status"] == "done"

    def test_running_is_recorded_before_completion(self, client, monkeypatch):
        """The board has to show 'processing', not jump from nothing to done."""
        seen = []

        class SlowProcessor(FakeProcessor):
            def process_video(self, *a, **k):
                import time

                from rch.core import tracker as T

                seen.append(T.effective_status(T.read_state()[VIDEO_ID])["status"])
                time.sleep(0.05)
                return (["clips/clip_1_88pts_dQw4w9WgXcQ.mp4"], "Judul")

        _install(monkeypatch, SlowProcessor)
        started = client.post("/api/clip", json={
            "url": VIDEO_URL, "numClips": 1, "minDur": 10, "maxDur": 20,
            "style": "clean_white",
        }).get_json()
        job_id = started["jobId"]

        import time

        # The worker runs in a thread, so wait for it to actually observe the
        # ledger rather than racing it.
        for _ in range(200):
            if seen:
                break
            time.sleep(0.01)
        assert seen == ["processing"]

        for _ in range(200):
            if client.get(f"/api/status/{job_id}").get_json()["status"] != "running":
                break
            time.sleep(0.02)
        assert client.get(f"/api/status/{job_id}").get_json()["status"] == "done"

    def test_done_records_the_produced_files(self, client, monkeypatch):
        _run(client, monkeypatch, FakeProcessor)
        entry = _state()[VIDEO_ID]
        assert entry["clip"]["files"] == ["clip_1_88pts_dQw4w9WgXcQ.mp4"]

    def test_done_records_the_style_and_title(self, client, monkeypatch):
        _run(client, monkeypatch, FakeProcessor)
        entry = _state()[VIDEO_ID]
        assert entry["clip"]["style"] == "clean_white"
        assert entry["title"] == "Judul Video"

    def test_exception_records_failure(self, client, monkeypatch):
        payload = _run(client, monkeypatch, BoomProcessor)
        assert payload["status"] == "error"
        entry = _state()[VIDEO_ID]
        assert entry["clip"]["status"] == "failed"
        assert "meledak" in entry["clip"]["error"]

    def test_no_output_is_treated_as_failure(self, client, monkeypatch):
        """An empty run leaves the video with clips=0; calling that done would
        show a green tick on the board for nothing."""
        _run(client, monkeypatch, EmptyProcessor)
        assert _state()[VIDEO_ID]["clip"]["status"] == "failed"

    def test_rejected_url_writes_nothing(self, client, monkeypatch):
        _install(monkeypatch, FakeProcessor)
        r = client.post("/api/clip", json={"url": "https://example.com/x"})
        assert r.status_code == 400
        assert _state() == {}


class TestClipHistoryVideoId:
    def _write(self, tmp_path, line):
        """Write a legacy-format line, which is what these tests exercise."""
        path = tmp_path / A._LEGACY_HISTORY_FILE
        path.write_text(line + "\n", encoding="utf-8")
        return path

    def test_new_line_carries_the_id(self, client, tmp_path, monkeypatch):
        _run(client, monkeypatch, FakeProcessor)
        raw = (tmp_path / A._RCH_HISTORY_FILE).read_text(encoding="utf-8")
        assert json.loads(raw.strip())["videoId"] == VIDEO_ID

    def test_reader_surfaces_the_id(self, client, tmp_path, monkeypatch):
        _run(client, monkeypatch, FakeProcessor)
        rows = A.read_history()
        assert rows[0]["videoId"] == VIDEO_ID

    def test_legacy_seven_field_line_still_reads(self, client, tmp_path):
        self._write(tmp_path,
                    "2026-01-01 00:00:00 | clean_white | clips=2 | min=10 | max=20"
                    " | done | Judul Lama")
        rows = A.read_history()
        assert len(rows) == 1
        assert rows[0]["videoId"] is None
        assert rows[0]["title"] == "Judul Lama"
        assert rows[0]["clips"] == 2

    def test_placeholder_id_is_not_reported(self, client, tmp_path):
        self._write(tmp_path,
                    "2026-01-01 00:00:00 | clean_white | clips=1 | min=10 | max=20"
                    " | error | Gagal | id=-")
        assert A.read_history()[0]["videoId"] is None

    def test_short_lines_are_skipped(self, client, tmp_path):
        self._write(tmp_path, "terlalu | pendek")
        assert A.read_history() == []


class TestTrackerFailureNeverBreaksAJob:
    def test_job_still_completes_when_tracking_explodes(self, client, monkeypatch):
        """Tracking is observability. A broken ledger must not cost the user
        their clip."""

        class Exploding:
            @staticmethod
            def append_event(*a, **k):
                raise RuntimeError("disk on fire")

        import rch.core.tracker as real

        monkeypatch.setattr(real, "append_event", Exploding.append_event)
        monkeypatch.setattr(
            "rch.core.tracker.append_event", Exploding.append_event,
            raising=False,
        )

        payload = _run(client, monkeypatch, FakeProcessor)
        assert payload["status"] == "done"
        assert payload["outputs"] == ["clip_1_88pts_dQw4w9WgXcQ.mp4"]