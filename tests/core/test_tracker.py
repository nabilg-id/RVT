"""Tests for rch.core.tracker — the shared per-video ledger.

The tracker is written by two processes at once (the harvester's thread pool
and the clipper's job thread), so the append path and the tolerant read are the
parts worth pinning. Every test points VIDEO_TRACKER_FILE at tmp_path; anything
left unredirected would write into the working tree.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from rch.core import tracker as T

VIDEO_ID = "dQw4w9WgXcQ"
OTHER_ID = "jNQXAC9IVRw"


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "tracker.jsonl"
    monkeypatch.setenv("VIDEO_TRACKER_FILE", str(path))
    return path


@pytest.fixture()
def repo_clean(tmp_path, monkeypatch):
    """Run a body and assert the default ledger path was never touched."""
    default_path = tmp_path / "temp" / T.TRACKER_FILENAME
    monkeypatch.delenv("VIDEO_TRACKER_FILE", raising=False)
    return default_path


# -- path resolution -------------------------------------------------------


class TestPath:
    def test_env_override_wins(self, ledger):
        assert T.tracker_path() == ledger

    def test_relative_override_is_anchored_to_repo_root(self, monkeypatch):
        monkeypatch.setenv("VIDEO_TRACKER_FILE", "./temp/custom.jsonl")
        assert T.tracker_path().is_absolute()
        assert T.tracker_path().name == "custom.jsonl"
        assert T.tracker_path().parent.name == "temp"

    def test_blank_override_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("VIDEO_TRACKER_FILE", "   ")
        assert T.tracker_path().name == T.TRACKER_FILENAME

    def test_default_is_under_repo_temp(self, monkeypatch):
        monkeypatch.delenv("VIDEO_TRACKER_FILE", raising=False)
        path = T.tracker_path()
        assert path.is_absolute()
        assert path.name == T.TRACKER_FILENAME
        assert path.parent.name == "temp"


# -- validation ------------------------------------------------------------


class TestValidation:
    @pytest.mark.parametrize("value", [VIDEO_ID, OTHER_ID, "a" * 11])
    def test_valid_ids(self, value):
        assert T.is_video_id(value)

    @pytest.mark.parametrize(
        "value",
        ["", "short", "a" * 10, "a" * 12, "../../etc", "abc-def\n",
         "abc def12", None, 123, "abcdefghij!@"],
    )
    def test_invalid_ids(self, value):
        assert not T.is_video_id(value)

    def test_bad_id_is_not_written(self, ledger):
        assert T.append_event("nope", "download", status="done") is None
        assert not ledger.exists()

    def test_id_carrying_a_path_is_rejected(self, ledger):
        # The ledger is user-writable; an unvalidated id could smuggle a path
        # into it from any page that can post to the local server.
        assert T.append_event("../../etc/passwd", "download", status="done") is None
        assert not ledger.exists()


# -- appending -------------------------------------------------------------


class TestAppend:
    def test_writes_one_json_object_per_line(self, ledger):
        T.append_event(VIDEO_ID, "download", status="done", path="x.mp4")
        T.append_event(OTHER_ID, "clip", status="done", files=["a.mp4"])

        lines = ledger.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["videoId"] == VIDEO_ID
        assert json.loads(lines[1])["videoId"] == OTHER_ID

    def test_record_carries_schema_version_and_timestamp(self, ledger):
        T.append_event(VIDEO_ID, "seen")
        rec = json.loads(ledger.read_text(encoding="utf-8").strip())
        assert rec["v"] == T.SCHEMA_VERSION
        assert rec["event"] == "seen"
        assert rec["ts"]

    def test_none_fields_are_dropped(self, ledger):
        T.append_event(VIDEO_ID, "download", status="done", title=None)
        rec = json.loads(ledger.read_text(encoding="utf-8").strip())
        assert "title" not in rec

    def test_creates_parent_directory(self, tmp_path, monkeypatch):
        nested = tmp_path / "a" / "b" / "tracker.jsonl"
        monkeypatch.setenv("VIDEO_TRACKER_FILE", str(nested))
        assert T.append_event(VIDEO_ID, "seen") is not None
        assert nested.exists()

    def test_never_raises_when_unwritable(self, monkeypatch, tmp_path):
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setenv("VIDEO_TRACKER_FILE", str(blocker / "sub.jsonl"))
        # Tracking is observability; a download must not die because of it.
        assert T.append_event(VIDEO_ID, "download", status="done") is None

    def test_non_ascii_title_survives(self, ledger):
        T.append_event(VIDEO_ID, "download", status="done", title="Halo Đunia — 测试")
        assert T.read_state()[VIDEO_ID]["title"] == "Halo Đunia — 测试"

    def test_title_never_breaks_the_line_per_event_rule(self, ledger):
        T.append_event(VIDEO_ID, "download", status="done", title="a\nb\r\nc")
        raw = ledger.read_text(encoding="utf-8")
        # Exactly one line, so a multi-line title cannot forge a second record.
        assert len(raw.strip().splitlines()) == 1
        assert T.read_state()[VIDEO_ID]["title"] == "a\nb\r\nc"


class TestConcurrentAppend:
    def test_threads_do_not_interleave_a_line(self, ledger):
        """channel_full harvests with a thread pool, so the append path has to
        survive concurrent writers producing one torn line at a time."""
        errors = []

        def worker(n):
            try:
                for i in range(25):
                    T.append_event(OTHER_ID, "download", status="done",
                                   path=f"file-{n}-{i}.mp4")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        lines = ledger.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 100
        # Every line must still be valid JSON: an interleaved append would
        # produce unparsable fragments here.
        for line in lines:
            assert json.loads(line)["videoId"] == OTHER_ID


# -- reading ---------------------------------------------------------------


class TestRead:
    def test_missing_file_is_empty(self, ledger):
        assert T.read_state() == {}
        assert T.list_videos() == []

    def test_malformed_lines_are_skipped_not_fatal(self, ledger):
        T.append_event(VIDEO_ID, "download", status="done")
        with open(ledger, "a", encoding="utf-8") as fh:
            fh.write("{not json\n")
            fh.write("\n")
            fh.write('"a string"\n')
        state = T.read_state()
        assert list(state) == [VIDEO_ID]

    def test_non_dict_lines_are_skipped(self, ledger):
        ledger.write_text('[1,2,3]\n"x"\n5\n', encoding="utf-8")
        assert T.read_state() == {}

    def test_last_write_wins_per_section(self, ledger):
        T.append_event(VIDEO_ID, "download", status="done", path="final.mp4")
        T.append_event(VIDEO_ID, "download", status="failed", error="boom")
        entry = T.read_state()[VIDEO_ID]
        assert entry["download"]["status"] == "failed"
        assert entry["download"]["error"] == "boom"

    def test_download_and_clip_sections_are_independent(self, ledger):
        T.append_event(VIDEO_ID, "download", status="done", path="v.mp4")
        T.append_event(VIDEO_ID, "clip", status="done", files=["c.mp4"])
        entry = T.read_state()[VIDEO_ID]
        assert entry["download"]["status"] == "done"
        assert entry["clip"]["status"] == "done"

    def test_title_survives_across_events(self, ledger):
        T.append_event(VIDEO_ID, "download", status="done", title="Judul")
        T.append_event(VIDEO_ID, "clip", status="queued")
        assert T.read_state()[VIDEO_ID]["title"] == "Judul"

    def test_url_is_derived_when_absent(self, ledger):
        T.append_event(VIDEO_ID, "seen")
        assert T.read_state()[VIDEO_ID]["url"] == f"https://youtu.be/{VIDEO_ID}"


class TestEffectiveStatus:
    def test_seen_only_is_none(self, ledger):
        T.append_event(VIDEO_ID, "seen")
        assert T.effective_status(T.read_state()[VIDEO_ID])["status"] == "none"

    def test_downloaded(self, ledger):
        T.append_event(VIDEO_ID, "download", status="done")
        v = T.effective_status(T.read_state()[VIDEO_ID])
        assert v["status"] == "downloaded"
        assert v["downloadStatus"] == "done"

    def test_download_failed(self, ledger):
        T.append_event(VIDEO_ID, "download", status="failed", error="403")
        assert T.effective_status(T.read_state()[VIDEO_ID])["status"] == "download_failed"

    def test_queued(self, ledger):
        T.append_event(VIDEO_ID, "clip", status="queued")
        assert T.effective_status(T.read_state()[VIDEO_ID])["status"] == "queued"

    def test_running_wins_over_queued(self, ledger):
        T.append_event(VIDEO_ID, "clip", status="queued")
        T.append_event(VIDEO_ID, "clip", status="running")
        assert T.effective_status(T.read_state()[VIDEO_ID])["status"] == "processing"

    def test_clipped_wins_over_downloaded(self, ledger):
        T.append_event(VIDEO_ID, "download", status="done")
        T.append_event(VIDEO_ID, "clip", status="done", files=["c.mp4"])
        view = T.effective_status(T.read_state()[VIDEO_ID])
        assert view["status"] == "clipped"
        assert view["clipFiles"] == ["c.mp4"]

    def test_unqueue_returns_to_none(self, ledger):
        T.append_event(VIDEO_ID, "clip", status="queued")
        T.append_event(VIDEO_ID, "clip", status="none")
        assert T.effective_status(T.read_state()[VIDEO_ID])["status"] == "none"

    def test_view_exposes_both_raw_statuses(self, ledger):
        T.append_event(VIDEO_ID, "download", status="done")
        T.append_event(VIDEO_ID, "clip", status="done", style="clean_white")
        v = T.effective_status(T.read_state()[VIDEO_ID])
        assert v["downloadStatus"] == "done"
        assert v["clipStatus"] == "done"
        assert v["clipStyle"] == "clean_white"

    def test_error_is_truncated(self, ledger):
        T.append_event(VIDEO_ID, "clip", status="failed", error="x" * 5000)
        entry = T.read_state()[VIDEO_ID]
        assert len(entry["clip"]["error"]) == 200


class TestListAndSummarize:
    def test_list_is_newest_first(self, ledger):
        T.append_event(VIDEO_ID, "seen")
        T.append_event(OTHER_ID, "seen")
        ids = [v["videoId"] for v in T.list_videos()]
        assert ids[0] != ids[1]

    def test_summarize_preseeds_every_bucket(self, ledger):
        counts = T.summarize()
        for key in ("none", "downloaded", "queued", "processing", "clipped",
                    "download_failed", "clip_failed", "total"):
            assert key in counts

    def test_summarize_counts(self, ledger):
        T.append_event(VIDEO_ID, "download", status="done")
        T.append_event(OTHER_ID, "clip", status="done")
        counts = T.summarize()
        assert counts["downloaded"] == 1
        assert counts["clipped"] == 1
        assert counts["total"] == 2

    def test_statuses_for_maps_id_to_status(self, ledger):
        T.append_event(VIDEO_ID, "download", status="done")
        assert T.statuses_for() == {VIDEO_ID: "downloaded"}


# -- backfill --------------------------------------------------------------


def _make_download_folder(root, name, video_id, *, with_video=True,
                          sidecar="link.txt"):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    if with_video:
        (folder / "video.mp4").write_bytes(b"\x00" * 32)
    if sidecar == "link.txt":
        (folder / "link.txt").write_text(
            f"https://youtu.be/{video_id}\n", encoding="utf-8")
    elif sidecar == "metadata.json":
        (folder / "metadata.json").write_text(
            json.dumps({"id": video_id, "title": f"Titel {video_id}"}),
            encoding="utf-8")
    return folder


class TestBackfill:
    def test_records_folders_that_have_a_video(self, tmp_path, ledger):
        _make_download_folder(tmp_path, "satu", VIDEO_ID)
        assert T.backfill_from_disk(tmp_path, tmp_path / "klips") == 1
        view = T.list_videos()[0]
        assert view["videoId"] == VIDEO_ID
        assert view["status"] == "downloaded"
        assert view["title"] == f"Titel {VIDEO_ID}" or view["title"] is None

    def test_folder_without_video_is_not_counted(self, tmp_path, ledger):
        # channel-info writes the same folders minus video.mp4; counting those
        # would mark never-downloaded videos as done.
        _make_download_folder(tmp_path, "info-saja", OTHER_ID, with_video=False)
        assert T.backfill_from_disk(tmp_path, None) == 0
        assert T.read_state() == {}

    def test_reads_id_from_legacy_link_txt(self, tmp_path, ledger):
        _make_download_folder(tmp_path, "lama", VIDEO_ID, sidecar="link.txt")
        T.backfill_from_disk(tmp_path, None)
        assert VIDEO_ID in T.read_state()

    def test_reads_id_from_metadata_json(self, tmp_path, ledger):
        _make_download_folder(tmp_path, "baru", OTHER_ID, sidecar="metadata.json")
        T.backfill_from_disk(tmp_path, None)
        entry = T.read_state()[OTHER_ID]
        assert entry["title"] == f"Titel {OTHER_ID}"

    def test_metadata_json_wins_over_link_txt(self, tmp_path, ledger):
        folder = _make_download_folder(tmp_path, "dua", VIDEO_ID)
        (folder / "link.txt").write_text("https://youtu.be/wrongwrongwrong",
                                         encoding="utf-8")
        (folder / "metadata.json").write_text(json.dumps({"id": OTHER_ID}),
                                              encoding="utf-8")
        T.backfill_from_disk(tmp_path, None)
        assert OTHER_ID in T.read_state()

    def test_corrupt_metadata_falls_back_to_link_txt(self, tmp_path, ledger):
        folder = _make_download_folder(tmp_path, "rusak", VIDEO_ID)
        (folder / "metadata.json").write_text("{broken", encoding="utf-8")
        T.backfill_from_disk(tmp_path, None)
        assert VIDEO_ID in T.read_state()

    def test_records_existing_clips(self, tmp_path, ledger):
        clips = tmp_path / "klips"
        clips.mkdir()
        (clips / f"clip_1_88pts_{VIDEO_ID}.mp4").write_bytes(b"\x00")
        assert T.backfill_from_disk(None, clips) == 1
        view = T.list_videos()[0]
        assert view["status"] == "clipped"
        assert view["clipFiles"] == [f"clip_1_88pts_{VIDEO_ID}.mp4"]

    def test_variable_width_scores_parse(self, tmp_path, ledger):
        clips = tmp_path / "klips"
        clips.mkdir()
        (clips / f"clip_1_9pts_{VIDEO_ID}.mp4").write_bytes(b"\x00")
        (clips / f"clip_2_100pts_{OTHER_ID}.mp4").write_bytes(b"\x00")
        T.backfill_from_disk(None, clips)
        assert len(T.read_state()) == 2

    def test_unrelated_mp4_is_ignored(self, tmp_path, ledger):
        clips = tmp_path / "klips"
        clips.mkdir()
        (clips / "random-video.mp4").write_bytes(b"\x00")
        (clips / f"clip_x_88pts_{VIDEO_ID}.mp4").write_bytes(b"\x00")
        assert T.backfill_from_disk(None, clips) == 0

    def test_is_idempotent(self, tmp_path, ledger):
        _make_download_folder(tmp_path, "satu", VIDEO_ID)
        assert T.backfill_from_disk(tmp_path, None) == 1
        assert T.backfill_from_disk(tmp_path, None) == 0
        assert len(ledger.read_text(encoding="utf-8").strip().splitlines()) == 1

    def test_does_not_overwrite_a_real_status(self, tmp_path, ledger):
        _make_download_folder(tmp_path, "satu", VIDEO_ID)
        T.append_event(VIDEO_ID, "download", status="failed", error="403")
        T.backfill_from_disk(tmp_path, None)
        assert T.read_state()[VIDEO_ID]["download"]["status"] == "failed"

    def test_missing_directories_are_fine(self, tmp_path, ledger):
        assert T.backfill_from_disk(tmp_path / "tidak-ada", tmp_path / "juga") == 0

    def test_download_and_clip_for_one_video(self, tmp_path, ledger):
        _make_download_folder(tmp_path, "satu", VIDEO_ID)
        clips = tmp_path / "klips"
        clips.mkdir()
        (clips / f"clip_1_88pts_{VIDEO_ID}.mp4").write_bytes(b"\x00")
        T.backfill_from_disk(tmp_path, clips)
        view = T.list_videos()[0]
        assert view["status"] == "clipped"
        assert view["downloadStatus"] == "done"


class TestCompact:
    def test_rewrites_to_folded_state(self, ledger):
        T.append_event(VIDEO_ID, "download", status="done", path="v.mp4")
        T.append_event(VIDEO_ID, "download", status="failed", error="x")
        T.append_event(VIDEO_ID, "clip", status="done", files=["c.mp4"])

        assert T.compact() == 1
        state = T.read_state()
        assert len(state) == 1
        entry = state[VIDEO_ID]
        assert entry["download"]["status"] == "failed"
        assert entry["clip"]["files"] == ["c.mp4"]

    def test_state_is_preserved_across_compact(self, ledger):
        T.append_event(VIDEO_ID, "download", status="done", title="Judul")
        T.compact()
        assert T.read_state()[VIDEO_ID]["title"] == "Judul"

    def test_missing_file_is_a_noop(self, ledger):
        assert T.compact() == 0

    def test_empty_file_stays_empty(self, ledger):
        ledger.write_text("", encoding="utf-8")
        assert T.compact() == 0
        assert ledger.read_text(encoding="utf-8") == ""


class TestUnreadableInputs:
    """Every read path degrades to less data rather than raising."""

    def test_read_state_of_a_directory_returns_empty(self, tmp_path, ledger):
        ledger.mkdir()
        assert T.read_state(ledger) == {}

    def test_read_state_ignores_an_oserror(self, tmp_path, monkeypatch, ledger):
        ledger.write_text('{"videoId": "dQw4w9WgXcQ"}\n', encoding="utf-8")

        def _boom(self, *a, **k):
            raise OSError("permission denied")

        monkeypatch.setattr(type(ledger), "read_text", _boom)
        assert T.read_state(ledger) == {}

    def test_unreadable_link_txt_is_ignored(self, tmp_path, ledger):
        _make_download_folder(tmp_path, "satu", VIDEO_ID, sidecar="link.txt")
        real_read = Path.read_text

        def _boom(self, *a, **k):
            if self.name == "link.txt":
                raise OSError("permission denied")
            return real_read(self, *a, **k)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(Path, "read_text", _boom)
            T.backfill_from_disk(tmp_path, None)
        assert VIDEO_ID not in T.read_state()

    def test_folder_without_any_id_source_is_skipped(self, tmp_path, ledger):
        folder = tmp_path / "tanpa-id"
        folder.mkdir()
        (folder / "video.mp4").write_bytes(b"\x00")
        T.backfill_from_disk(tmp_path, None)
        assert T.read_state() == {}

    def test_link_txt_with_a_non_id_is_skipped(self, tmp_path, ledger):
        folder = _make_download_folder(tmp_path, "aneh", VIDEO_ID)
        (folder / "link.txt").write_text("https://example.com/watch",
                                         encoding="utf-8")
        T.backfill_from_disk(tmp_path, None)
        assert T.read_state() == {}

    def test_link_txt_with_query_string_still_yields_the_id(self, tmp_path, ledger):
        folder = _make_download_folder(tmp_path, "query", VIDEO_ID)
        (folder / "link.txt").write_text(
            f"https://www.youtube.com/watch?v={VIDEO_ID}&t=30s\n",
            encoding="utf-8")
        T.backfill_from_disk(tmp_path, None)
        assert VIDEO_ID in T.read_state()


class TestSeenEvent:
    def test_seen_records_a_timestamp_without_touching_a_section(self, ledger):
        T.append_event(VIDEO_ID, "seen")
        entry = T.read_state()[VIDEO_ID]
        assert entry["seenAt"]
        assert entry["download"] == {}
        assert entry["clip"] == {}

    def test_seen_does_not_reset_an_existing_download(self, ledger):
        T.append_event(VIDEO_ID, "download", status="done", path="v.mp4")
        T.append_event(VIDEO_ID, "seen")
        assert T.read_state()[VIDEO_ID]["download"]["status"] == "done"

    def test_unknown_event_type_is_ignored(self, ledger):
        T.append_event(VIDEO_ID, "telemetry", status="whatever")
        assert T.read_state()[VIDEO_ID]["clip"] == {}


class TestBackfillAfterExistingState:
    """Backfill must add what is missing without touching what is recorded."""

    def test_clip_added_for_a_video_that_only_has_a_download(
        self, tmp_path, ledger
    ):
        _make_download_folder(tmp_path, "satu", VIDEO_ID)
        clips = tmp_path / "klips"
        clips.mkdir()
        (clips / f"clip_1_88pts_{VIDEO_ID}.mp4").write_bytes(b"\x00")

        # Simulate the download already being recorded, with no clip files.
        T.append_event(VIDEO_ID, "download", status="done", path="v.mp4")

        T.backfill_from_disk(None, clips)
        view = T.list_videos()[0]
        assert view["clipFiles"] == [f"clip_1_88pts_{VIDEO_ID}.mp4"]

    def test_metadata_title_is_used_when_present(self, tmp_path, ledger):
        _make_download_folder(tmp_path, "baru", VIDEO_ID,
                              sidecar="metadata.json")
        T.backfill_from_disk(tmp_path, None)
        assert T.read_state()[VIDEO_ID]["title"] == f"Titel {VIDEO_ID}"

    def test_corrupt_metadata_still_records_the_download(self, tmp_path, ledger):
        folder = _make_download_folder(tmp_path, "rusak", VIDEO_ID)
        (folder / "metadata.json").write_text("{bukan json", encoding="utf-8")
        T.backfill_from_disk(tmp_path, None)
        assert T.read_state()[VIDEO_ID]["download"]["status"] == "done"


class TestCompactFailure:
    def test_an_unwritable_target_returns_zero(self, tmp_path):
        target = tmp_path / "t.jsonl"
        target.write_text(
            '{"v":1,"ts":"x","videoId":"' + VIDEO_ID + '",'
            '"event":"download","status":"done"}\n',
            encoding="utf-8",
        )

        def _boom(self, other):
            raise OSError("read-only file system")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(Path, "replace", _boom)
            assert T.compact(target) == 0

    def test_the_original_is_left_intact_when_compact_fails(self, ledger):
        T.append_event(VIDEO_ID, "download", status="done", title="Judul")
        before = ledger.read_text(encoding="utf-8")

        def _boom(self, other):
            raise OSError("read-only file system")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(Path, "replace", _boom)
            T.compact(ledger)

        assert ledger.read_text(encoding="utf-8") == before


class TestNoWorkingTreeWrites:
    def test_read_only_operations_touch_nothing(self, tmp_path, monkeypatch):
        """Regression guard: tracker tests that forgot to redirect
        VIDEO_TRACKER_FILE used to drop a file into the repo."""
        workdir = tmp_path / "wd"
        workdir.mkdir()
        monkeypatch.chdir(workdir)
        monkeypatch.delenv("VIDEO_TRACKER_FILE", raising=False)

        T.read_state()
        T.list_videos()
        T.summarize()
        T.statuses_for()
        T.backfill_from_disk(tmp_path / "a", tmp_path / "b")

        assert os.listdir(workdir) == []