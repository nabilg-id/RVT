"""Tests for :mod:`clipper.app` — the Flask web layer.

The heavy pipeline (moviepy, Whisper, mediapipe) is stubbed out; these tests
cover the parts that are new in the merge: input validation, the cross-origin
guard, the job lifecycle, and the history file. They must run without the
multi-gigabyte media stack installed.
"""
from __future__ import annotations

import sys
import time
import types

import pytest

from clipper import app as A
from clipper.styles.caption_styles import CAPTION_STYLES

HOST = {"Host": "127.0.0.1:8787"}
VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


class FakeProcessor:
    """Berperilaku seperti VideoProcessor tanpa dependensi media."""

    def __init__(self, caption_style="clean_white"):
        self.caption_style = caption_style
        self.caption_maker = types.SimpleNamespace(
            styles=CAPTION_STYLES, selected_style=caption_style
        )

    def process_video(self, url, num_clips, min_dur, max_dur):
        print("📥 Downloading video...")
        print("🎵 Starting transcription with Whisper...")
        print("🧠 Using AI to select the most viral clips...")
        print(f"🎬 Processing {num_clips} viral clips...")
        for i in range(1, num_clips + 1):
            print(f"📹 Clip {i}/{num_clips}: Momen {i}")
            print("✅ Video encoding complete")
        print("🎉 VIRAL CLIPS GENERATED!")
        print("📹 Source: 'Video Uji'")
        print("📋 Generated Clips:")
        for i in range(1, num_clips + 1):
            print(f"  - clip_{i}_88pts_v.mp4 (1.20 MB)")
        return [f"clips/clip_{i}_88pts_v.mp4" for i in range(1, num_clips + 1)], "Video Uji"


class BoomProcessor(FakeProcessor):
    def process_video(self, *a, **k):
        raise RuntimeError("pipeline gagal total")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    A.app.config["TESTING"] = True
    monkeypatch.setattr(A, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(A, "OUTPUT_DIR", tmp_path / "clips")
    with A.app.test_client() as c:
        yield c
    with A._JOB_LOCK:
        A._JOBS.clear()


@pytest.fixture()
def stub_processor(monkeypatch):
    mod = types.ModuleType("clipper.services.video_processor")
    mod.VideoProcessor = FakeProcessor
    monkeypatch.setitem(sys.modules, "clipper.services.video_processor", mod)
    return mod


def _wait(client, job_id, tries=100):
    for _ in range(tries):
        payload = client.get(f"/api/status/{job_id}").get_json()
        if payload.get("status") != "running":
            return payload
        time.sleep(0.02)
    return client.get(f"/api/status/{job_id}").get_json()


class TestStaticAndIndex:
    def test_index_renders(self, client):
        assert client.get("/").status_code == 200

    def test_index_offers_every_caption_style(self, client):
        body = client.get("/").get_data(as_text=True)
        for key in CAPTION_STYLES:
            assert f'value="{key}"' in body

    def test_styles_endpoint_lists_all(self, client):
        payload = client.get("/api/styles").get_json()
        assert [s["key"] for s in payload] == list(CAPTION_STYLES)

    def test_static_assets_served(self, client):
        assert client.get("/static/style.css").status_code == 200
        assert client.get("/static/app.js").status_code == 200


class TestClipDownload:
    """The results table links to /clips/<name>, so that route is the only way
    a finished clip reaches the user. Nothing exercised it, which is how the
    link path drifted from the route unnoticed."""

    def test_serves_a_finished_clip(self, client):
        A.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (A.OUTPUT_DIR / "clip_1_88pts_v.mp4").write_bytes(b"\x00" * 2048)

        r = client.get("/clips/clip_1_88pts_v.mp4")
        assert r.status_code == 200
        assert len(r.data) == 2048

    def test_missing_clip_is_404(self, client):
        A.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        assert client.get("/clips/tidak_ada.mp4").status_code == 404

    @pytest.mark.parametrize(
        "target",
        ["../secret.txt", "..%2fsecret.txt", "..\\secret.txt"],
    )
    def test_traversal_outside_output_dir_is_refused(self, client, tmp_path, target):
        (tmp_path / "secret.txt").write_text("rahasia")
        A.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        r = client.get(f"/clips/{target}")
        assert r.status_code == 404
        assert b"rahasia" not in r.data

    def test_app_js_links_to_this_route(self, client):
        # Guards against the URL drifting apart again: the template builds
        # hrefs client-side, so the string lives only in app.js.
        body = client.get("/static/app.js").get_data(as_text=True)
        assert "/clips/" in body


class TestClipRequestValidation:
    def test_missing_url_is_400(self, client):
        r = client.post("/api/clip", json={})
        assert r.status_code == 400
        assert "URL" in r.get_json()["error"]

    def test_blank_url_is_400(self, client):
        assert client.post("/api/clip", json={"url": "   "}).status_code == 400

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/watch?v=abc",
            "ftp://youtube.com/watch?v=abc",
            "not-a-url",
            "javascript:alert(1)",
        ],
    )
    def test_non_youtube_url_is_400(self, client, url):
        r = client.post("/api/clip", json={"url": url})
        assert r.status_code == 400
        assert "YouTube" in r.get_json()["error"]

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=abc",
            "https://youtu.be/abc",
            "http://m.youtube.com/watch?v=abc",
        ],
    )
    def test_valid_youtube_urls_pass_validation(self, client, url):
        assert client.post("/api/clip", json={"url": url}).status_code == 200

    def test_unknown_style_is_400(self, client):
        r = client.post("/api/clip", json={"url": VIDEO_URL, "style": "nope"})
        assert r.status_code == 400
        assert "caption" in r.get_json()["error"].lower()

    def test_min_greater_than_max_is_400(self, client):
        r = client.post("/api/clip", json={"url": VIDEO_URL, "minDur": 90, "maxDur": 20})
        assert r.status_code == 400

    def test_min_equal_to_max_is_400(self, client):
        r = client.post("/api/clip", json={"url": VIDEO_URL, "minDur": 30, "maxDur": 30})
        assert r.status_code == 400

    @pytest.mark.parametrize("field", ["numClips", "minDur", "maxDur"])
    def test_non_numeric_input_falls_back_to_default(self, client, field):
        r = client.post("/api/clip", json={"url": VIDEO_URL, field: "abc"})
        assert r.status_code == 200

    def test_out_of_range_num_clips_is_clamped(self, client):
        assert client.post("/api/clip", json={"url": VIDEO_URL, "numClips": 9999}).status_code == 200
        assert client.post("/api/clip", json={"url": VIDEO_URL, "numClips": -5}).status_code == 200

    def test_empty_body_still_returns_400(self, client):
        assert client.post("/api/clip").status_code == 400


