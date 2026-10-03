"""Tests for ``channel_full`` running through the shared batch downloader.

``channel_full`` had its own per-video worker loop: its own error handling, its
own resume, its own archive. ``download_many`` now carries the batch semantics,
so these tests pin what the harvester must still guarantee on top of it, rather
than re-testing the batching that is already covered in
``test_download_many.py``.
"""
from __future__ import annotations

import json
from pathlib import Path

from rch.youtube.channel import channel_full, channel_slug

CHANNEL_URL = "https://www.youtube.com/@contoh/videos"


class _FakeIds:
    def __init__(self, ids):
        self._ids = list(ids)

    def __call__(self, url):
        return list(self._ids)


class _FakeMeta:
    """Stands in for MetadataClient.

    The batch is what resolve_metadata reaches for first; ``single`` and
    ``fallback`` only run for ids the batch missed, so they are here to satisfy
    the interface rather than to be exercised.
    """

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


def _opts(tmp_path, **overrides):
    opts = {"outputDir": str(tmp_path / "downloads"), "concurrency": 1}
    opts.update(overrides)
    return opts


def _meta(ids):
    return _FakeMeta({vid: {"id": vid, "title": f"Judul {vid}",
                            "description": f"Deskripsi {vid}"}
                      for vid in ids})


def _work_dir(tmp_path):
    """The folder channel_full builds for this channel.

    Derived from channel_slug rather than hardcoded: the slug is the whole
    normalised URL, so guessing it would make every path assertion here wrong
    for the wrong reason.
    """
    return tmp_path / "downloads" / f"{channel_slug(CHANNEL_URL)}-full"


def _ids(*vids):
    return _FakeIds(vids)


class TestFolderNaming:
    def test_each_video_gets_a_folder_named_after_its_title(self, tmp_path):
        channel_full(
            CHANNEL_URL, _opts(tmp_path),
            list_ids=_ids("a1", "b2"), metadata=_meta(["a1", "b2"]),
            download_video=lambda url, opts: {"status": True, "result": {"path": "x"}},
            download_image=_FakeImage(), sleep=_FakeSleep(),
        )

        root = _work_dir(tmp_path)
        names = sorted(p.name for p in root.iterdir() if p.is_dir())
        assert names == ["judul-a1", "judul-b2"]

    def test_an_unavailable_video_is_prefixed_rather_than_titled(self, tmp_path):
        """A private or deleted video has no title, so its folder is marked
        unavailable-<id> instead of colliding with some other video's slug."""
        meta = _FakeMeta({"gone": {"id": "gone", "title": None, "description": ""}})
        channel_full(
            CHANNEL_URL, _opts(tmp_path),
            list_ids=_ids("gone"), metadata=meta,
            download_video=lambda url, opts: {"status": True, "result": {"path": "x"}},
            download_image=_FakeImage(), sleep=_FakeSleep(),
        )

        assert (_work_dir(tmp_path) / "unavailable-gone").is_dir()


class TestMetadataFiles:
    def test_description_and_link_are_written_per_video(self, tmp_path):
        channel_full(
            CHANNEL_URL, _opts(tmp_path),
            list_ids=_ids("a1"), metadata=_meta(["a1"]),
            download_video=lambda url, opts: {"status": True, "result": {"path": "x"}},
            download_image=_FakeImage(), sleep=_FakeSleep(),
        )

        data = json.loads(
            (_work_dir(tmp_path) / "judul-a1" / "metadata.json")
            .read_text(encoding="utf-8")
        )
        assert data["description"] == "Deskripsi a1"
        assert data["url"].endswith("a1")

    def test_thumbnail_is_saved_next_to_them(self, tmp_path):
        channel_full(
            CHANNEL_URL, _opts(tmp_path),
            list_ids=_ids("a1"), metadata=_meta(["a1"]),
            download_video=lambda url, opts: {"status": True, "result": {"path": "x"}},
            download_image=_FakeImage(), sleep=_FakeSleep(),
        )

        folder = _work_dir(tmp_path) / "judul-a1"
        assert (folder / "thumbnail.jpg").exists()

    def test_a_thumbnail_failure_does_not_fail_the_video(self, tmp_path):
        def _no_image(url):
            raise OSError("cdn down")

        result = channel_full(
            CHANNEL_URL, _opts(tmp_path),
            list_ids=_ids("a1"), metadata=_meta(["a1"]),
            download_video=lambda url, opts: {"status": True, "result": {"path": "x"}},
            download_image=_no_image, sleep=_FakeSleep(),
        )

        assert result["status"] is True
        assert result["result"]["success"] == 1


