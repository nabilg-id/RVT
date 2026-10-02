"""Tests for rch.web.server — the local Flask GUI.

Covers the job registry (``_next_job_id``, ``_emitter``, ``_start_channel_job``),
the result reducer (``_summarise``) and every HTTP endpoint via Flask's test
client. No socket is ever opened and no engine function runs for real: the
channel entry points are patched at their defining module, and the background
worker threads are joined deterministically.
"""
from __future__ import annotations

import threading

import pytest

from rch.web import server as srv

VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture
def client():
    srv.app.config.update(TESTING=True)
    with srv.app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_registry():
    """Isolate the module-level job registry and shutdown flag per test."""
    with srv._JOB_LOCK:
        srv._JOBS.clear()
        srv._JOB_COUNTER = 0
    srv._SHUTDOWN.clear()
    yield
    with srv._JOB_LOCK:
        srv._JOBS.clear()
        srv._JOB_COUNTER = 0
    srv._SHUTDOWN.clear()


def _drain(job_id, timeout=5.0):
    """Block until a background job leaves the 'running' state."""
    for _ in range(int(timeout * 100)):
        with srv._JOB_LOCK:
            job = srv._JOBS.get(job_id)
            if job is not None and job["status"] != "running":
                return job
        threading.Event().wait(0.01)
    raise AssertionError(f"job {job_id} never finished")


def _drain_running(job_id, release, timeout=5.0):
    """Wait until a job is in flight, then release it and wait for completion."""
    for _ in range(int(timeout * 100)):
        with srv._JOB_LOCK:
            job = srv._JOBS.get(job_id)
            if job is not None and job["items"]:
                break
        threading.Event().wait(0.01)
    release.set()
    return _drain(job_id, timeout)


def _job(client, job_id):
    return client.get(f"/api/status/{job_id}").get_json()


# ---------------------------------------------------------------------------
# _next_job_id
# ---------------------------------------------------------------------------


class TestNextJobId:
    def test_ids_start_at_one(self):
        assert srv._next_job_id() == 1

    def test_ids_increment_monotonically(self):
        assert [srv._next_job_id() for _ in range(3)] == [1, 2, 3]

    def test_ids_are_unique_across_threads(self):
        seen = []
        barrier = threading.Barrier(4)

        def worker():
            barrier.wait()
            seen.extend(srv._next_job_id() for _ in range(25))

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(seen) == 100
        assert len(set(seen)) == 100


# ---------------------------------------------------------------------------
# _summarise
# ---------------------------------------------------------------------------


