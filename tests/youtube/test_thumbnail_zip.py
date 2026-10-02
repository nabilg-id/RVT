"""A ZIP built from several thumbnails must not contain duplicate members.

Two videos can share a title, so ``slugify`` maps both to the same filename.
``zipfile`` then writes two entries with the same arcname, which some
extractors resolve by silently taking the first or the last copy, so the
archive contents depend on ordering rather than on what was downloaded.
The channel path already dedupes via ``_ZipCollector``; this pins the same
guarantee for the single-call thumbnail path.
"""
from __future__ import annotations

import warnings
import zipfile

from rch.youtube.thumbnail import download_thumbnails

DUP = "https://youtu.be/dQw4w9WgXcQ"
UNIQUE = "https://youtu.be/9bZkp7q19f0"


def _zip(tmp_path, urls, **kwargs):
    result = download_thumbnails(
        urls,
        output_dir=str(tmp_path),
        http_get=lambda url: b"DATA",
        fetch_title=lambda vid: "Same Title",
        **kwargs,
    )
    assert result["status"] is True
    return result


class TestZipArcnamesAreUnique:
    def test_duplicate_titles_yield_no_duplicate_entries(self, tmp_path):
        result = _zip(tmp_path, [DUP, UNIQUE], zip=True)

        with zipfile.ZipFile(result["result"]["zipPath"]) as zf:
            names = zf.namelist()

        assert len(names) == len(set(names)), f"duplicate arcnames: {names}"

    def test_zipping_duplicate_titles_emits_no_warning(self, tmp_path):
        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            result = _zip(tmp_path, [DUP, UNIQUE], zip=True)

        assert result["result"]["zipPath"]

    def test_distinct_titles_are_all_kept(self, tmp_path):
        result = download_thumbnails(
            [DUP, UNIQUE],
            output_dir=str(tmp_path),
            zip=True,
            http_get=lambda url: b"DATA",
            fetch_title=lambda vid: f"Title {vid}",
        )

        with zipfile.ZipFile(result["result"]["zipPath"]) as zf:
            names = zf.namelist()

        assert len(names) == 2
        assert len(set(names)) == 2

    def test_no_zip_requested_still_works(self, tmp_path):
        result = _zip(tmp_path, [DUP, UNIQUE])

        assert "zipPath" not in result["result"]
