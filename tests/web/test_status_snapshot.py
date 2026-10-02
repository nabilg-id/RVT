"""``/api/status`` must hand out a deep, detached snapshot of the job record.

The live record's ``items`` list is appended to by worker threads while the
response is serialised. A shallow copy would leave ``items`` shared, so a
worker appending mid-serialisation could render a torn row. ``_snapshot``
must therefore deep-copy, not just clone the top-level dict.
"""
from __future__ import annotations

import pytest

from rch.web import server as srv


@pytest.fixture()
def client():
    srv.app.config["TESTING"] = True
    with srv.app.test_client() as c:
        yield c
    with srv._JOB_LOCK:
        srv._JOBS.clear()


class TestSnapshot:
    def test_items_list_is_deeply_detached(self):
        job = {
            "status": "running", "progress": 0, "phase": "Mulai",
            "items": [{"ok": True, "videoId": "v1", "title": "T", "error": ""}],
            "result": None, "error": None,
        }

        snap = srv._snapshot(job)
        job["items"].append({"ok": False, "videoId": "v2", "title": "U", "error": "boom"})

        assert len(snap["items"]) == 1

    def test_mutating_the_snapshot_does_not_touch_the_record(self):
        job = {
            "status": "running", "progress": 0, "phase": "Mulai",
            "items": [], "result": None, "error": None,
        }

        snap = srv._snapshot(job)
        snap["items"].append({"injected": True})

        assert job["items"] == []

    def test_nested_result_is_detached_too(self):
        job = {
            "status": "done", "progress": 1, "phase": "Selesai",
            "items": [], "result": {"items": [{"videoId": "v1"}]}, "error": None,
        }

        snap = srv._snapshot(job)
        job["result"]["items"].append({"videoId": "v2"})

        assert snap["result"]["items"] == [{"videoId": "v1"}]

    def test_unknown_fields_are_not_whitelisted(self):
        job = {
            "status": "done", "progress": 1, "phase": "Selesai",
            "items": [], "result": {"total": 1}, "error": None,
        }

        assert set(srv._snapshot(job)) == {
            "status", "progress", "phase", "items", "result", "error",
        }


class TestStatusEndpoint:
    def test_missing_job_is_a_404(self, client):
        assert client.get("/api/status/424242").status_code == 404

    def test_endpoint_uses_a_snapshot(self, client):
        with srv._JOB_LOCK:
            srv._JOBS[1] = {
                "status": "running", "progress": 0, "phase": "Mulai",
                "items": [{"ok": True, "videoId": "v1", "title": "T", "error": ""}],
                "result": None, "error": None,
            }

        payload = client.get("/api/status/1").get_json()

        assert payload["items"] == [{"ok": True, "videoId": "v1", "title": "T", "error": ""}]