class TestSummarise:
    def test_non_dict_passes_through(self):
        assert srv._summarise("string") == "string"
        assert srv._summarise(None) is None
        assert srv._summarise([1, 2]) == [1, 2]

    def test_failure_becomes_a_message(self):
        assert srv._summarise({"status": False, "message": "boom"}) == {"message": "boom"}

    def test_failure_without_message_uses_generic_text(self):
        assert srv._summarise({"status": False}) == {"message": "Gagal"}

    def test_counts_are_carried_through(self):
        result = srv._summarise({"status": True, "result": {
            "total": 5, "success": 4, "failed": 1, "unavailable": 2,
            "zipPath": "/tmp/x.zip",
        }})

        assert result["total"] == 5
        assert result["success"] == 4
        assert result["failed"] == 1
        assert result["unavailable"] == 2
        assert result["zipPath"] == "/tmp/x.zip"

    def test_result_payload_is_not_dict(self):
        assert srv._summarise({"status": True, "result": "teks"}) == "teks"

    def test_video_item_shape_maps_ok_and_video_id(self):
        result = srv._summarise({"status": True, "result": {"items": [
            {"ok": True, "videoId": "v1", "title": "T", "error": None},
        ]}})

        # ``status`` is cross-read from the shared ledger; "v1" is not a valid
        # id, so nothing is found and the field is None rather than missing.
        assert result["items"] == [
            {"ok": True, "videoId": "v1", "title": "T", "error": "",
             "status": None},
        ]

    def test_full_item_shape_maps_video_ok_to_ok(self):
        """channel-full items carry ``videoOk``, not ``ok`` — the UI needs ``ok``."""
        result = srv._summarise({"status": True, "result": {"items": [
            {"videoId": "v1", "title": "T", "videoOk": True, "thumbOk": True,
             "unavailable": False, "error": None},
        ]}})

        assert result["items"][0]["ok"] is True

    def test_full_item_failure_maps_to_ok_false(self):
        result = srv._summarise({"status": True, "result": {"items": [
            {"videoId": "v1", "videoOk": False, "thumbOk": False,
             "unavailable": False, "error": "403"},
        ]}})

        assert result["items"][0]["ok"] is False
        assert result["items"][0]["error"] == "403"

    def test_item_falls_back_to_id_when_video_id_missing(self):
        result = srv._summarise({"status": True, "result": {"items": [{"id": "v9", "ok": True}]}})

        assert result["items"][0]["videoId"] == "v9"

    def test_non_dict_items_are_dropped(self):
        result = srv._summarise({"status": True, "result": {"items": ["x", 5, None]}})

        assert result["items"] == []

    def test_missing_title_defaults_to_empty_string(self):
        result = srv._summarise({"status": True, "result": {"items": [{"ok": True, "id": "v"}]}})

        assert result["items"][0]["title"] == ""

    def test_absent_items_yields_empty_list(self):
        assert srv._summarise({"status": True, "result": {}})["items"] == []

    def test_null_item_is_safe(self):
        result = srv._summarise({"status": True, "result": {"items": [None]}})

        assert result["items"] == []

    def test_bulk_fields_are_not_leaked(self):
        result = srv._summarise({"status": True, "result": {
            "total": 1, "workDir": "/tmp/secret-workdir", "channel": "https://x/@ch",
        }})

        assert "workDir" not in result
        assert "channel" not in result


# ---------------------------------------------------------------------------
# _emitter
# ---------------------------------------------------------------------------


class TestEmitter:
    def _bind(self, job_id):
        srv._JOBS[job_id] = {"status": "running", "progress": 0, "phase": "Mulai",
                             "items": [], "result": None, "error": None}
        return srv._emitter(job_id)

    def test_progress_updates_fraction_and_phase(self):
        emitter = self._bind(1)

        emitter.emit("progress", {"done": 1, "total": 4})

        assert srv._JOBS[1]["progress"] == 0.25
        assert srv._JOBS[1]["phase"] == "1/4"

    def test_progress_is_capped_at_one(self):
        emitter = self._bind(1)

        emitter.emit("progress", {"done": 9, "total": 4})

        assert srv._JOBS[1]["progress"] == 1.0

    def test_progress_without_total_is_ignored(self):
        emitter = self._bind(1)

        emitter.emit("progress", {"done": 1})

        assert srv._JOBS[1]["progress"] == 0

    def test_null_progress_payload_is_ignored(self):
        emitter = self._bind(1)

        emitter.emit("progress", None)

        assert srv._JOBS[1]["progress"] == 0

    def test_phase_prefers_the_human_message(self):
        emitter = self._bind(1)

        emitter.emit("phase", {"phase": "list", "msg": "Mengambil daftar"})

        assert srv._JOBS[1]["phase"] == "Mengambil daftar"

    def test_phase_payload_is_never_rendered_raw(self):
        emitter = self._bind(1)

        emitter.emit("phase", {"phase": "list", "msg": "M"})

        assert "{" not in srv._JOBS[1]["phase"]

    def test_phase_without_msg_falls_back_to_phase_key(self):
        emitter = self._bind(1)

        emitter.emit("phase", {"phase": "download"})

        assert srv._JOBS[1]["phase"] == "download"

    def test_video_done_ok_appends_a_row(self):
        emitter = self._bind(1)

        emitter.emit("video:done", {"id": "v1", "title": "T", "ok": True})

        assert srv._JOBS[1]["items"] == [
            {"ok": True, "videoId": "v1", "title": "T", "error": "",
             "status": None},
        ]

    def test_video_done_failure_appends_a_failed_row(self):
        emitter = self._bind(1)

        emitter.emit("video:done", {"id": "v2", "title": "T", "ok": False, "error": "403"})

        assert srv._JOBS[1]["items"][0]["ok"] is False
        assert srv._JOBS[1]["items"][0]["error"] == "403"

    def test_rows_accumulate_in_emission_order(self):
        emitter = self._bind(1)

        emitter.emit("video:done", {"id": "v1", "ok": True})
        emitter.emit("video:done", {"id": "v2", "ok": True})

        assert [i["videoId"] for i in srv._JOBS[1]["items"]] == ["v1", "v2"]

    def test_events_for_an_unknown_job_are_ignored(self):
        emitter = self._bind(1)
        with srv._JOB_LOCK:
            srv._JOBS.pop(1)

        emitter.emit("video:done", {"id": "v1", "ok": True})
        emitter.emit("progress", {"done": 1, "total": 2})

        assert srv._JOBS == {}