class TestCrossOriginGuard:
    def test_no_origin_allowed(self, client):
        assert client.post("/api/clip", json={}, headers=HOST).status_code == 400

    def test_same_origin_allowed(self, client):
        r = client.post("/api/clip", json={},
                        headers={**HOST, "Origin": "http://127.0.0.1:8787"})
        assert r.status_code != 403

    def test_foreign_origin_is_403(self, client):
        r = client.post("/api/clip", json={},
                        headers={**HOST, "Origin": "http://evil.example"})
        assert r.status_code == 403
        assert "Cross-origin" in r.get_json()["error"]

    def test_null_origin_is_403(self, client):
        r = client.post("/api/clip", json={}, headers={**HOST, "Origin": "null"})
        assert r.status_code == 403

    def test_different_port_is_403(self, client):
        r = client.post("/api/clip", json={},
                        headers={**HOST, "Origin": "http://127.0.0.1:9999"})
        assert r.status_code == 403

    def test_preview_is_guarded_too(self, client):
        r = client.post("/api/preview", json={},
                        headers={**HOST, "Origin": "http://evil.example"})
        assert r.status_code == 403

    def test_quit_is_guarded_too(self, client):
        r = client.post("/api/quit", json={},
                        headers={**HOST, "Origin": "http://evil.example"})
        assert r.status_code == 403

    def test_reads_are_not_guarded(self, client):
        assert client.get("/api/styles",
                          headers={"Origin": "http://evil.example"}).status_code == 200

    def test_lan_origin_is_allowed(self, client):
        r = client.post("/api/clip", json={},
                        headers={"Host": "192.168.1.20:8787",
                                 "Origin": "http://192.168.1.20:8787"})
        assert r.status_code != 403


