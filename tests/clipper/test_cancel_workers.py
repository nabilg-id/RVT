"""A cancel click has to stop the work, not just grey out a button.

``clipper.progress.Job`` grew a ``cancelled`` flag and ``/api/cancel`` grew an
endpoint, but on their own they changed nothing: no worker read the flag, so a
cancelled channel run kept downloading and a cancelled clip still built a
processor and ran Whisper over the video.

These tests cover the two readers and the state each job settles in. The flag
and the endpoint are covered in ``test_cancel.py``; the engine side - stopping
between videos, keeping the resume checkpoint - is in
``tests/youtube/test_channel_cancel.py``.
"""
from __future__ import annotations

import sys
import threading
import time
import types

import pytest

from clipper import app as A
from clipper.progress import Job
from clipper.styles.caption_styles import CAPTION_STYLES

HOST = {"Host": "127.0.0.1:8787"}
VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
VIDEO_ID = "dQw4w9WgXcQ"


class FakeProcessor:
    """A processor that records that it ran, and returns one clip."""

    built = 0

    def __init__(self, caption_style="clean_white"):
        FakeProcessor.built += 1
        self.caption_maker = types.SimpleNamespace(
            styles=CAPTION_STYLES, selected_style=caption_style
        )

    def process_video(self, url, num_clips, min_dur, max_dur):
        return (["clips/clip_1_88pts_dQw4w9WgXcQ.mp4"], "Judul Video")


class GatedProcessor(FakeProcessor):
    """Blocks inside the pipeline until the test lets it out.

    A clip job cannot be cancelled in the middle of a Whisper transcription by
    any means short of killing the thread, so the only honest test is the one
    that cancels while the work is genuinely in flight and then checks that the
    job settles as cancelled rather than done.
    """

    gate = threading.Event()

    def process_video(self, url, num_clips, min_dur, max_dur):
        GatedProcessor.gate.wait(10)
        return (["clips/clip_1_88pts_dQw4w9WgXcQ.mp4"], "Judul Video")


class GateEngine:
    """Stands in for ``channel_full``: walks videos, one permit at a time.

    The engine blocks before each video until the test allows it, so a cancel
    can be placed at an exact point in the run. Gating on a shared "is it
    cancelled yet" flag instead would leave the loop racing the test, and a
    cancellation test that passes only when the scheduler is kind is not a test.
    """

    def __init__(self, videos=("a1", "a2", "a3")):
        self.videos = list(videos)
        self.seen = []
        self.permits = threading.Semaphore(0)

    def allow(self, count=1):
        for _ in range(count):
            self.permits.release()

    def __call__(self, link, options, emitter=None, should_stop=None):
        for video_id in self.videos:
            self.permits.acquire()
            if should_stop is not None and should_stop():
                break
            self.seen.append(video_id)
            if emitter is not None:
                emitter.emit("progress", {"done": len(self.seen),
                                          "total": len(self.videos)})
        return {"status": True,
                "cancelled": bool(should_stop is not None and should_stop()),
                "result": {"total": len(self.videos), "success": len(self.seen),
                           "failed": 0, "unavailable": 0, "zipPath": "",
                           "items": [{"videoId": v, "ok": True} for v in self.seen]}}


