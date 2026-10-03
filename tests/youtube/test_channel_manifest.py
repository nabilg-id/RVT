"""Tests for the manifest channel_full writes alongside the per-video files.

Each video folder carries its own ``metadata.json``, which is the right shape for
"open this one video". It is the wrong shape for "what is in this channel" -
answering that meant walking 700 folders and reading 700 files, or unzipping the
archive and doing it inside it.

The manifest is one file listing every video. It is what a catalogue consumer
wants first, and it survives a zip being extracted or not extracted at all.
"""
from __future__ import annotations

import json
import zipfile

from rch.youtube.channel import channel_full

CHANNEL_URL = "https://www.youtube.com/@contoh/videos"

MANIFEST = "manifest.json"


class _Ids:
    def __init__(self, ids):
        self._ids = list(ids)

    def __call__(self, url):
        return list(self._ids)


class _Meta:
    def __init__(self, batch):
        self._batch = batch

    def batch(self, ids):
        return {v: self._batch[v] for v in ids if v in self._batch}

    def single(self, url):
        return {}

    def fallback(self, vid):
        return {}


class _Image:
    def __call__(self, url):
        return b"\xff\xd8\xff\xe0" + b"x" * 40


def _sleep(_s):
    return None


def _meta(ids):
    return _Meta({vid: {"id": vid, "title": f"Judul {vid}",
                        "description": f"Deskripsi {vid}", "duration": 100,
                        "uploadDate": "20240101"}
                  for vid in ids})


def _run(tmp_path, ids, meta=None, **overrides):
    opts = {"outputDir": str(tmp_path / "downloads"), "concurrency": 1,
            "includeVideo": False}
    opts.update(overrides)
    return channel_full(
        CHANNEL_URL, opts,
        list_ids=_Ids(ids), metadata=meta or _meta(ids),
        download_video=lambda url, o: {"status": True, "result": {"path": "x"}},
        download_image=_Image(), sleep=_sleep,
    )


def _work(tmp_path):
    return tmp_path / "downloads" / "www-youtube-com-contoh-videos-full"


class TestTheManifestIsWritten:
    def test_it_lands_in_the_work_folder(self, tmp_path):
        _run(tmp_path, ["a1", "b2"])

        assert (_work(tmp_path) / MANIFEST).is_file()

    def test_it_is_valid_json(self, tmp_path):
        _run(tmp_path, ["a1"])

        json.loads((_work(tmp_path) / MANIFEST).read_text(encoding="utf-8"))

    def test_it_reaches_the_archive(self, tmp_path):
        """Someone who only has the zip must still be able to list the channel
        without extracting seven hundred folders first."""
        result = _run(tmp_path, ["a1", "b2"])

        with zipfile.ZipFile(result["result"]["zipPath"]) as zf:
            names = zf.namelist()

        assert any(n.endswith(MANIFEST) for n in names)

    def test_the_archived_copy_is_the_same_document(self, tmp_path):
        result = _run(tmp_path, ["a1"])

        with zipfile.ZipFile(result["result"]["zipPath"]) as zf:
            entry = next(n for n in zf.namelist() if n.endswith(MANIFEST))
            from_zip = json.loads(zf.read(entry).decode("utf-8"))

        from_disk = json.loads((_work(tmp_path) / MANIFEST).read_text(
            encoding="utf-8"))

        assert from_zip == from_disk