class TestIsolation:
    def test_one_bad_video_does_not_reduce_the_success_count(self, tmp_path):
        def _mixed(url, opts):
            if "b2" in url:
                raise RuntimeError("403 Forbidden")
            folder = Path(opts["outputDir"])
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / "video.mp4"
            path.write_bytes(b"q" * 2)
            return {"status": True, "result": {"path": str(path)}}

        result = channel_full(
            CHANNEL_URL, _opts(tmp_path),
            list_ids=_ids("a1", "b2", "c3"), metadata=_meta(["a1", "b2", "c3"]),
            download_video=_mixed, download_image=_FakeImage(), sleep=_FakeSleep(),
        )

        body = result["result"]
        assert body["total"] == 3
        assert body["success"] == 2
        assert body["failed"] == 1

    def test_the_failing_video_still_gets_its_metadata_files(self, tmp_path):
        """A dead video is still a real entry; losing its description would
        make the archive look complete when it is not."""

        def _boom(url, opts):
            raise RuntimeError("tidak bisa diakses")

        channel_full(
            CHANNEL_URL, _opts(tmp_path),
            list_ids=_ids("a1"), metadata=_meta(["a1"]),
            download_video=_boom, download_image=_FakeImage(), sleep=_FakeSleep(),
        )

        folder = _work_dir(tmp_path) / "judul-a1"
        assert (folder / "metadata.json").exists()
        assert not (folder / "video.mp4").exists()

    def test_every_video_failing_is_still_a_200_envelope(self, tmp_path):
        def _boom(url, opts):
            raise RuntimeError("jaringan mati")

        result = channel_full(
            CHANNEL_URL, _opts(tmp_path),
            list_ids=_ids("a1", "b2"), metadata=_meta(["a1", "b2"]),
            download_video=_boom, download_image=_FakeImage(), sleep=_FakeSleep(),
        )

        assert result["status"] is True
        assert result["result"]["success"] == 0


class TestArchive:
    def test_a_zip_is_written_and_holds_the_metadata_files(self, tmp_path):
        channel_full(
            CHANNEL_URL, _opts(tmp_path),
            list_ids=_ids("a1"), metadata=_meta(["a1"]),
            download_video=lambda url, opts: {"status": True, "result": {"path": "x"}},
            download_image=_FakeImage(), sleep=_FakeSleep(),
        )

        zips = list((tmp_path / "downloads").glob("*.zip"))
        assert len(zips) == 1

        import zipfile
        names = zipfile.ZipFile(zips[0]).namelist()
        assert any(n.endswith("metadata.json") for n in names)
        assert any(n.endswith("thumbnail.jpg") for n in names)


class TestNoVideoMode:
    def test_include_video_false_skips_the_download_entirely(self, tmp_path):
        """Metadata-only is the mode that harvests a channel for its titles and
        descriptions without spending bandwidth on hundreds of videos."""
        called = []

        def _never(url, opts):
            called.append(url)
            return {"status": True, "result": {"path": "x"}}

        result = channel_full(
            CHANNEL_URL, _opts(tmp_path, includeVideo=False),
            list_ids=_ids("a1"), metadata=_meta(["a1"]),
            download_video=_never, download_image=_FakeImage(), sleep=_FakeSleep(),
        )

        assert called == []
        folder = _work_dir(tmp_path) / "judul-a1"
        assert (folder / "metadata.json").exists()
        assert not (folder / "video.mp4").exists()
        assert result["result"]["success"] == 1