# ---------------------------------------------------------------------------
# _start_channel_job
# ---------------------------------------------------------------------------


class TestStartChannelJob:
    def test_success_marks_the_job_done(self, monkeypatch):
        job_id = srv._start_channel_job(
            lambda link, opts, emitter=None: {"status": True, "result": {
                "total": 1, "success": 1, "failed": 0, "items": [],
            }},
            "https://x/@ch", {},
        )

        job = _drain(job_id)

        assert job["status"] == "done"
        assert job["progress"] == 1
        assert job["phase"] == "Selesai"
        assert job["result"]["total"] == 1

    def test_initial_record_is_running(self, monkeypatch):
        started = threading.Event()

        def slow(link, opts, emitter=None):
            started.set()
            threading.Event().wait(0.2)
            return {"status": True, "result": {}}

        job_id = srv._start_channel_job(slow, "u", {})
        started.wait(2)
        with srv._JOB_LOCK:
            assert srv._JOBS[job_id]["status"] == "running"

    def test_exception_marks_the_job_error(self):
        def boom(link, opts, emitter=None):
            raise RuntimeError("gagal")

        job_id = srv._start_channel_job(boom, "u", {})
        job = _drain(job_id)

        assert job["status"] == "error"
        assert job["error"] == "gagal"
        assert job["phase"] == "Gagal"

    def test_options_are_forwarded(self):
        seen = {}

        def capture(link, options, emitter=None):
            seen.update({"link": link, "options": options})
            return {"status": True, "result": {}}

        job_id = srv._start_channel_job(capture, "https://x/@ch", {"limit": 3})
        _drain(job_id)

        assert seen["link"] == "https://x/@ch"
        assert seen["options"] == {"limit": 3}

    def test_emitter_is_supplied_and_bound_to_the_job(self):
        def capture(link, options, emitter=None):
            emitter.emit("video:done", {"id": "v1", "ok": True})
            return {"status": True, "result": {"total": 1, "success": 1, "failed": 0,
                                               "items": []}}

        job_id = srv._start_channel_job(capture, "u", {})
        job = _drain(job_id)

        assert [i["videoId"] for i in job["items"]] == ["v1"]

    def test_distinct_jobs_get_distinct_ids(self):
        a = srv._start_channel_job(lambda *a, **k: {"status": True, "result": {}}, "u", {})
        b = srv._start_channel_job(lambda *a, **k: {"status": True, "result": {}}, "u", {})

        assert a != b


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


class TestIndex:
    def test_renders_the_template(self, client):
        assert client.get("/").status_code == 200

    def test_html_content_type(self, client):
        assert "text/html" in client.get("/").headers["Content-Type"]

    def test_page_contains_the_action_buttons(self, client):
        body = client.get("/").get_data(as_text=True)

        for act in ("info", "download", "channel-info", "channel-video", "channel-full"):
            assert f'data-act="{act}"' in body