class TestWhatItCarries:
    def _manifest(self, tmp_path):
        return json.loads((_work(tmp_path) / MANIFEST).read_text(
            encoding="utf-8"))

    def test_it_names_the_channel_it_came_from(self, tmp_path):
        _run(tmp_path, ["a1"])

        data = self._manifest(tmp_path)
        assert data["channel"] == CHANNEL_URL
        assert data["source"] == CHANNEL_URL

    def test_it_records_when_it_was_generated(self, tmp_path):
        _run(tmp_path, ["a1"])

        assert self._manifest(tmp_path)["generatedAt"]

    def test_it_counts_what_happened(self, tmp_path):
        _run(tmp_path, ["a1", "b2"])

        counts = self._manifest(tmp_path)["counts"]
        assert counts["total"] == 2
        assert counts["withThumbnail"] == 2
        assert counts["unavailable"] == 0

    def test_it_counts_unavailable_videos(self, tmp_path):
        meta = _Meta({"a1": {"id": "a1", "title": "Satu", "description": ""},
                      "gone": {"id": "gone", "title": None, "description": ""}})
        _run(tmp_path, ["a1", "gone"], meta=meta)

        counts = self._manifest(tmp_path)["counts"]
        assert counts["total"] == 2
        assert counts["unavailable"] == 1

    def test_every_video_gets_a_row(self, tmp_path):
        _run(tmp_path, ["a1", "b2", "c3"])

        rows = self._manifest(tmp_path)["videos"]
        assert [r["id"] for r in rows] == ["a1", "b2", "c3"]

    def test_a_row_carries_the_fields_a_catalogue_needs(self, tmp_path):
        _run(tmp_path, ["a1"])

        row = self._manifest(tmp_path)["videos"][0]
        assert row["id"] == "a1"
        assert row["title"] == "Judul a1"
        assert row["url"].endswith("a1")
        assert row["folder"] == "judul-a1"
        assert row["description"] == "Deskripsi a1"
        assert row["duration"] == 100

    def test_a_row_names_its_thumbnail_file(self, tmp_path):
        """The image is a separate file, so the row has to say which one."""
        _run(tmp_path, ["a1"])

        row = self._manifest(tmp_path)["videos"][0]
        assert row["thumbnails"][0]["file"] == "thumbnail.jpg"

    def test_an_unavailable_video_is_marked_not_dropped(self, tmp_path):
        """Silently omitting them would make the channel look smaller than it
        is and hide why."""
        meta = _Meta({"gone": {"id": "gone", "title": None, "description": ""}})
        _run(tmp_path, ["gone"], meta=meta)

        row = self._manifest(tmp_path)["videos"][0]
        assert row["available"] is False
        assert row["unavailable"]

    def test_rows_are_sorted_by_title(self, tmp_path):
        meta = _Meta({"z1": {"id": "z1", "title": "Zulu", "description": ""},
                      "a2": {"id": "a2", "title": "Alpha", "description": ""}})
        _run(tmp_path, ["z1", "a2"], meta=meta)

        titles = [r["title"] for r in self._manifest(tmp_path)["videos"]]
        assert titles == ["Alpha", "Zulu"]

    def test_it_declares_its_schema(self, tmp_path):
        """A file that outlives the version that wrote it needs a way to be
        recognised."""
        _run(tmp_path, ["a1"])

        assert self._manifest(tmp_path)["schema"] == 1

    def test_the_thumbnail_size_is_recorded(self, tmp_path):
        """A consumer reading the manifest cannot otherwise tell whether the
        images are 120x90 or 480x360."""
        _run(tmp_path, ["a1"], size="hqdefault")

        assert self._manifest(tmp_path)["thumbnailSizes"] == ["hqdefault"]


class TestMetadataOnlyDoesNotClaimVideos:
    """Upstream, ``videoOk`` means "no error on this row", which is true even
    when the video was never asked for. Reporting it unchanged would tell a
    catalogue consumer that every video in a metadata-only run was downloaded,
    and the archive would contain no video at all."""

    def test_no_row_claims_a_download(self, tmp_path):
        _run(tmp_path, ["a1", "b2"], includeVideo=False)

        rows = json.loads((_work(tmp_path) / MANIFEST).read_text(
            encoding="utf-8"))["videos"]

        assert [r["videoOk"] for r in rows] == [False, False]

    def test_the_count_is_zero(self, tmp_path):
        _run(tmp_path, ["a1", "b2"], includeVideo=False)

        counts = json.loads((_work(tmp_path) / MANIFEST).read_text(
            encoding="utf-8"))["counts"]

        assert counts["withVideo"] == 0

    def test_a_real_run_still_claims_its_videos(self, tmp_path):
        _run(tmp_path, ["a1"], includeVideo=True)

        data = json.loads((_work(tmp_path) / MANIFEST).read_text(encoding="utf-8"))

        assert data["counts"]["withVideo"] == 1
        assert data["videos"][0]["videoOk"] is True

    def test_thumbnails_are_still_claimed(self, tmp_path):
        """Only the video claim is affected; the images really were fetched."""
        _run(tmp_path, ["a1"], includeVideo=False)

        counts = json.loads((_work(tmp_path) / MANIFEST).read_text(
            encoding="utf-8"))["counts"]

        assert counts["withThumbnail"] == 1


class TestResilience:
    def test_an_empty_run_still_writes_a_manifest(self, tmp_path):
        """A channel that came back with nothing is a result worth recording,
        and its absence is indistinguishable from a crash."""
        result = channel_full(
            CHANNEL_URL,
            {"outputDir": str(tmp_path / "downloads"), "concurrency": 1},
            list_ids=_Ids([]), metadata=_meta([]),
            download_image=_Image(), sleep=_sleep,
        )

        assert result["status"] is False

    def test_a_failed_download_still_lists_the_video(self, tmp_path):
        def _boom(url, o):
            raise RuntimeError("403")

        result = channel_full(
            CHANNEL_URL,
            {"outputDir": str(tmp_path / "downloads"), "concurrency": 1},
            list_ids=_Ids(["a1"]), metadata=_meta(["a1"]),
            download_video=_boom, download_image=_Image(), sleep=_sleep,
        )

        data = json.loads((_work(tmp_path) / MANIFEST).read_text(encoding="utf-8"))
        assert data["videos"][0]["id"] == "a1"
        assert data["videos"][0]["videoOk"] is False
        assert data["counts"]["failed"] == 1
        assert result["result"]["failed"] == 1