class TestResume:
    def test_an_already_downloaded_video_is_not_fetched_again(self, tmp_path):
        def _write(url, opts):
            folder = Path(opts["outputDir"])
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / "video.mp4"
            path.write_bytes(b"q" * 2)
            return {"status": True, "result": {"path": str(path)}}

        opts = _opts(tmp_path, resume=True)
        channel_full(
            CHANNEL_URL, opts,
            list_ids=_ids("a1"), metadata=_meta(["a1"]),
            download_video=_write, download_image=_FakeImage(), sleep=_FakeSleep(),
        )

        called = []

        def _count(url, o):
            called.append(url)
            return _write(url, o)

        channel_full(
            CHANNEL_URL, opts,
            list_ids=_ids("a1"), metadata=_meta(["a1"]),
            download_video=_count, download_image=_FakeImage(), sleep=_FakeSleep(),
        )

        assert called == []


class TestSizeAndLimit:
    def test_hqdefault_is_the_default_thumbnail_size(self, tmp_path):
        seen = []

        def _record(url):
            seen.append(url)
            return b"\xff\xd8\xff\xe0" + b"x" * 40

        channel_full(
            CHANNEL_URL, _opts(tmp_path),
            list_ids=_ids("a1"), metadata=_meta(["a1"]),
            download_video=lambda url, o: {"status": True, "result": {"path": "x"}},
            download_image=_record, sleep=_FakeSleep(),
        )

        assert seen and seen[0].endswith("/hqdefault.jpg")

    def test_limit_caps_the_number_of_videos(self, tmp_path):
        result = channel_full(
            CHANNEL_URL, _opts(tmp_path, limit=2),
            list_ids=_ids("a1", "b2", "c3"), metadata=_meta(["a1", "b2", "c3"]),
            download_video=lambda url, o: {"status": True, "result": {"path": "x"}},
            download_image=_FakeImage(), sleep=_FakeSleep(),
        )

        assert result["result"]["total"] == 2

    def test_an_empty_channel_is_reported_not_raised(self, tmp_path):
        result = channel_full(
            CHANNEL_URL, _opts(tmp_path),
            list_ids=_ids(), metadata=_meta([]),
            download_image=_FakeImage(), sleep=_FakeSleep(),
        )

        assert result["status"] is False


class TestFilters:
    def test_a_filter_that_matches_nothing_reports_failure(self, tmp_path):
        """The video needs a duration for the filter to have anything to judge;
        unknown metadata is deliberately always kept."""
        meta = _FakeMeta({"a1": {"id": "a1", "title": "Judul a1",
                                 "description": "", "duration": 12}})
        result = channel_full(
            CHANNEL_URL, _opts(tmp_path, minDuration=100000),
            list_ids=_ids("a1"), metadata=meta,
            download_video=lambda url, o: {"status": True, "result": {"path": "x"}},
            download_image=_FakeImage(), sleep=_FakeSleep(),
        )

        assert result["status"] is False


class TestReportShape:
    def test_the_result_is_json_serialisable(self, tmp_path):
        """The report is what the CLI prints and the GUI polls, so a Path or a
        Path-bearing object in here breaks both."""

        def _ok(url, opts):
            folder = Path(opts["outputDir"])
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / "video.mp4"
            path.write_bytes(b"q" * 2)
            return {"status": True, "result": {"path": str(path)}}

        result = channel_full(
            CHANNEL_URL, _opts(tmp_path),
            list_ids=_ids("a1"), metadata=_meta(["a1"]),
            download_video=_ok, download_image=_FakeImage(), sleep=_FakeSleep(),
        )

        json.dumps(result)