# ---------------------------------------------------------------------------
# POST /api/info
# ---------------------------------------------------------------------------


class TestApiInfo:
    def test_missing_url_is_a_400(self, client):
        res = client.post("/api/info", json={})

        assert res.status_code == 400
        assert res.get_json()["status"] is False

    def test_blank_url_is_a_400(self, client):
        assert client.post("/api/info", json={"url": "   "}).status_code == 400

    def test_link_key_is_accepted_as_an_alias(self, client, monkeypatch):
        seen = {}
        monkeypatch.setattr("rch.youtube.thumbnail.thumbnail",
                            lambda link: seen.setdefault("link", link) or {"status": True})

        client.post("/api/info", json={"link": VIDEO_URL})

        assert seen["link"] == VIDEO_URL

    def test_returns_the_resolver_payload(self, client, monkeypatch):
        payload = {"status": True, "result": {"id": "v1", "thumbnails": {}}}
        monkeypatch.setattr("rch.youtube.thumbnail.thumbnail", lambda link: payload)

        res = client.post("/api/info", json={"url": VIDEO_URL})

        assert res.status_code == 200
        assert res.get_json() == payload

    def test_resolver_failure_is_still_200(self, client, monkeypatch):
        monkeypatch.setattr("rch.youtube.thumbnail.thumbnail",
                            lambda link: {"status": False, "message": "gagal"})

        res = client.post("/api/info", json={"url": VIDEO_URL})

        assert res.status_code == 200
        assert res.get_json()["status"] is False

    def test_resolver_exception_becomes_a_500(self, client, monkeypatch):
        def boom(_link):
            raise RuntimeError("jaringan mati")

        monkeypatch.setattr("rch.youtube.thumbnail.thumbnail", boom)

        res = client.post("/api/info", json={"url": VIDEO_URL})

        assert res.status_code == 500
        assert res.get_json() == {"status": False, "message": "jaringan mati"}

    def test_malformed_json_body_is_treated_as_empty(self, client):
        res = client.post("/api/info", data="not json",
                          content_type="application/json")

        assert res.status_code == 400

    def test_no_body_is_treated_as_empty(self, client):
        assert client.post("/api/info").status_code == 400


# ---------------------------------------------------------------------------
# POST /api/download
# ---------------------------------------------------------------------------


class TestApiDownload:
    @pytest.fixture(autouse=True)
    def _patch(self, monkeypatch):
        self.calls = []
        self.result = {"status": True, "result": {"path": "/tmp/v.mp4"}}

        def fake(link, options):
            self.calls.append((link, options))
            return self.result

        monkeypatch.setattr("rch.youtube.video.download", fake)

    def test_missing_url_is_a_400(self, client):
        assert client.post("/api/download", json={}).status_code == 400

    def test_success_is_a_200(self, client):
        res = client.post("/api/download", json={"url": VIDEO_URL})

        assert res.status_code == 200
        assert res.get_json()["result"]["path"] == "/tmp/v.mp4"

    def test_engine_failure_is_a_400(self, client):
        self.result = {"status": False, "message": "gagal"}

        res = client.post("/api/download", json={"url": VIDEO_URL})

        assert res.status_code == 400

    def test_defaults_are_applied(self, client):
        client.post("/api/download", json={"url": VIDEO_URL})

        _link, options = self.calls[0]
        assert options == {"format": "mp4", "quality": "720p", "outputDir": srv.DEFAULT_OUT}

    def test_body_fields_override_defaults(self, client):
        client.post("/api/download", json={
            "url": VIDEO_URL, "format": "mp3", "quality": "1080p", "outputDir": "/tmp/out",
        })

        _link, options = self.calls[0]
        assert options == {"format": "mp3", "quality": "1080p", "outputDir": "/tmp/out"}

    def test_blank_fields_fall_back_to_defaults(self, client):
        client.post("/api/download", json={
            "url": VIDEO_URL, "format": "", "quality": "", "outputDir": "",
        })

        _link, options = self.calls[0]
        assert options["format"] == "mp4"
        assert options["outputDir"] == srv.DEFAULT_OUT


