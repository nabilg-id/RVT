"""Tests for one job registry serving both clip jobs and download jobs.

The two apps each had their own ``_JOBS`` dict and their own ``_JOB_COUNTER``
starting at zero, so both handed out job id 1, 2, 3... and both served
``/api/status/<int:job_id>``. Merging the routes without merging the registry
would have made job 1 ambiguous: the download page and the clipper page would
have been reading each other's jobs.

One counter is the fix. Ids are unique across both kinds, one registry holds
both, and every status response says which kind it is so a client can tell them
apart without guessing.
"""
from __future__ import annotations

import pytest

from clipper import app as A
from clipper.progress import Job
from rch.core.jobs import DEFAULT_MAX_JOBS

HOST = {"Host": "127.0.0.1:8787"}


@pytest.fixture(autouse=True)
def _registry(monkeypatch, tmp_path):
    """Give every test a private registry and counter.

    No real worker thread is started here - these tests register records
    directly - so there is nothing to drain at teardown.
    """
    A.app.config["TESTING"] = True
    monkeypatch.setattr(A, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(A, "OUTPUT_DIR", tmp_path / "clips")
    monkeypatch.setattr(A, "_JOBS", {})
    monkeypatch.setattr(A, "_JOB_COUNTER", 0)


def _clip_job(payload=None):
    """Allocate a clip job and register it, the way /api/clip does."""
    job = Job(A._next_job_id(), payload or {"url": "https://youtu.be/dQw4w9WgXcQ"})
    with A._JOB_LOCK:
        A._JOBS[job.id] = job
    return job


def _download_job(command="channel-info"):
    """Register a download job the way the channel endpoints will."""
    return A._start_download_job(command)


def _get(job_id):
    return A.app.test_client().get(f"/api/status/{job_id}", headers=HOST)


class TestIdAllocation:
    def test_two_kinds_never_share_an_id(self):
        """The collision this whole step exists to remove."""
        clip = _clip_job()
        download = _download_job()

        assert clip.id != download

    def test_ids_keep_climbing_across_kinds(self):
        first = _download_job()
        second = _clip_job()
        third = _download_job()

        assert len({first, second.id, third}) == 3

    def test_allocation_is_ordered(self):
        ids = [_download_job(), _clip_job().id, _download_job()]

        assert ids == sorted(ids)


class TestOneRegistry:
    def test_both_kinds_live_in_the_same_dict(self):
        clip = _clip_job()
        download = _download_job()

        assert set(A._JOBS) == {clip.id, download}

    def test_a_download_job_records_its_kind(self):
        download = _download_job("channel-full")

        assert A._JOBS[download]["kind"] == "download"
        assert A._JOBS[download]["command"] == "channel-full"

    def test_a_clip_job_says_it_is_a_clip(self):
        clip = _clip_job()

        assert clip.snapshot()["kind"] == "clip"


class TestStatusRoute:
    def test_it_serves_a_clip_job(self):
        clip = _clip_job()
        A._JOBS[clip.id] = clip

        body = _get(clip.id).get_json()

        assert body["status"] == "running"
        assert body["kind"] == "clip"

    def test_it_serves_a_download_job(self):
        download = _download_job()

        body = _get(download).get_json()

        assert body["kind"] == "download"
        assert "items" in body

    def test_a_download_job_still_reports_progress_and_phase(self):
        """Both UIs read these three, so both kinds have to carry them."""
        download = _download_job()
        A._JOBS[download]["phase"] = "2/10"
        A._JOBS[download]["progress"] = 0.2

        body = _get(download).get_json()

        assert body["phase"] == "2/10"
        assert body["progress"] == 0.2
        assert body["status"] == "running"

    def test_an_unknown_id_is_still_404(self):
        assert _get(999).status_code == 404

    def test_polling_a_finished_download_job_does_not_re_emit(self):
        """Snapshotting must be side-effect free; only the clipper's history
        write depends on being called once, and that stays on its own branch."""
        download = _download_job()
        A._JOBS[download].update(status="done", progress=1.0)

        first = _get(download).get_json()
        second = _get(download).get_json()

        assert first == second


class TestDownloadJobLifecycle:
    def test_a_new_download_job_starts_running(self):
        job_id = _download_job()

        job = A._JOBS[job_id]
        assert job["status"] == "running"
        assert job["progress"] == 0
        assert job["phase"] == "Mulai"
        assert job["items"] == []

    def test_it_can_be_finished_with_a_result(self):
        job_id = _download_job()

        A._update_download_job(job_id, status="done", progress=1.0,
                               phase="Selesai", result={"total": 3})

        job = A._JOBS[job_id]
        assert job["status"] == "done"
        assert job["result"] == {"total": 3}
        assert job["finishedAt"]

    def test_a_failure_carries_the_error(self):
        job_id = _download_job()

        A._update_download_job(job_id, status="error", progress=1.0,
                               phase="Gagal", error="jaringan mati")

        assert A._JOBS[job_id]["error"] == "jaringan mati"

    def test_it_can_record_a_finished_video(self):
        job_id = _download_job()

        A._append_download_item(job_id, {"videoId": "v1", "ok": True})

        assert A._JOBS[job_id]["items"] == [{"videoId": "v1", "ok": True}]

    def test_touching_a_pruned_job_is_ignored(self):
        """A worker can outlive its record once the registry prunes it, and
        raising there would turn a finished run into a crash."""
        A._update_download_job(4242, status="done")
        A._append_download_item(4242, {"videoId": "v1"})

    def test_finished_download_jobs_are_pruned(self):
        for _ in range(DEFAULT_MAX_JOBS + 20):
            job_id = _download_job()
            A._update_download_job(job_id, status="done")

        downloads = [j for j in A._JOBS.values() if j.get("kind") == "download"]
        assert len(downloads) <= DEFAULT_MAX_JOBS

    def test_a_running_clip_job_is_not_pruned_away(self):
        """Pruning is what makes the registry bounded; it must not cost a live
        clip job its status endpoint."""
        clip = _clip_job()
        A._JOBS[clip.id] = clip

        for i in range(DEFAULT_MAX_JOBS + 20):
            job_id = _download_job()
            A._update_download_job(job_id, status="done")

        assert clip.id in A._JOBS
        assert _get(clip.id).status_code == 200
