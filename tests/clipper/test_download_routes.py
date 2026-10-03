"""Tests for the download routes living in the clipper app.

These endpoints used to live in a second Flask app with its own job registry and
its own ``/api/status``. They are served by ``clipper.app`` now, so a single
process and a single registry cover both halves of the tool.

The one route that could not simply move is ``/api/history``: the harvester reads
it as *run* history and the clipper already reads it as *clip* history. Two
different meanings cannot share a path, so run history is served from
``/api/runs`` and ``/api/history`` keeps meaning clips.

The engine calls are stubbed throughout. These are route tests - what they owe is
validation, job registration and the response envelope, not the harvest itself.
"""
from __future__ import annotations

import pytest

from clipper import app as A

HOST = {"Host": "127.0.0.1:8787"}
CHANNEL_URL = "https://www.youtube.com/@contoh/videos"


@pytest.fixture(autouse=True)
def _registry(monkeypatch, tmp_path):
    A.app.config["TESTING"] = True
    monkeypatch.setattr(A, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(A, "OUTPUT_DIR", tmp_path / "clips")
    monkeypatch.setattr(A, "_JOBS", {})
    monkeypatch.setattr(A, "_JOB_COUNTER", 0)


@pytest.fixture()
def engine(monkeypatch):
    """Stub every engine entry point so no route reaches the network."""
    calls = []

    def _fake(name, result):
        def _inner(*args, **kwargs):
            calls.append((name, args, kwargs))
            return result

        return _inner

    monkeypatch.setattr("rch.youtube.channel.channel_info",
                        _fake("channel_info", {"status": True, "result": {
                            "total": 2, "success": 2, "failed": 0, "items": []}}))
    monkeypatch.setattr("rch.youtube.channel.channel_video",
                        _fake("channel_video", {"status": True, "result": {
                            "total": 2, "success": 1, "failed": 1, "items": []}}))
    monkeypatch.setattr("rch.youtube.channel.channel_full",
                        _fake("channel_full", {"status": True, "result": {
                            "total": 2, "success": 2, "failed": 0, "items": [],
                            "zipPath": "out.zip"}}))
    monkeypatch.setattr("rch.youtube.video.download",
                        _fake("download", {"status": True, "result": {}}))
    monkeypatch.setattr("rch.youtube.playlist.playlist_metadata",
                        _fake("playlist", {"status": True, "result": {"items": []}}))
    monkeypatch.setattr("rch.youtube.thumbnail.thumbnail",
                        _fake("thumbnail", {"status": True, "result": {
                            "id": "dQw4w9WgXcQ", "title": "Judul"}}))
    return calls


def _post(path, **body):
    return A.app.test_client().post(path, json=body, headers=HOST)


def _wait(client, job_id, tries=200):
    import time

    for _ in range(tries):
        payload = client.get(f"/api/status/{job_id}", headers=HOST).get_json()
        if payload.get("status") != "running":
            return payload
        time.sleep(0.02)
    raise AssertionError("job tidak selesai")


class TestChannelRoutes:
    @pytest.mark.parametrize("route", [
        "/api/channel-info", "/api/channel-video", "/api/channel-full",
    ])
    def test_it_starts_a_job(self, route, engine):
        body = _post(route, url=CHANNEL_URL).get_json()

        assert body["status"] == "running"
        assert body["jobId"] in A._JOBS

    @pytest.mark.parametrize("route", [
        "/api/channel-info", "/api/channel-video", "/api/channel-full",
    ])
    def test_a_missing_url_is_400(self, route, engine):
        assert _post(route).status_code == 400

    @pytest.mark.parametrize("route", [
        "/api/channel-info", "/api/channel-video", "/api/channel-full",
    ])
    def test_a_blank_url_is_400(self, route, engine):
        assert _post(route, url="   ").status_code == 400

    def test_the_job_is_registered_as_a_download(self, engine):
        job_id = _post("/api/channel-full", url=CHANNEL_URL).get_json()["jobId"]

        assert A._JOBS[job_id]["kind"] == "download"
        assert A._JOBS[job_id]["command"] == "channel-full"

    def test_the_engine_receives_the_url_and_options(self, engine):
        _post("/api/channel-full", url=CHANNEL_URL, quality="1080p", limit=5)

        _wait(A.app.test_client(),
              _post("/api/channel-full", url=CHANNEL_URL).get_json()["jobId"])
        name, args, _kwargs = engine[0]
        assert name == "channel_full"
        assert args[0] == CHANNEL_URL
        assert args[1]["quality"] == "1080p"

    def test_output_dir_defaults_to_the_downloads_folder(self, engine):
        _post("/api/channel-info", url=CHANNEL_URL)
        client = A.app.test_client()
        _wait(client, _post("/api/channel-info", url=CHANNEL_URL).get_json()["jobId"])

        assert engine[0][1][1]["outputDir"] == A.DEFAULT_OUT

    def test_a_finished_job_reports_its_summary(self, engine):
        client = A.app.test_client()
        job_id = _post("/api/channel-full", url=CHANNEL_URL).get_json()["jobId"]

        payload = _wait(client, job_id)

        assert payload["status"] == "done"
        assert payload["result"]["total"] == 2

    def test_an_engine_error_becomes_an_error_job(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("jaringan mati")

        monkeypatch.setattr("rch.youtube.channel.channel_full", _boom)
        client = A.app.test_client()
        job_id = _post("/api/channel-full", url=CHANNEL_URL).get_json()["jobId"]

        payload = _wait(client, job_id)

        assert payload["status"] == "error"
        assert "jaringan mati" in payload["error"]


class TestIdSpaceIsShared:
    def test_a_channel_job_does_not_collide_with_a_clip_job(self, engine):
        from clipper.progress import Job

        clip_id = Job(A._next_job_id(), {"url": CHANNEL_URL}).id
        channel_id = _post("/api/channel-info", url=CHANNEL_URL).get_json()["jobId"]

        assert clip_id != channel_id

    def test_both_kinds_come_back_from_the_same_status_route(self, engine):
        from clipper.progress import Job

        client = A.app.test_client()
        clip = Job(A._next_job_id(), {"url": CHANNEL_URL})
        with A._JOB_LOCK:
            A._JOBS[clip.id] = clip
        channel_id = _post("/api/channel-info", url=CHANNEL_URL).get_json()["jobId"]

        assert client.get(f"/api/status/{clip.id}",
                          headers=HOST).get_json()["kind"] == "clip"
        assert client.get(f"/api/status/{channel_id}",
                          headers=HOST).get_json()["kind"] == "download"


class TestSingleItemRoutes:
    def test_info_returns_the_preview(self, engine):
        body = _post("/api/info", url="https://youtu.be/dQw4w9WgXcQ").get_json()

        assert body["status"] is True
        assert body["result"]["id"] == "dQw4w9WgXcQ"

    def test_info_without_a_url_is_400(self, engine):
        assert _post("/api/info").status_code == 400

    def test_info_failure_is_500_not_a_crash(self, monkeypatch):
        monkeypatch.setattr("rch.youtube.thumbnail.thumbnail",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        res = _post("/api/info", url="https://youtu.be/dQw4w9WgXcQ")

        assert res.status_code == 500
        assert res.get_json()["status"] is False

    def test_download_passes_format_and_quality(self, engine):
        _post("/api/download", url="https://youtu.be/dQw4w9WgXcQ",
              format="mp3", quality="128kbps")

        name, args, _kwargs = engine[0]
        assert name == "download"
        # The options arrive as the second positional argument.
        assert args[1]["format"] == "mp3"
        assert args[1]["quality"] == "128kbps"

    def test_download_without_a_url_is_400(self, engine):
        assert _post("/api/download").status_code == 400

    def test_download_reports_failure_with_400(self, monkeypatch):
        monkeypatch.setattr("rch.youtube.video.download",
                            lambda *a, **k: {"status": False, "message": "404"})
        res = _post("/api/download", url="https://youtu.be/dQw4w9WgXcQ")

        assert res.status_code == 400

    def test_playlist_returns_metadata(self, engine):
        res = _post("/api/playlist", url=CHANNEL_URL, limit=3)

        assert res.status_code == 200
        assert engine[0][0] == "playlist"

    def test_playlist_without_a_url_is_400(self, engine):
        assert _post("/api/playlist").status_code == 400


class TestTwoKindsOfHistory:
    def test_runs_serves_the_harvest_history(self, tmp_path):
        from rch.core.report import append_history

        out = tmp_path / "downloads"
        append_history(out, {"command": "channel-full", "channel": CHANNEL_URL,
                             "total": 5, "success": 5, "failed": 0})

        res = A.app.test_client().get(f"/api/runs?out={out}", headers=HOST)
        body = res.get_json()

        assert res.status_code == 200
        assert body[0]["command"] == "channel-full"
        assert body[0]["channel"] == CHANNEL_URL

    def test_history_still_means_clips(self, engine):
        """The clipper already owned this path. Run history moved to /api/runs
        precisely so this one could keep its meaning."""
        res = A.app.test_client().get("/api/history", headers=HOST)

        assert res.status_code == 200
        assert res.get_json() == []

    def test_a_rejecting_reader_becomes_400_not_a_500(self, monkeypatch, tmp_path):
        """The reader validates its own input, so a rejection has to reach the
        caller as a 400 rather than a stack trace."""
        import rch.core.report as report_mod

        def _reject(_out):
            raise TypeError("output_dir harus string atau PathLike")

        monkeypatch.setattr(report_mod, "read_history", _reject)

        res = A.app.test_client().get(f"/api/runs?out={tmp_path}", headers=HOST)

        assert res.status_code == 400
        assert res.get_json()["error"] == "Parameter out tidak valid"

    def test_runs_carries_the_per_video_status(self, tmp_path):
        from rch.core.report import append_history

        out = tmp_path / "downloads"
        append_history(out, {"command": "channel-full", "channel": CHANNEL_URL,
                             "total": 1, "success": 1, "failed": 0,
                             "videoIds": ["dQw4w9WgXcQ"]})

        body = A.app.test_client().get(f"/api/runs?out={out}",
                                       headers=HOST).get_json()

        assert body[0]["items"] == [{"videoId": "dQw4w9WgXcQ", "status": None}]


class TestThePageCanActuallyRenderThePayload:
    """The payload and the page are separate pieces that have to agree.

    These came from the retired app's own suite. Nothing here checks that the
    numbers are right - it checks that download.js has the fields it reads, and
    that a row whose engine result spells success ``videoOk`` is not rendered as
    a failure.
    """

    def test_the_status_payload_has_what_the_poll_loop_reads(self, engine):
        client = A.app.test_client()
        job_id = _post("/api/channel-full", url=CHANNEL_URL).get_json()["jobId"]

        body = _wait(client, job_id)

        assert set(body) >= {"status", "progress", "phase", "items", "result",
                             "error"}
        assert set(body["result"]) >= {"total", "success", "failed", "zipPath",
                                       "items"}

    def test_a_video_ok_row_is_not_rendered_as_a_failure(self, monkeypatch):
        """channel-full items spell success ``videoOk``; the page reads ``ok``.
        Without the rename every successful channel-full row would be labelled
        GAGAL, which is the kind of bug a passing unit test never catches."""
        monkeypatch.setattr("rch.youtube.channel.channel_full",
                            lambda link, opts, emitter=None: {
                                "status": True, "result": {
                                    "total": 1, "success": 1, "failed": 0,
                                    "items": [{"videoId": "v1", "title": "T",
                                               "videoOk": True, "thumbOk": True,
                                               "unavailable": False,
                                               "error": None}]}})
        client = A.app.test_client()
        job_id = _post("/api/channel-full", url=CHANNEL_URL).get_json()["jobId"]

        row = _wait(client, job_id)["result"]["items"][0]

        assert set(row) >= {"ok", "videoId", "title", "error"}
        assert row["ok"] is True

    def test_the_row_carries_exactly_what_the_page_reads(self, monkeypatch):
        """An extra key here would be noise; a missing one is a blank cell."""
        monkeypatch.setattr("rch.youtube.channel.channel_full",
                            lambda link, opts, emitter=None: {
                                "status": True, "result": {
                                    "total": 1, "success": 1, "failed": 0,
                                    "items": [{"videoId": "v1", "title": "T",
                                               "videoOk": True}]}})
        client = A.app.test_client()
        job_id = _post("/api/channel-full", url=CHANNEL_URL).get_json()["jobId"]

        row = _wait(client, job_id)["result"]["items"][0]

        assert set(row) == {"ok", "videoId", "title", "error"}

    def test_an_engine_error_never_leaks_a_traceback(self, monkeypatch):
        """The page renders the message straight into the DOM, so a traceback
        would hand a local user the server's file paths."""
        def _boom(_link):
            raise RuntimeError("boom")

        monkeypatch.setattr("rch.youtube.thumbnail.thumbnail", _boom)

        body = _post("/api/info",
                     url="https://youtu.be/dQw4w9WgXcQ").get_data(as_text=True)

        assert "Traceback" not in body
        assert 'File "' not in body

    def test_a_channel_job_error_never_leaks_a_traceback(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr("rch.youtube.channel.channel_full", _boom)
        client = A.app.test_client()
        job_id = _post("/api/channel-full", url=CHANNEL_URL).get_json()["jobId"]

        payload = _wait(client, job_id)

        assert "Traceback" not in str(payload)
        assert 'File "' not in str(payload)


class TestNothingIsLostInTheMove:
    def test_every_former_route_is_served_here(self):
        """Each of these used to be served by the retired app. One of them had to
        change name because /api/history was already taken."""
        rules = {rule.rule for rule in A.app.url_map.iter_rules()}

        for route in ("/api/info", "/api/download", "/api/playlist",
                      "/api/channel-info", "/api/channel-video",
                      "/api/channel-full", "/api/status/<int:job_id>",
                      "/api/runs", "/api/clip", "/api/videos"):
            assert route in rules

    def test_the_old_run_history_path_is_not_silently_reused(self):
        """/api/runs must not be an alias of /api/history, or the clipper's
        clip history would start answering harvest queries."""
        rules = {rule.rule: rule for rule in A.app.url_map.iter_rules()}

        assert rules["/api/history"].endpoint != rules["/api/runs"].endpoint