# ---------------------------------------------------------------------------
# POST /api/channel-{info,video,full}
# ---------------------------------------------------------------------------


class TestChannelJobEndpoints:
    def test_channel_info_returns_a_job_id(self, client, monkeypatch):
        monkeypatch.setattr("rch.youtube.channel.channel_info",
                            lambda link, opts, emitter=None: {"status": True, "result": {}})

        res = client.post("/api/channel-info", json={"url": "https://x/@ch"})

        assert res.status_code == 200
        assert res.get_json()["status"] == "running"
        assert res.get_json()["jobId"] == 1

    def test_channel_video_returns_a_job_id(self, client, monkeypatch):
        monkeypatch.setattr("rch.youtube.channel.channel_video",
                            lambda link, opts, emitter=None: {"status": True, "result": {}})

        res = client.post("/api/channel-video", json={"url": "https://x/@ch"})

        assert res.get_json()["jobId"] >= 1

    def test_channel_full_returns_a_job_id(self, client, monkeypatch):
        monkeypatch.setattr("rch.youtube.channel.channel_full",
                            lambda link, opts, emitter=None: {"status": True, "result": {}})

        res = client.post("/api/channel-full", json={"url": "https://x/@ch"})

        assert res.get_json()["jobId"] >= 1

    def test_channel_info_defaults(self, client, monkeypatch):
        seen = {}
        monkeypatch.setattr("rch.youtube.channel.channel_info",
                            lambda link, opts, emitter=None: seen.setdefault("o", opts) or {})

        client.post("/api/channel-info", json={"url": "https://x/@ch"})

        assert seen["o"] == {"size": "hqdefault", "outputDir": srv.DEFAULT_OUT,
                             "limit": None, "shorts": False}

    def test_channel_info_body_overrides(self, client, monkeypatch):
        seen = {}
        monkeypatch.setattr("rch.youtube.channel.channel_info",
                            lambda link, opts, emitter=None: seen.setdefault("o", opts) or {})

        client.post("/api/channel-info", json={
            "url": "https://x/@ch", "size": "maxresdefault",
            "outputDir": "/tmp/out", "limit": 5, "shorts": True,
        })

        assert seen["o"] == {"size": "maxresdefault", "outputDir": "/tmp/out",
                             "limit": 5, "shorts": True}

    def test_channel_video_defaults(self, client, monkeypatch):
        seen = {}
        monkeypatch.setattr("rch.youtube.channel.channel_video",
                            lambda link, opts, emitter=None: seen.setdefault("o", opts) or {})

        client.post("/api/channel-video", json={"url": "https://x/@ch"})

        assert seen["o"] == {"quality": "720p", "outputDir": srv.DEFAULT_OUT,
                             "limit": None, "shorts": False}

    def test_channel_full_merges_size_and_quality(self, client, monkeypatch):
        seen = {}
        monkeypatch.setattr("rch.youtube.channel.channel_full",
                            lambda link, opts, emitter=None: seen.setdefault("o", opts) or {})

        client.post("/api/channel-full", json={
            "url": "https://x/@ch", "size": "sddefault", "quality": "1080p",
        })

        assert seen["o"] == {"size": "sddefault", "quality": "1080p",
                             "outputDir": srv.DEFAULT_OUT, "limit": None, "shorts": False}

    def test_jobs_do_not_share_state(self, client, monkeypatch):
        monkeypatch.setattr("rch.youtube.channel.channel_full",
                            lambda link, opts, emitter=None: {"status": True, "result": {
                                "total": 1, "success": 1, "failed": 0, "items": []}})

        first = client.post("/api/channel-full", json={"url": "https://x/@a"}).get_json()["jobId"]
        second = client.post("/api/channel-full", json={"url": "https://x/@b"}).get_json()["jobId"]

        _drain(first)
        _drain(second)

        assert first != second
        assert _job(client, first)["result"]["total"] == 1
        assert _job(client, second)["result"]["total"] == 1