@pytest.fixture(autouse=True)
def _registry(monkeypatch, tmp_path):
    """Give every test a private registry, counter, and ledger."""
    A.app.config["TESTING"] = True
    monkeypatch.setattr(A, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(A, "OUTPUT_DIR", tmp_path / "clips")
    monkeypatch.setattr(A, "_JOBS", {})
    monkeypatch.setattr(A, "_JOB_COUNTER", 0)
    FakeProcessor.built = 0
    GatedProcessor.gate = threading.Event()
    with A.app.test_client() as c:
        yield c


def _install_processor(monkeypatch, processor_cls):
    """Swap a fake VideoProcessor into ``sys.modules``.

    A ``sys.modules`` entry rather than a patch of the real module: importing
    ``clipper.services.video_processor`` pulls in yt-dlp, which does not import
    at all on Python 3.14.
    """
    mod = types.ModuleType("clipper.services.video_processor")
    mod.VideoProcessor = processor_cls
    monkeypatch.setitem(sys.modules, "clipper.services.video_processor", mod)


def _await(job_id, timeout=10.0):
    """Poll the status endpoint until the job settles, as the browser does."""
    client = A.app.test_client()
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = client.get(f"/api/status/{job_id}", headers=HOST).get_json()
        if payload["status"] != "running":
            return payload
        time.sleep(0.02)
    raise AssertionError("the job never settled")


def _run_download(engine):
    """Start a download job on ``engine`` and return its id."""
    return A._start_download(engine, "channel-full", "https://youtu.be/@x",
                             {"outputDir": "downloads"})


def _await_videos(engine, wanted, timeout=10.0):
    """Wait until the engine has walked exactly ``wanted`` videos."""
    deadline = time.time() + timeout
    while len(engine.seen) < wanted and time.time() < deadline:
        time.sleep(0.01)
    return engine.seen


class TestACancelledDownloadJobStopsTheRun:
    def test_it_stops_before_the_next_video(self, _registry):
        """The whole point: three videos queued, the user cancels, and none of
        them is ever started."""
        engine = GateEngine()
        job_id = _run_download(engine)

        _registry.post(f"/api/cancel/{job_id}", headers=HOST)
        engine.allow(5)

        payload = _await(job_id)
        assert engine.seen == []
        assert payload["status"] == "cancelled"

    def test_it_does_not_report_a_partial_run_as_done(self, _registry):
        """A cancelled run harvested part of a channel. Reporting done would
        write a completion into the run history the user never got, and the
        archive on disk is short by every video that was never started."""
        engine = GateEngine()
        job_id = _run_download(engine)

        _registry.post(f"/api/cancel/{job_id}", headers=HOST)
        engine.allow(5)
        _await(job_id)

        assert A._JOBS[job_id]["status"] == "cancelled"

    def test_a_cancelled_run_does_not_claim_full_progress(self, _registry):
        """100% would be a lie; the run stopped part way."""
        engine = GateEngine()
        job_id = _run_download(engine)

        engine.allow(1)
        _await_videos(engine, 1)
        _registry.post(f"/api/cancel/{job_id}", headers=HOST)
        engine.allow(5)
        _await(job_id)

        assert A._JOBS[job_id]["progress"] < 1

    def test_the_status_endpoint_says_cancelled(self, _registry):
        """The browser learns the outcome by polling, so the poll has to carry
        it."""
        engine = GateEngine()
        job_id = _run_download(engine)

        _registry.post(f"/api/cancel/{job_id}", headers=HOST)
        engine.allow(5)

        assert _await(job_id)["status"] == "cancelled"

    def test_an_uncancelled_run_still_finishes_as_done(self, _registry):
        """The other half of the contract: a run nobody cancelled must settle
        exactly as it did before."""
        engine = GateEngine()
        job_id = _run_download(engine)
        engine.allow(5)

        payload = _await(job_id)

        assert payload["status"] == "done"
        assert engine.seen == ["a1", "a2", "a3"]

    def test_a_cancelled_run_keeps_its_finished_videos(self, _registry):
        """Cancellation is not a rollback. The first video is already on disk
        and must still be reported - cancelling is not undoing."""
        engine = GateEngine()
        job_id = _run_download(engine)

        engine.allow(1)
        _await_videos(engine, 1)
        _registry.post(f"/api/cancel/{job_id}", headers=HOST)
        engine.allow(5)
        _await(job_id)

        assert engine.seen == ["a1"]


class TestACancelledClipJob:
    def test_an_already_cancelled_job_never_builds_a_processor(self, monkeypatch):
        """Whisper and the media stack are the expensive part. A job cancelled
        before it starts must not pay for any of it."""
        _install_processor(monkeypatch, FakeProcessor)
        job = Job(1, {"url": VIDEO_URL, "style": "clean_white"})
        job.cancel()

        A._run_clip_job(job, {"url": VIDEO_URL, "style": "clean_white",
                              "numClips": 1, "minDur": 10, "maxDur": 20})

        assert FakeProcessor.built == 0
        assert job.snapshot()["status"] == "cancelled"

    def test_a_cancelled_job_reports_no_clips(self, monkeypatch):
        _install_processor(monkeypatch, FakeProcessor)
        job = Job(1, {"url": VIDEO_URL, "style": "clean_white"})
        job.cancel()

        A._run_clip_job(job, {"url": VIDEO_URL, "style": "clean_white",
                              "numClips": 1, "minDur": 10, "maxDur": 20})

        assert job.snapshot()["outputs"] == []

    def test_a_job_cancelled_mid_run_settles_as_cancelled(self, _registry,
                                                        monkeypatch):
        """The video in flight is left to finish - it is several files being
        written - but the job must not end up claiming a clean success."""
        _install_processor(monkeypatch, GatedProcessor)
        started = _registry.post("/api/clip", json={
            "url": VIDEO_URL, "numClips": 1, "minDur": 10, "maxDur": 20,
            "style": "clean_white",
        }).get_json()
        job_id = started["jobId"]

        # The processor is now blocked inside the pipeline; cancel lands here.
        time.sleep(0.05)
        _registry.post(f"/api/cancel/{job_id}", headers=HOST)
        GatedProcessor.gate.set()

        assert _await(job_id)["status"] == "cancelled"

    def test_a_cancelled_clip_job_does_not_claim_clips_in_the_ledger(
        self, _registry, monkeypatch
    ):
        """The ledger is what the video board reads. Leaving it claiming the
        video is clipped, or failed, would both be wrong: the user stopped it."""
        from rch.core import tracker as T

        _install_processor(monkeypatch, GatedProcessor)
        started = _registry.post("/api/clip", json={
            "url": VIDEO_URL, "numClips": 1, "minDur": 10, "maxDur": 20,
            "style": "clean_white",
        }).get_json()
        job_id = started["jobId"]

        time.sleep(0.05)
        _registry.post(f"/api/cancel/{job_id}", headers=HOST)
        GatedProcessor.gate.set()
        _await(job_id)

        clip = T.read_state()[VIDEO_ID]["clip"]
        assert clip["status"] == "cancelled"

    def test_the_board_does_not_claim_the_cancelled_clip_succeeded(self, _registry,
                                                               monkeypatch):
        """The board reads the ledger, and it has three wrong answers available
        for a cancelled clip: still processing, clipped, or clip-failed. None of
        them is what happened, and the first one is the bug this feature exists
        to fix."""
        _install_processor(monkeypatch, GatedProcessor)
        started = _registry.post("/api/clip", json={
            "url": VIDEO_URL, "numClips": 1, "minDur": 10, "maxDur": 20,
            "style": "clean_white",
        }).get_json()
        job_id = started["jobId"]

        time.sleep(0.05)
        _registry.post(f"/api/cancel/{job_id}", headers=HOST)
        GatedProcessor.gate.set()
        _await(job_id)

        board = _registry.get("/api/videos").get_json()
        row = next(r for r in board["videos"] if r["videoId"] == VIDEO_ID)
        assert row["status"] not in {"processing", "clipped", "clip_failed"}
        assert row["clipStatus"] == "cancelled"

    def test_an_uncancelled_clip_job_is_still_done(self, _registry, monkeypatch):
        """Guard: the cancel checks must not change the normal path."""
        _install_processor(monkeypatch, FakeProcessor)
        started = _registry.post("/api/clip", json={
            "url": VIDEO_URL, "numClips": 1, "minDur": 10, "maxDur": 20,
            "style": "clean_white",
        }).get_json()

        payload = _await(started["jobId"])

        assert payload["status"] == "done"
        assert payload["outputs"]
