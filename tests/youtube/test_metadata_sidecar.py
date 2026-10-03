"""Tests for the per-video ``metadata.json`` that replaces the txt sidecars.

Two plain text files per video held the description and the link, and neither
carried anything else - not the title, not the duration, not the thumbnail. A
consumer had to read three files and infer the rest, and there was no way to
tell a video whose description was genuinely empty from one whose metadata was
never fetched.

One JSON file per video carries all of it. The legacy files are gone from what
is written, but everything that *reads* an old folder still has to work: people
have archives on disk from previous runs.
"""
from __future__ import annotations

import json

from rch.youtube.channel import (
    _UNAVAILABLE_DESCRIPTION,
    _write_sidecar_files,
    base_folder_name,
    channel_full,
    thumbnail_url,
)

VIDEO_ID = "dQw4w9WgXcQ"
WATCH_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
SHORT_URL = f"https://youtu.be/{VIDEO_ID}"


def _info(**overrides):
    base = {
        "id": VIDEO_ID,
        "title": "Judul Video",
        "description": "Baris pertama\nBaris kedua",
        "duration": 212,
        "uploadDate": "20230412",
    }
    base.update(overrides)
    return base


def _folder(tmp_path, name="judul"):
    folder = tmp_path / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


class TestWritesJson:
    def test_one_file_is_written_not_two(self, tmp_path):
        folder = _folder(tmp_path)

        _write_sidecar_files(folder, _info(), VIDEO_ID, False)

        assert (folder / "metadata.json").exists()
        assert not (folder / "deskripsi.txt").exists()
        assert not (folder / "link.txt").exists()

    def test_the_description_survives_verbatim(self, tmp_path):
        """Newlines included: descriptions are multi-line and the old txt file
        stored them raw, so anything that reformats them is a regression."""
        folder = _folder(tmp_path)

        _write_sidecar_files(folder, _info(), VIDEO_ID, False)
        data = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))

        assert data["description"] == "Baris pertama\nBaris kedua"

    def test_the_link_is_present_and_watchable(self, tmp_path):
        folder = _folder(tmp_path)

        _write_sidecar_files(folder, _info(), VIDEO_ID, False)
        data = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))

        assert data["id"] == VIDEO_ID
        # The short form is what link.txt held, so the tracker reads old and new
        # folders the same way.
        assert data["url"] == SHORT_URL
        assert data["watchUrl"] == WATCH_URL

    def test_the_thumbnail_is_described_even_though_it_is_a_separate_file(self, tmp_path):
        """The json says which file the image is in and where it came from, so a
        consumer does not have to guess the name or rebuild the CDN URL."""
        folder = _folder(tmp_path)

        _write_sidecar_files(folder, _info(), VIDEO_ID, False)
        data = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))

        assert data["thumbnail"]["file"] == "thumbnail.jpg"
        assert data["thumbnail"]["url"] == thumbnail_url(VIDEO_ID, "hqdefault")
        assert data["thumbnail"]["size"] == "hqdefault"

    def test_title_and_duration_travel_with_it(self, tmp_path):
        folder = _folder(tmp_path)

        _write_sidecar_files(folder, _info(), VIDEO_ID, False)
        data = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))

        assert data["title"] == "Judul Video"
        assert data["duration"] == 212
        assert data["uploadDate"] == "20230412"

    def test_the_file_is_utf8_and_readable_as_text(self, tmp_path):
        folder = _folder(tmp_path)

        _write_sidecar_files(folder, _info(description="Emoji: robot 🤖"), VIDEO_ID, False)
        raw = (folder / "metadata.json").read_text(encoding="utf-8")

        assert "🤖" in raw

    def test_the_file_is_indented_because_a_person_reads_it(self, tmp_path):
        """These get opened in an editor when something looks wrong; a single
        900-character line is unreadable."""
        folder = _folder(tmp_path)

        _write_sidecar_files(folder, _info(), VIDEO_ID, False)
        raw = (folder / "metadata.json").read_text(encoding="utf-8")

        assert "\n  " in raw