# ---------------------------------------------------------------------------
# GET /api/status/<job_id>
# ---------------------------------------------------------------------------


class TestApiStatus:
    def test_unknown_job_is_a_404(self, client):
        res = client.get("/api/status/999")

        assert res.status_code == 404
        assert res.get_json()["error"] == "Job tidak ditemukan"

    def test_running_job_reports_progress_fields(self, client):
        release = threading.Event()

        def blocking(link, opts, emitter=None):
            release.wait(5)
            return {"status": True, "result": {}}

        job_id = srv._start_channel_job(blocking, "u", {})
        try:
            with srv._JOB_LOCK:
                snapshot = dict(srv._JOBS[job_id])
        finally:
            release.set()
        _drain(job_id)

        body = client.get(f"/api/status/{job_id}").get_json()

        assert snapshot["status"] == "running"
        assert snapshot["progress"] == 0
        assert snapshot["items"] == []
        assert set(body) >= {"status", "progress", "phase", "items", "result", "error"}

    def test_non_integer_job_id_is_a_404(self, client):
        assert client.get("/api/status/abc").status_code == 404

    def test_response_is_a_copy_not_the_live_record(self, client, monkeypatch):
        monkeypatch.setattr("rch.youtube.channel.channel_full",
                            lambda link, opts, emitter=None: {"status": True, "result": {}})
        job_id = client.post("/api/channel-full",
                             json={"url": "https://x/@ch"}).get_json()["jobId"]
        _drain(job_id)

        body = _job(client, job_id)
        body["status"] = "tampered"

        assert srv._JOBS[job_id]["status"] == "done"


def _block_forever():
    threading.Event().wait(5)
    return {"status": True, "result": {}}


# ---------------------------------------------------------------------------
# GET /api/history
# ---------------------------------------------------------------------------


class TestApiHistory:
    def _write(self, out, count=2):
        from rch.core.report import append_history

        for i in range(count):
            append_history(out, {"command": f"cmd{i}", "channel": "ch",
                                 "total": i, "success": i, "failed": 0})

    def test_empty_when_no_history_file(self, client, tmp_path):
        res = client.get(f"/api/history?out={tmp_path}")

        assert res.status_code == 200
        assert res.get_json() == []

    def test_records_are_returned_newest_first(self, client, tmp_path):
        self._write(tmp_path, 2)

        body = client.get(f"/api/history?out={tmp_path}").get_json()

        assert [r["command"] for r in body] == ["cmd1", "cmd0"]

    def test_out_query_parameter_selects_the_directory(self, client, tmp_path):
        self._write(tmp_path, 1)

        assert len(client.get(f"/api/history?out={tmp_path}").get_json()) == 1

    def test_blank_out_falls_back_to_default(self, client):
        assert client.get("/api/history?out=").status_code == 200

    def test_each_record_exposes_the_six_ui_columns(self, client, tmp_path):
        self._write(tmp_path, 1)

        record = client.get(f"/api/history?out={tmp_path}").get_json()[0]

        # ``items`` is additive: the six original columns still drive the table,
        # and items carries the per-video statuses behind the new column.
        assert {"timestamp", "command", "channel",
                "total", "success", "failed"} <= set(record)
        assert record["items"] == []

    def test_record_lists_the_statuses_of_the_videos_it_covered(self, client, tmp_path):
        from rch.core.report import append_history
        from rch.core.tracker import append_event

        append_history(tmp_path, {
            "command": "channel-video", "channel": "https://x/@ch",
            "total": 2, "success": 1, "failed": 1,
            "videoIds": ["dQw4w9WgXcQ", "jNQXAC9IVRw"],
        })
        append_event("dQw4w9WgXcQ", "clip", status="done", files=["c.mp4"])
        append_event("jNQXAC9IVRw", "download", status="done")

        record = client.get(f"/api/history?out={tmp_path}").get_json()[0]

        statuses = {i["videoId"]: i["status"] for i in record["items"]}
        assert statuses == {"dQw4w9WgXcQ": "clipped",
                            "jNQXAC9IVRw": "downloaded"}

    def test_reader_rejection_becomes_a_400_not_a_500(self, client, monkeypatch, tmp_path):
        """The reader validates its input, so a rejection must not surface as a crash."""
        import rch.core.report as report_mod

        def _reject(_out):
            raise TypeError("output_dir harus string atau PathLike")

        monkeypatch.setattr(report_mod, "read_history", _reject)

        res = client.get(f"/api/history?out={tmp_path}")

        assert res.status_code == 400
        assert res.get_json()["error"] == "Parameter out tidak valid"


