"""Tests for a channel run that stops when the user cancels it.

A channel run touches hundreds of videos and each one is a yt-dlp call, a
thumbnail fetch, and a set of files written to disk. The only way to stop one
used to be to kill the server, which loses every other job in flight and leaves
the ledger claiming the video is still processing.

Cancellation is therefore a *flag the workers read between items*, not a signal
that interrupts whatever is running. A video mid-download is left to finish: it
is already part way through writing files, and tearing it down mid-write would
leave a truncated video and a thumbnail that disagree with each other. What
cancellation buys is that the run does not start the next four hundred.

These tests pin that contract at the engine level, where the loop lives. The
web layer's job for turning a click into this flag is in
``tests/clipper/test_cancel_workers.py``.
"""
from __future__ import annotations

from rch.youtube.channel import channel_full, channel_video

CHANNEL_URL = "https://www.youtube.com/@contoh/videos"


class _FakeIds:
    def __init__(self, ids):
        self._ids = list(ids)

    def __call__(self, url):
        return list(self._ids)


class _FakeMeta:
    """Stands in for MetadataClient; only ``batch`` is reached here."""

    def __init__(self, batch):
        self._batch = batch

    def batch(self, ids):
        return {vid: self._batch[vid] for vid in ids if vid in self._batch}

    def single(self, url):
        return {}

    def fallback(self, video_id):
        return {}

    def list_ids(self, url):
        return []


class _FakeImage:
    def __call__(self, url):
        return b"\xff\xd8\xff\xe0" + b"x" * 40


class _FakeSleep:
    def __call__(self, seconds):
        return None


def _meta(ids):
    return _FakeMeta({vid: {"id": vid, "title": f"Judul {vid}",
                            "description": f"Deskripsi {vid}"}
                      for vid in ids})


def _opts(tmp_path, **overrides):
    opts = {"outputDir": str(tmp_path / "downloads"), "concurrency": 1}
    opts.update(overrides)
    return opts


class _Recorder:
    """A downloader that remembers which videos it was asked for."""

    def __init__(self):
        self.seen = []

    def __call__(self, url, opts):
        self.seen.append(url)
        return {"status": True, "result": {"path": "x"}}


def _vid_of(url):
    return url.rsplit("/", 1)[-1]


class TestRunWorkersStopsBetweenItems:
    def test_a_stop_before_the_first_item_downloads_nothing(self, tmp_path):
        seen = _Recorder()

        channel_full(
            CHANNEL_URL, _opts(tmp_path),
            list_ids=_FakeIds(["a1", "b2", "c3"]),
            metadata=_meta(["a1", "b2", "c3"]),
            download_video=seen, download_image=_FakeImage(),
            sleep=_FakeSleep(), should_stop=lambda: True,
        )

        assert seen.seen == []

    def test_a_stop_partway_through_leaves_the_rest_untouched(self, tmp_path):
        """The flag flips once the first video is done; a2 and a3 must never
        start, or the run would keep going for the next four hundred."""
        seen = _Recorder()
        state = {"stop": False}

        def should_stop():
            if seen.seen:
                state["stop"] = True
            return state["stop"]

        channel_full(
            CHANNEL_URL, _opts(tmp_path),
            list_ids=_FakeIds(["a1", "a2", "a3"]),
            metadata=_meta(["a1", "a2", "a3"]),
            download_video=seen, download_image=_FakeImage(),
            sleep=_FakeSleep(), should_stop=should_stop,
        )

        assert [_vid_of(u) for u in seen.seen] == ["a1"]

    def test_a_stopped_run_reports_that_it_was_cancelled(self, tmp_path):
        result = channel_full(
            CHANNEL_URL, _opts(tmp_path),
            list_ids=_FakeIds(["a1", "b2"]), metadata=_meta(["a1", "b2"]),
            download_video=_Recorder(), download_image=_FakeImage(),
            sleep=_FakeSleep(), should_stop=lambda: True,
        )

        assert result["cancelled"] is True

    def test_skipped_videos_are_absent_from_the_items(self, tmp_path):
        """A row for a video that was never downloaded would be a row claiming
        something that did not happen, and the counts would be off by the number
        skipped."""
        result = channel_full(
            CHANNEL_URL, _opts(tmp_path),
            list_ids=_FakeIds(["a1", "b2"]), metadata=_meta(["a1", "b2"]),
            download_video=_Recorder(), download_image=_FakeImage(),
            sleep=_FakeSleep(), should_stop=lambda: True,
        )

        assert result["result"]["items"] == []

    def test_an_uncancelled_run_is_not_marked_cancelled(self, tmp_path):
        result = channel_full(
            CHANNEL_URL, _opts(tmp_path),
            list_ids=_FakeIds(["a1"]), metadata=_meta(["a1"]),
            download_video=_Recorder(), download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )

        assert result["cancelled"] is False

    def test_a_run_with_no_stop_hook_downloads_everything(self, tmp_path):
        """The hook is optional; a plain run must behave exactly as before."""
        seen = _Recorder()

        channel_full(
            CHANNEL_URL, _opts(tmp_path),
            list_ids=_FakeIds(["a1", "b2", "c3"]),
            metadata=_meta(["a1", "b2", "c3"]),
            download_video=seen, download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )

        assert [_vid_of(u) for u in seen.seen] == ["a1", "b2", "c3"]

    def test_the_manifest_still_lists_only_what_was_downloaded(self, tmp_path):
        import json

        from rch.youtube.channel import MANIFEST_FILENAME, channel_slug

        root = tmp_path / "downloads" / f"{channel_slug(CHANNEL_URL)}-full"
        channel_full(
            CHANNEL_URL, _opts(tmp_path),
            list_ids=_FakeIds(["a1", "b2"]), metadata=_meta(["a1", "b2"]),
            download_video=_Recorder(), download_image=_FakeImage(),
            sleep=_FakeSleep(), should_stop=lambda: True,
        )

        manifest = json.loads(
            (root / MANIFEST_FILENAME).read_text(encoding="utf-8"))

        assert [row["id"] for row in manifest["videos"]] == []

    def test_the_worker_is_asked_before_each_item(self, tmp_path):
        """Cancelling mid-run has to be noticed without waiting for the whole
        run, so the check happens per item rather than once up front."""
        calls = []

        def should_stop():
            calls.append(len(calls))
            return False

        channel_full(
            CHANNEL_URL, _opts(tmp_path),
            list_ids=_FakeIds(["a1", "b2"]), metadata=_meta(["a1", "b2"]),
            download_video=_Recorder(), download_image=_FakeImage(),
            sleep=_FakeSleep(), should_stop=should_stop,
        )

        assert len(calls) >= 2


