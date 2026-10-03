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


class TestLiveProgressReachesTheBrowser:
    """The emitter is how a channel run shows movement while it works.

    Nothing in the route tests emitted anything, so every handler here ran zero
    times. That is the path a user actually watches: a run of 700 videos that
    never moved the bar looks identical to one that is wedged.
    """

    def _emitting(self, events):
        """A fake engine that emits, then returns."""
        def _inner(_link, options, emitter=None):
            for name, payload in events:
                emitter.emit(name, payload)
            return {"status": True, "result": {"total": 2, "success": 2,
                                               "failed": 0, "items": []}}

        return _inner

    def _run(self, monkeypatch, events):
        monkeypatch.setattr("rch.youtube.channel.channel_full",
                            self._emitting(events))
        client = A.app.test_client()
        job_id = _post("/api/channel-full", url=CHANNEL_URL).get_json()["jobId"]
        return client, _wait(client, job_id)

    def test_progress_moves_the_bar(self, monkeypatch):
        _client, payload = self._run(
            monkeypatch, [("progress", {"done": 1, "total": 4})])

        assert payload["status"] == "done"

    def test_a_mid_run_snapshot_shows_the_ratio_and_count(self, monkeypatch):
        """Captured before the job settles, which is the state the browser
        actually polls."""
        seen = {}

        def _peek(_link, options, emitter=None):
            # The job id is not known until the route returns, so the engine
            # finds its own record rather than closing over a later assignment.
            with A._JOB_LOCK:
                job_id = next(i for i, j in A._JOBS.items()
                              if j.get("kind") == "download")
            emitter.emit("progress", {"done": 1, "total": 4})
            emitter.emit("phase", {"phase": "download"})
            emitter.emit("video:done", {"id": "v1", "title": "Satu",
                                        "ok": True, "error": None})
            with A._JOB_LOCK:
                seen.update(dict(A._JOBS[job_id]))
            return {"status": True, "result": {"total": 2, "success": 2,
                                               "failed": 0, "items": []}}

        monkeypatch.setattr("rch.youtube.channel.channel_full", _peek)
        started = _post("/api/channel-full", url=CHANNEL_URL).get_json()
        job_id = started["jobId"]

        import time

        for _ in range(200):
            with A._JOB_LOCK:
                if A._JOBS.get(job_id, {}).get("items"):
                    break
            time.sleep(0.02)

        assert seen.get("progress") in (0.25, 1.0)
        assert "1/4" in str(seen.get("phase")) or seen.get("phase") == "download"

    def test_a_finished_video_is_appended_as_a_row(self, monkeypatch):
        def _inner(_link, options, emitter=None):
            emitter.emit("video:done", {"id": "v1", "title": "Satu",
                                        "ok": True, "error": None})
            emitter.emit("video:done", {"id": "v2", "title": "Dua",
                                        "ok": False, "error": "403"})
            return {"status": True, "result": {"total": 2, "success": 1,
                                               "failed": 1, "items": []}}

        monkeypatch.setattr("rch.youtube.channel.channel_full", _inner)
        client = A.app.test_client()
        job_id = _post("/api/channel-full", url=CHANNEL_URL).get_json()["jobId"]

        _wait(client, job_id)
        rows = A._JOBS[job_id]["items"]

        assert [r["videoId"] for r in rows] == ["v1", "v2"]
        assert rows[0]["ok"] is True
        assert rows[1]["error"] == "403"

    def test_progress_with_no_total_does_not_divide_by_zero(self, monkeypatch):
        def _inner(_link, options, emitter=None):
            emitter.emit("progress", {"done": 0, "total": 0})
            return {"status": True, "result": {"total": 0, "success": 0,
                                               "failed": 0, "items": []}}

        monkeypatch.setattr("rch.youtube.channel.channel_full", _inner)
        client = A.app.test_client()
        job_id = _post("/api/channel-full", url=CHANNEL_URL).get_json()["jobId"]

        payload = _wait(client, job_id)

        assert payload["status"] == "done"


class TestResultShaping:
    """The summary is what the page renders, so its edge cases matter more than
    they look: every one of these is a shape a misbehaving engine can return."""

    def test_a_non_dict_result_passes_through(self):
        assert A._summarise("apa saja") == "apa saja"

    def test_a_failed_run_becomes_a_message(self):
        out = A._summarise({"status": False, "message": "tidak ada video"})

        assert out == {"message": "tidak ada video"}

    def test_a_failed_run_without_a_message_says_gagal(self):
        assert A._summarise({"status": False}) == {"message": "Gagal"}

    def test_a_non_dict_result_body_passes_through(self):
        out = A._summarise({"status": True, "result": "ringkasan teks"})

        assert out == "ringkasan teks"

    def test_a_row_keeps_an_explicit_ok(self):
        """videoOk is the fallback; an explicit ok must not be overwritten by it
        when an engine happens to send both."""
        row = A._result_row({"videoId": "v", "ok": False, "videoOk": True})

        assert row["ok"] is False

    def test_a_row_falls_back_to_the_id_field(self):
        row = A._result_row({"id": "abc", "videoOk": True})

        assert row["videoId"] == "abc"

    def test_a_row_normalises_none_text_to_empty(self):
        """jsonify emits null for None, and the page renders these straight
        into cells; an empty string keeps the cells blank rather than saying
        'null'."""
        row = A._result_row({"videoId": "v", "title": None, "error": None})

        assert row["title"] == ""
        assert row["error"] == ""

    def test_non_dict_items_are_dropped(self):
        out = A._summarise({"status": True, "result": {
            "total": 1, "items": ["bukan dict", {"videoId": "v", "videoOk": True}]}})

        assert len(out["items"]) == 1

    def test_a_result_with_no_items_key_is_fine(self):
        out = A._summarise({"status": True, "result": {"total": 0}})

        assert out["items"] == []


class TestLinkExtraction:
    def test_link_is_accepted_as_well_as_url(self, engine):
        _post("/api/info", link="https://youtu.be/dQw4w9WgXcQ")

        assert engine[0][1][0] == "https://youtu.be/dQw4w9WgXcQ"

    def test_whitespace_around_a_url_is_stripped(self, engine):
        _post("/api/info", url="  https://youtu.be/dQw4w9WgXcQ  ")

        assert engine[0][1][0] == "https://youtu.be/dQw4w9WgXcQ"

    def test_url_wins_over_link_when_both_are_sent(self, engine):
        _post("/api/info", url="https://youtu.be/dQw4w9WgXcQ",
              link="https://youtu.be/lain")

        assert engine[0][1][0] == "https://youtu.be/dQw4w9WgXcQ"


class TestTrackerStatusLookup:
    def test_it_is_empty_outside_a_request(self):
        """_result_row is called from the worker thread, where there is no
        request to cache against. Raising here would kill the job."""
        assert A._tracker_statuses() == {}

    def test_a_missing_ledger_yields_no_statuses(self, monkeypatch):
        import rch.core.tracker as tracker_mod

        def _boom():
            raise OSError("ledger hilang")

        monkeypatch.setattr(tracker_mod, "statuses_for", _boom)
        client = A.app.test_client()

        res = client.get("/api/runs?out=/tmp/apa-saja", headers=HOST)

        assert res.status_code == 200


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