# ---------------------------------------------------------------------------
# POST /api/quit
# ---------------------------------------------------------------------------


class TestApiQuit:
    def test_reports_stopping(self, client, monkeypatch):
        called = threading.Event()
        monkeypatch.setattr(srv, "_shutdown_werkzeug", called.set)

        res = client.post("/api/quit", json={})

        assert res.status_code == 200
        assert res.get_json() == {"status": "stopping"}
        for _ in range(200):
            if called.is_set():
                break
            threading.Event().wait(0.01)
        assert called.is_set()

    def test_sets_the_shutdown_event(self, client, monkeypatch):
        monkeypatch.setattr(srv, "_shutdown_werkzeug", lambda: None)

        client.post("/api/quit", json={})

        assert srv._SHUTDOWN.is_set()

    def test_shuts_down_the_live_server_not_a_default_one(self, client, monkeypatch):
        """``/api/quit`` must stop the server that is actually running."""
        class _FakeServer:
            def __init__(self):
                self.stopped = False

            def shutdown(self):
                self.stopped = True

        live = _FakeServer()
        monkeypatch.setattr(srv, "_LIVE_SERVER", live, raising=False)

        client.post("/api/quit", json={})
        for _ in range(200):
            if live.stopped:
                break
            threading.Event().wait(0.01)

        assert live.stopped is True


# ---------------------------------------------------------------------------
# create_server / run_server / _shutdown_werkzeug
# ---------------------------------------------------------------------------