class TestUnavailableVideos:
    def test_an_unavailable_video_is_marked_not_guessed(self, tmp_path):
        folder = _folder(tmp_path)

        _write_sidecar_files(folder, {"title": None, "description": ""},
                             VIDEO_ID, True)
        data = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))

        assert data["unavailable"] is True

    def test_the_unavailable_note_is_kept_in_the_description(self, tmp_path):
        """The old txt file wrote the same placeholder text, so a folder that
        only has these two files still says why it is empty."""
        folder = _folder(tmp_path)

        _write_sidecar_files(folder, {"title": None, "description": ""},
                             VIDEO_ID, True)
        data = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))

        assert data["description"] == _UNAVAILABLE_DESCRIPTION

    def test_an_available_video_is_not_marked_unavailable(self, tmp_path):
        folder = _folder(tmp_path)

        _write_sidecar_files(folder, _info(), VIDEO_ID, False)
        data = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))

        assert data["unavailable"] is False

    def test_a_genuinely_empty_description_is_not_confused_with_unavailable(
        self, tmp_path,
    ):
        """A real video with no description must not be reported as missing."""
        folder = _folder(tmp_path)

        _write_sidecar_files(folder, _info(description=""), VIDEO_ID, False)
        data = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))

        assert data["description"] == ""
        assert data["unavailable"] is False


class TestArchiveContents:
    def test_the_json_is_what_reaches_the_zip(self, tmp_path):
        import zipfile

        class _Ids:
            def __call__(self, url):
                return [VIDEO_ID]

        class _Meta:
            def batch(self, ids):
                return {VIDEO_ID: _info()}

            def single(self, url):
                return {}

            def fallback(self, vid):
                return {}

        channel_full(
            "https://www.youtube.com/@contoh/videos",
            {"outputDir": str(tmp_path / "downloads"), "concurrency": 1},
            list_ids=_Ids(), metadata=_Meta(),
            download_video=lambda u, o: {"status": True, "result": {"path": "x"}},
            download_image=lambda url: b"\xff\xd8" + b"y" * 40,
            sleep=lambda s: None,
        )

        zips = list((tmp_path / "downloads").glob("*.zip"))
        names = zipfile.ZipFile(zips[0]).namelist()

        assert any(n.endswith("metadata.json") for n in names)
        assert not any(n.endswith("deskripsi.txt") for n in names)
        assert not any(n.endswith("link.txt") for n in names)

    def test_the_zip_json_is_valid_json(self, tmp_path):
        import zipfile

        class _Ids:
            def __call__(self, url):
                return [VIDEO_ID]

        class _Meta:
            def batch(self, ids):
                return {VIDEO_ID: _info()}

            def single(self, url):
                return {}

            def fallback(self, vid):
                return {}

        channel_full(
            "https://www.youtube.com/@contoh/videos",
            {"outputDir": str(tmp_path / "downloads"), "concurrency": 1},
            list_ids=_Ids(), metadata=_Meta(),
            download_video=lambda u, o: {"status": True, "result": {"path": "x"}},
            download_image=lambda url: b"\xff\xd8" + b"y" * 40,
            sleep=lambda s: None,
        )

        zips = list((tmp_path / "downloads").glob("*.zip"))
        archive = zipfile.ZipFile(zips[0])
        entry = next(n for n in archive.namelist() if n.endswith("metadata.json"))

        json.loads(archive.read(entry).decode("utf-8"))


class TestFolderNamingIsUnaffected:
    def test_folder_names_still_come_from_the_title(self):
        assert base_folder_name("abc", "Judul Video") == "judul-video"

    def test_a_title_that_slugs_to_nothing_falls_back_to_the_id(self):
        assert base_folder_name("abc", "///") == "abc"

    def test_no_title_at_all_is_marked_unavailable(self):
        assert base_folder_name("abc", None) == "unavailable-abc"


class TestFailureStillProducesMetadata:
    def test_a_failed_download_still_leaves_a_readable_json(self, tmp_path):
        """This is the case that used to produce an empty folder."""
        class _Ids:
            def __call__(self, url):
                return [VIDEO_ID]

        class _Meta:
            def batch(self, ids):
                return {VIDEO_ID: _info()}

            def single(self, url):
                return {}

            def fallback(self, vid):
                return {}

        def _boom(url, opts):
            raise RuntimeError("403 Forbidden")

        channel_full(
            "https://www.youtube.com/@contoh/videos",
            {"outputDir": str(tmp_path / "downloads"), "concurrency": 1},
            list_ids=_Ids(), metadata=_Meta(),
            download_video=_boom,
            download_image=lambda url: b"\xff\xd8" + b"y" * 40,
            sleep=lambda s: None,
        )

        matches = list((tmp_path / "downloads").rglob("metadata.json"))
        assert len(matches) == 1
        data = json.loads(matches[0].read_text(encoding="utf-8"))
        assert data["url"] == SHORT_URL
        assert not (matches[0].parent / "video.mp4").exists()
