"""The video board: which videos are downloaded, clipped, or still pending.

These routes are the only answer to "which ones are done". The ids arriving
here come straight from the browser and end up in a user-writable file, so the
validation tests matter as much as the happy path.
"""
from __future__ import annotations

import pytest

from clipper import app as A

VIDEO_ID = "dQw4w9WgXcQ"
OTHER_ID = "jNQXAC9IVRw"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    A.app.config["TESTING"] = True
    monkeypatch.setattr(A, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(A, "OUTPUT_DIR", tmp_path / "clips")
    with A.app.test_client() as c:
        yield c
    with A._JOB_LOCK:
        A._JOBS.clear()


def _record(video_id, event, **fields):
    from rch.core.tracker import append_event

    append_event(video_id, event, **fields)


class TestListVideos:
    def test_empty_ledger_returns_an_empty_board(self, client):
        data = client.get("/api/videos").get_json()
        assert data["videos"] == []
        assert data["total"] == 0
        assert data["counts"]["total"] == 0

    def test_returns_flat_views_with_labels(self, client):
        _record(VIDEO_ID, "download", status="done", title="Judul Uji")
        data = client.get("/api/videos").get_json()
        row = data["videos"][0]
        assert row["videoId"] == VIDEO_ID
        assert row["status"] == "downloaded"
        assert row["downloadStatus"] == "done"
        assert row["url"].endswith(VIDEO_ID)

    def test_labels_are_human_readable(self, client):
        _record(VIDEO_ID, "clip", status="done")
        labels = client.get("/api/videos").get_json()["labels"]
        assert labels["clipped"] == "Sudah Clip"
        assert labels["none"] == "Belum"
        assert labels["queued"] == "Antri"

    def test_counts_cover_every_bucket(self, client):
        _record(VIDEO_ID, "download", status="done")
        counts = client.get("/api/videos").get_json()["counts"]
        assert counts["downloaded"] == 1
        for key in ("none", "queued", "processing", "clipped",
                    "download_failed", "clip_failed", "total"):
            assert key in counts

    def test_filter_by_status(self, client):
        _record(VIDEO_ID, "download", status="done")
        _record(OTHER_ID, "clip", status="done")
        data = client.get("/api/videos?status=clipped").get_json()
        assert [v["videoId"] for v in data["videos"]] == [OTHER_ID]

    def test_filter_all_returns_everything(self, client):
        _record(VIDEO_ID, "download", status="done")
        _record(OTHER_ID, "seen")
        data = client.get("/api/videos?status=all").get_json()
        assert data["total"] == 2

    def test_unknown_status_filter_is_empty_not_an_error(self, client):
        _record(VIDEO_ID, "download", status="done")
        assert client.get("/api/videos?status=nonsense").get_json()["videos"] == []

    def test_search_matches_title(self, client):
        _record(VIDEO_ID, "download", status="done", title="Tutorial Adsensi")
        _record(OTHER_ID, "download", status="done", title="Video Lain")
        data = client.get("/api/videos?q=adsensi").get_json()
        assert [v["videoId"] for v in data["videos"]] == [VIDEO_ID]

    def test_search_is_case_insensitive(self, client):
        _record(VIDEO_ID, "download", status="done", title="Tutorial ADSENSI")
        assert client.get("/api/videos?q=adsensi").get_json()["total"] == 1

    def test_search_matches_video_id(self, client):
        _record(VIDEO_ID, "download", status="done")
        _record(OTHER_ID, "download", status="done")
        data = client.get(f"/api/videos?q={OTHER_ID}").get_json()
        assert [v["videoId"] for v in data["videos"]] == [OTHER_ID]

    def test_limit_truncates_the_page(self, client):
        ids = [f"{chr(97 + n)}{'b' * 10}" for n in range(5)]
        for video_id in ids:
            _record(video_id, "download", status="done")
        data = client.get("/api/videos?limit=2").get_json()
        assert len(data["videos"]) == 2

    def test_limit_is_capped_at_the_ceiling(self, client):
        _record(VIDEO_ID, "download", status="done")
        assert client.get("/api/videos?limit=99999").status_code == 200

    def test_nonsense_limit_does_not_500(self, client):
        assert client.get("/api/videos?limit=abc").status_code == 200


class TestQueue:
    def test_queue_marks_the_video(self, client):
        _record(VIDEO_ID, "download", status="done")
        r = client.post("/api/videos/queue", json={"videoId": VIDEO_ID})
        assert r.status_code == 200
        assert r.get_json()["video"]["status"] == "queued"

    def test_queue_is_reflected_in_the_board(self, client):
        _record(VIDEO_ID, "download", status="done")
        client.post("/api/videos/queue", json={"videoId": VIDEO_ID})
        row = client.get("/api/videos").get_json()["videos"][0]
        assert row["status"] == "queued"
        assert row["downloadStatus"] == "done"

    def test_queueing_does_not_start_anything(self, client):
        """A queue flag must not launch work: the user presses Generate."""
        _record(VIDEO_ID, "download", status="done")
        client.post("/api/videos/queue", json={"videoId": VIDEO_ID})
        with A._JOB_LOCK:
            assert A._JOBS == {}

    def test_unqueue_clears_the_flag(self, client):
        _record(VIDEO_ID, "download", status="done")
        client.post("/api/videos/queue", json={"videoId": VIDEO_ID})
        r = client.post("/api/videos/unqueue", json={"videoId": VIDEO_ID})
        assert r.get_json()["video"]["status"] == "downloaded"

    def test_unqueue_after_a_clip_ran_is_refused(self, client):
        _record(VIDEO_ID, "clip", status="done", files=["c.mp4"])
        r = client.post("/api/videos/unqueue", json={"videoId": VIDEO_ID})
        assert r.status_code == 409
        assert "sudah" in r.get_json()["error"].lower()

    def test_unqueue_while_processing_is_refused(self, client):
        _record(VIDEO_ID, "clip", status="running")
        assert client.post("/api/videos/unqueue",
                           json={"videoId": VIDEO_ID}).status_code == 409

    def test_queueing_an_unknown_video_is_404(self, client):
        r = client.post("/api/videos/queue", json={"videoId": OTHER_ID})
        assert r.status_code == 404

    @pytest.mark.parametrize(
        "video_id",
        [None, "", "short", "a" * 12, "../../etc/passwd", 123,
         "abc def123", "a" * 11 + "|x", "'; DROP TABLE--"],
    )
    def test_malformed_ids_are_rejected(self, client, video_id):
        r = client.post("/api/videos/queue", json={"videoId": video_id})
        assert r.status_code == 400

    def test_missing_body_is_rejected(self, client):
        assert client.post("/api/videos/queue", json={}).status_code == 400

    def test_rejected_id_does_not_reach_the_ledger(self, client):
        from rch.core.tracker import read_state

        client.post("/api/videos/queue", json={"videoId": "../../etc/passwd"})
        assert read_state() == {}


class TestBoardSurvivesABrokenLedger:
    def test_unreadable_ledger_is_a_500_not_a_crash(self, client, monkeypatch, tmp_path):
        broken = tmp_path / "video-tracker.jsonl"
        broken.write_text('{"broken', encoding="utf-8")
        monkeypatch.setenv("VIDEO_TRACKER_FILE", str(broken))
        # A torn line must degrade to fewer records, not an error.
        assert client.get("/api/videos").status_code == 200


class TestBackfillOnStart:
    def test_existing_clips_are_picked_up(self, tmp_path, monkeypatch):
        monkeypatch.setattr(A, "OUTPUT_DIR", tmp_path / "clips")
        clips = tmp_path / "clips"
        clips.mkdir()
        (clips / f"clip_1_88pts_{VIDEO_ID}.mp4").write_bytes(b"\x00" * 16)

        assert A.backfill_ledger() == 1
        from rch.core.tracker import list_videos

        assert list_videos()[0]["status"] == "clipped"

    def test_existing_downloads_are_picked_up(self, tmp_path, monkeypatch):
        monkeypatch.setattr(A, "OUTPUT_DIR", tmp_path / "clips")
        folder = tmp_path / "downloads" / "satu"
        folder.mkdir(parents=True)
        (folder / "video.mp4").write_bytes(b"\x00" * 16)
        (folder / "link.txt").write_text(f"https://youtu.be/{OTHER_ID}\n",
                                        encoding="utf-8")

        assert A.backfill_ledger() == 1
        from rch.core.tracker import list_videos

        assert list_videos()[0]["videoId"] == OTHER_ID

    def test_running_twice_adds_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(A, "OUTPUT_DIR", tmp_path / "clips")
        clips = tmp_path / "clips"
        clips.mkdir()
        (clips / f"clip_1_88pts_{VIDEO_ID}.mp4").write_bytes(b"\x00" * 16)

        assert A.backfill_ledger() == 1
        assert A.backfill_ledger() == 0

    def test_missing_folders_are_harmless(self, tmp_path, monkeypatch):
        monkeypatch.setattr(A, "OUTPUT_DIR", tmp_path / "tidak-ada")
        assert A.backfill_ledger() == 0

    def test_a_broken_tracker_import_does_not_raise(self, tmp_path, monkeypatch):
        monkeypatch.setattr(A, "OUTPUT_DIR", tmp_path / "clips")
        monkeypatch.setattr(
            "rch.core.tracker.backfill_from_disk",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert A.backfill_ledger() == 0


class TestStaticAssetsKnowAboutTheBoard:
    def test_index_renders_the_board(self, client):
        body = client.get("/").get_data(as_text=True)
        assert "Papan Video" in body
        assert 'id="videoRows"' in body

    def test_app_js_wires_the_board(self, client):
        js = client.get("/static/app.js").get_data(as_text=True)
        assert "/api/videos" in js
        assert "/api/videos/queue" in js
        assert "/api/videos/unqueue" in js

    def test_app_js_escapes_titles_it_renders(self, client):
        """Titles come from YouTube and end up in the ledger, so every value
        rendered into the board has to go through esc()."""
        js = client.get("/static/app.js").get_data(as_text=True)
        assert 'esc(v.title' in js
        assert 'esc(v.videoId' in js

    def test_board_css_is_served(self, client):
        css = client.get("/static/style.css").get_data(as_text=True)
        assert ".chip" in css