class TestServerLifecycle:
    def test_create_server_uses_the_requested_host_and_port(self, monkeypatch):
        seen = {}
        monkeypatch.setattr("werkzeug.serving.make_server",
                            lambda host, port, app: seen.update(h=host, p=port) or "srv")

        assert srv.create_server("0.0.0.0", 1234) == "srv"
        assert seen == {"h": "0.0.0.0", "p": 1234}

    def test_create_server_defaults_to_loopback(self, monkeypatch):
        seen = {}
        monkeypatch.setattr("werkzeug.serving.make_server",
                            lambda host, port, app: seen.update(h=host, p=port) or "srv")

        srv.create_server()

        assert seen == {"h": "127.0.0.1", "p": 8787}

    def test_run_server_serves_forever_then_closes(self, monkeypatch, capsys):
        class _FakeServer:
            def __init__(self):
                self.closed = False

            def serve_forever(self):
                return None

            def server_close(self):
                self.closed = True

        fake = _FakeServer()
        monkeypatch.setattr("werkzeug.serving.make_server", lambda *a: fake)

        srv.run_server("127.0.0.1", 8787, open_browser=False)

        assert fake.closed is True
        assert "http://127.0.0.1:8787" in capsys.readouterr().out

    def test_run_server_opens_the_browser_when_asked(self, monkeypatch):
        opened = []
        monkeypatch.setattr("werkzeug.serving.make_server",
                            lambda *a: type("S", (), {"serve_forever": lambda s: None,
                                                     "server_close": lambda s: None})())
        monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))

        srv.run_server("127.0.0.1", 8787, open_browser=True)
        for _ in range(200):
            if opened:
                break
            threading.Event().wait(0.01)

        assert opened == ["http://127.0.0.1:8787"]

    def test_run_server_does_not_open_the_browser_when_disabled(self, monkeypatch):
        opened = []
        monkeypatch.setattr("werkzeug.serving.make_server",
                            lambda *a: type("S", (), {"serve_forever": lambda s: None,
                                                     "server_close": lambda s: None})())
        monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))

        srv.run_server("127.0.0.1", 8787, open_browser=False)
        threading.Event().wait(0.1)

        assert opened == []

    def test_keyboard_interrupt_is_swallowed(self, monkeypatch, capsys):
        class _Interrupting:
            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                return None

        monkeypatch.setattr("werkzeug.serving.make_server", lambda *a: _Interrupting())

        srv.run_server("127.0.0.1", 8787, open_browser=False)

        assert "Server dihentikan." in capsys.readouterr().out

    def test_shutdown_without_a_live_server_is_a_noop(self, monkeypatch):
        monkeypatch.setattr(srv, "_LIVE_SERVER", None, raising=False)
        monkeypatch.setattr("werkzeug.serving.make_server",
                            lambda *a: pytest.fail("must not build a second server"))

        srv._shutdown_werkzeug()

    def test_shutdown_swallows_server_errors(self, monkeypatch):
        class _Angry:
            def shutdown(self):
                raise RuntimeError("socket sudah tutup")

        monkeypatch.setattr(srv, "_LIVE_SERVER", _Angry(), raising=False)

        srv._shutdown_werkzeug()

    def test_default_bind_is_loopback_only(self):
        assert srv.DEFAULT_HOST == "127.0.0.1"

    def test_index_is_the_only_non_api_route(self, client):
        assert client.get("/api/nope").status_code == 404


# ---------------------------------------------------------------------------
# Response-shape contracts the frontend depends on
# ---------------------------------------------------------------------------


class TestFrontendContract:
    def test_status_payload_keys_match_the_poll_loop(self, client, monkeypatch):
        monkeypatch.setattr("rch.youtube.channel.channel_full",
                            lambda link, opts, emitter=None: {"status": True, "result": {
                                "total": 2, "success": 2, "failed": 0,
                                "zipPath": "/tmp/x.zip", "items": [],
                            }})
        job_id = client.post("/api/channel-full",
                             json={"url": "https://x/@ch"}).get_json()["jobId"]
        _drain(job_id)

        body = _job(client, job_id)

        assert set(body) >= {"status", "progress", "phase", "items", "result", "error"}
        assert set(body["result"]) >= {"total", "success", "failed", "zipPath", "items"}

    def test_row_shape_matches_render_rows(self, client, monkeypatch):
        """app.js reads it.ok / it.videoId / it.title / it.error on every row."""
        monkeypatch.setattr("rch.youtube.channel.channel_full",
                            lambda link, opts, emitter=None: {"status": True, "result": {
                                "total": 1, "success": 1, "failed": 0,
                                "items": [{"videoId": "v1", "title": "T", "videoOk": True,
                                           "thumbOk": True, "unavailable": False,
                                           "error": None}],
                            }})
        job_id = client.post("/api/channel-full",
                             json={"url": "https://x/@ch"}).get_json()["jobId"]
        _drain(job_id)

        row = _job(client, job_id)["result"]["items"][0]

        assert set(row) == {"ok", "videoId", "title", "error", "status"}
        assert row["ok"] is True, "app.js would label a successful channel-full row GAGAL"

    def test_responses_are_json(self, client):
        res = client.post("/api/download", json={})

        assert res.headers["Content-Type"].startswith("application/json")

    def test_error_payload_never_leaks_a_traceback(self, client, monkeypatch):
        def boom(_link):
            raise RuntimeError("boom")

        monkeypatch.setattr("rch.youtube.thumbnail.thumbnail", boom)
        body = client.post("/api/info", json={"url": VIDEO_URL}).get_data(as_text=True)

        assert "Traceback" not in body
        assert "File \"" not in body