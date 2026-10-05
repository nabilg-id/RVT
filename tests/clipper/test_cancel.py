"""Tests for cancelling a running job.

There was no way to stop a job at all. A channel run touches hundreds of videos
and a clip job runs Whisper, face tracking and encoding; when one goes wrong the
only option was to kill the server, which loses every other job in flight and
leaves the ledger claiming the video is still processing.

Cancellation has to be honest about three things: it stops the *next* item
rather than the current one, the job ends in a state of its own rather than
being reported done or failed, and cancelling something already finished
changes nothing.
"""
from __future__ import annotations

import pytest

from clipper import app as A
from clipper.progress import Job

HOST = {"Host": "127.0.0.1:8787"}


class TestJobFlag:
    def test_a_new_job_is_not_cancelled(self):
        assert Job(1, {"url": "u"}).cancelled is False

    def test_cancel_marks_it(self):
        job = Job(1, {"url": "u"})
        job.cancel()
        assert job.cancelled is True

    def test_cancel_is_idempotent(self):
        job = Job(1, {"url": "u"})
        job.cancel()
        job.cancel()
        assert job.cancelled is True

    def test_a_snapshot_reports_the_request(self):
        """The button greys out from the status poll, so the flag has to be in
        the response or the browser never learns the click landed."""
        job = Job(1, {"url": "u"})
        job.cancel()
        assert job.snapshot()["cancelled"] is True

    def test_a_fresh_snapshot_does_not_claim_cancellation(self):
        assert Job(1, {"url": "u"}).snapshot()["cancelled"] is False


class TestFinishAfterCancel:
    def test_a_cancelled_job_ends_as_cancelled_not_done(self):
        """Reporting done would write a completion the user never got."""
        job = Job(1, {"url": "u"})
        job.cancel()
        job.finish()
        assert job.snapshot()["status"] == "cancelled"

    def test_cancelling_a_failed_job_does_not_rewrite_it(self):
        job = Job(1, {"url": "u"})
        job.finish(error="boom")
        job.cancel()
        assert job.snapshot()["status"] == "error"

    def test_cancelling_a_finished_job_changes_nothing(self):
        job = Job(1, {"url": "u"})
        job.finish()
        job.cancel()
        assert job.snapshot()["status"] == "done"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    A.app.config["TESTING"] = True
    monkeypatch.setattr(A, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(A, "OUTPUT_DIR", tmp_path / "clips")
    monkeypatch.setattr(A, "_JOBS", {})
    monkeypatch.setattr(A, "_JOB_COUNTER", 0)
    with A.app.test_client() as c:
        yield c


class TestCancelEndpoint:
    def _register_clip(self):
        job = Job(A._next_job_id(), {"url": "https://youtu.be/dQw4w9WgXcQ"})
        with A._JOB_LOCK:
            A._JOBS[job.id] = job
        return job

    def _register_download(self):
        return A._start_download_job("channel-full")

    def test_it_cancels_a_running_clip_job(self, client):
        job = self._register_clip()
        res = client.post(f"/api/cancel/{job.id}", headers=HOST)
        assert res.status_code == 200
        assert job.cancelled is True

    def test_it_reports_the_new_state(self, client):
        job = self._register_clip()
        body = client.post(f"/api/cancel/{job.id}", headers=HOST).get_json()
        assert body["cancelled"] is True

    def test_it_cancels_a_download_job(self, client):
        job_id = self._register_download()
        res = client.post(f"/api/cancel/{job_id}", headers=HOST)
        assert res.status_code == 200
        assert A._JOBS[job_id]["cancelled"] is True

    def test_an_unknown_job_is_404(self, client):
        assert client.post("/api/cancel/9999", headers=HOST).status_code == 404

    def test_cancelling_twice_is_not_an_error(self, client):
        job = self._register_clip()
        first = client.post(f"/api/cancel/{job.id}", headers=HOST)
        second = client.post(f"/api/cancel/{job.id}", headers=HOST)
        assert first.status_code == second.status_code == 200

    def test_a_finished_job_is_left_alone(self, client):
        """A user clicking late must not see their completed job flipped."""
        job = self._register_clip()
        job.finish()
        client.post(f"/api/cancel/{job.id}", headers=HOST)
        assert job.snapshot()["status"] == "done"