class TestCancellingDoesNotUndoResumeState:
    """A cancelled run is unfinished, not clean.

    Both of these are the shape of bug where stopping a run makes it look like
    it succeeded: the checkpoint is what a later resume reads to skip work it
    already did, and clearing it throws that away.
    """

    def test_a_cancelled_run_keeps_its_checkpoint(self, tmp_path):
        from rch.core.checkpoint import CHECKPOINT_FILE, mark_completed

        out = tmp_path / "downloads"
        out.mkdir(parents=True, exist_ok=True)
        mark_completed(str(out), "a1")

        channel_video(
            CHANNEL_URL, _opts(tmp_path),
            list_ids=_FakeIds(["a1", "b2"]), metadata=_meta(["a1", "b2"]),
            download_video=_Recorder(), sleep=_FakeSleep(),
            should_stop=lambda: True,
        )

        assert (out / CHECKPOINT_FILE).exists()

    def test_a_finished_run_still_clears_its_checkpoint(self, tmp_path):
        """The other half of the contract: a run that really did finish must
        keep cleaning up after itself."""
        from rch.core.checkpoint import CHECKPOINT_FILE, mark_completed

        out = tmp_path / "downloads"
        out.mkdir(parents=True, exist_ok=True)
        mark_completed(str(out), "a1")

        def ok(url, opts):
            return {"status": True, "result": {"path": "x"}}

        channel_video(
            CHANNEL_URL, _opts(tmp_path, retryFailed=False),
            list_ids=_FakeIds(["a1"]), metadata=_meta(["a1"]),
            download_video=ok, sleep=_FakeSleep(),
        )

        assert not (out / CHECKPOINT_FILE).exists()

    def test_a_cancelled_run_does_not_retry_what_it_skipped(self, tmp_path):
        """The retry pass would re-download videos the cancel just stopped, which
        is the opposite of what the user asked for."""
        attempts = []

        def failing(url, opts):
            attempts.append(url)
            return {"status": False, "message": "gagal"}

        def should_stop():
            return bool(attempts)

        channel_video(
            CHANNEL_URL, _opts(tmp_path),
            list_ids=_FakeIds(["a1", "a2"]), metadata=_meta(["a1", "a2"]),
            download_video=failing, sleep=_FakeSleep(),
            should_stop=should_stop,
        )

        assert len(attempts) == 1


class TestChannelVideoHonoursTheFlag:
    def test_it_downloads_nothing_when_already_cancelled(self, tmp_path):
        seen = _Recorder()

        channel_video(
            CHANNEL_URL, _opts(tmp_path),
            list_ids=_FakeIds(["a1", "b2"]), metadata=_meta(["a1", "b2"]),
            download_video=seen, sleep=_FakeSleep(),
            should_stop=lambda: True,
        )

        assert seen.seen == []

    def test_it_still_works_without_the_hook(self, tmp_path):
        seen = _Recorder()

        channel_video(
            CHANNEL_URL, _opts(tmp_path),
            list_ids=_FakeIds(["a1"]), metadata=_meta(["a1"]),
            download_video=seen, sleep=_FakeSleep(),
        )

        assert [_vid_of(u) for u in seen.seen] == ["a1"]