class TestJobLifecycle:
    def test_job_starts_and_completes(self, client, stub_processor):
        job_id = client.post("/api/clip", json={"url": VIDEO_URL}).get_json()["jobId"]
        payload = _wait(client, job_id)

        assert payload["status"] == "done"
        assert payload["progress"] == 1.0
        assert payload["error"] is None

    def test_job_captures_pipeline_log(self, client, stub_processor):
        job_id = client.post("/api/clip", json={"url": VIDEO_URL}).get_json()["jobId"]
        log = _wait(client, job_id)["log"]
        assert any("Downloading video" in ln for ln in log)
        assert any("transcription" in ln.lower() for ln in log)

    def test_job_collects_output_names(self, client, stub_processor):
        job_id = client.post("/api/clip", json={"url": VIDEO_URL, "numClips": 2}).get_json()["jobId"]
        payload = _wait(client, job_id)
        assert payload["outputs"] == ["clip_1_88pts_v.mp4", "clip_2_88pts_v.mp4"]

    def test_job_records_title(self, client, stub_processor):
        job_id = client.post("/api/clip", json={"url": VIDEO_URL}).get_json()["jobId"]
        assert _wait(client, job_id)["title"] == "Video Uji"

    def test_job_reaches_done_not_stuck_in_running(self, client, stub_processor):
        job_id = client.post("/api/clip", json={"url": VIDEO_URL}).get_json()["jobId"]
        assert _wait(client, job_id)["status"] != "running"

    def test_pipeline_failure_becomes_job_error(self, client, monkeypatch):
        mod = types.ModuleType("clipper.services.video_processor")
        mod.VideoProcessor = BoomProcessor
        monkeypatch.setitem(sys.modules, "clipper.services.video_processor", mod)

        job_id = client.post("/api/clip", json={"url": VIDEO_URL}).get_json()["jobId"]
        payload = _wait(client, job_id)
        assert payload["status"] == "error"
        assert "pipeline gagal total" in payload["error"]

    def test_unknown_job_is_404(self, client):
        assert client.get("/api/status/424242").status_code == 404

    def test_distinct_jobs_get_distinct_ids(self, client, stub_processor):
        a = client.post("/api/clip", json={"url": VIDEO_URL}).get_json()["jobId"]
        b = client.post("/api/clip", json={"url": VIDEO_URL}).get_json()["jobId"]
        assert a != b

    def test_jobs_do_not_share_state(self, client, stub_processor):
        a = client.post("/api/clip", json={"url": VIDEO_URL, "numClips": 1}).get_json()["jobId"]
        b = client.post("/api/clip", json={"url": VIDEO_URL, "numClips": 3}).get_json()["jobId"]
        _wait(client, a)
        _wait(client, b)
        assert len(client.get(f"/api/status/{a}").get_json()["outputs"]) == 1
        assert len(client.get(f"/api/status/{b}").get_json()["outputs"]) == 3


class TestHistory:
    def test_empty_history_is_empty_list(self, client, tmp_path):
        assert client.get("/api/history").get_json() == []

    def test_completed_job_writes_history(self, client, stub_processor, tmp_path):
        job_id = client.post("/api/clip", json={"url": VIDEO_URL}).get_json()["jobId"]
        _wait(client, job_id)
        client.get(f"/api/status/{job_id}")

        rows = client.get("/api/history").get_json()
        assert len(rows) == 1
        assert rows[0]["status"] == "done"
        assert rows[0]["title"] == "Video Uji"

    def test_history_reader_round_trip(self, client, tmp_path):
        from datetime import datetime, timezone

        path = tmp_path / "clip-history.log"
        stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        path.write_text(
            f"{stamp} | clean_white | clips=3 | min=20 | max=60 | done | Video A\n",
            encoding="utf-8",
        )
        rows = A.read_history()
        assert rows[0]["style"] == "clean_white"
        assert rows[0]["clips"] == "3"
        assert rows[0]["range"] == "20–60s"

    def test_history_skips_malformed_lines(self, client, tmp_path):
        (tmp_path / "clip-history.log").write_text(
            "too | few\n\n2026-01-01 00:00:00 | s | clips=1 | min=1 | max=2 | done | T\n",
            encoding="utf-8",
        )
        assert len(A.read_history()) == 1

    def test_history_respects_limit(self, client, tmp_path):
        from datetime import datetime, timezone

        stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            f"{stamp} | s{i} | clips=1 | min=1 | max=2 | done | T{i}\n"
            for i in range(10)
        ]
        (tmp_path / "clip-history.log").write_text("".join(lines), encoding="utf-8")
        assert len(A.read_history(limit=3)) == 3

    def test_pipe_in_title_does_not_break_the_record(self, client, tmp_path):
        A._append_history({"style": "s", "minDur": 1, "maxDur": 2},
                          types.SimpleNamespace(outputs=[], status="done", title="A | B"))
        rows = A.read_history()
        assert len(rows) == 1
        assert rows[0]["title"] == "A / B"


class TestPreview:
    def test_missing_url_is_400(self, client):
        assert client.post("/api/preview", json={}).status_code == 400

    def test_non_youtube_url_is_400(self, client):
        assert client.post("/api/preview", json={"url": "https://example.com"}).status_code == 400

    def test_preview_returns_id_for_valid_url(self, client, monkeypatch):
        from .common_stub import stub_thumbnail

        stub_thumbnail(monkeypatch)
        r = client.post("/api/preview", json={"url": VIDEO_URL})
        assert r.status_code == 200
        assert r.get_json()["id"] == "dQw4w9WgXcQ"

    def test_preview_survives_metadata_failure(self, client, monkeypatch):
        from .common_stub import stub_metadata_boom, stub_thumbnail

        stub_thumbnail(monkeypatch)
        stub_metadata_boom(monkeypatch)
        r = client.post("/api/preview", json={"url": VIDEO_URL})
        assert r.status_code == 200
        assert "warning" in r.get_json()
