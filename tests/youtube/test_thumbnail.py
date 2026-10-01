"""Tests for rch.youtube.thumbnail — URL building & download orchestration.

The pure URL builder (``thumbnail_url``) and the resolver (``thumbnail``) are
unit-tested with an injected title-fetcher so no network access is required.
``download_thumbnail`` is tested with an injected ``http_get`` returning fake
bytes; the filesystem side-effect is exercised against ``tmp_path``.
"""
from __future__ import annotations

import os

import pytest

from rch.youtube.thumbnail import (
    THUMBNAIL_SIZES,
    download_thumbnail,
    download_thumbnails,
    thumbnail,
    thumbnail_url,
)

VIDEO_ID = "dQw4w9WgXcQ"


# ---------------------------------------------------------------------------
# thumbnail_url — pure URL builder
# ---------------------------------------------------------------------------


class TestThumbnailUrl:
    def test_builds_canonical_url(self):
        assert (
            thumbnail_url(VIDEO_ID, "maxresdefault")
            == f"https://i.ytimg.com/vi/{VIDEO_ID}/maxresdefault.jpg"
        )

    @pytest.mark.parametrize("size", THUMBNAIL_SIZES)
    def test_all_known_sizes_produce_jpg(self, size):
        url = thumbnail_url(VIDEO_ID, size)
        assert url.endswith(f"/{size}.jpg")
        assert url.startswith("https://i.ytimg.com/vi/")

    def test_different_video_id(self):
        assert (
            thumbnail_url("AbCdEfGhIjK", "mqdefault")
            == "https://i.ytimg.com/vi/AbCdEfGhIjK/mqdefault.jpg"
        )


# ---------------------------------------------------------------------------
# thumbnail — URL resolver (no network)
# ---------------------------------------------------------------------------


class TestThumbnail:
    def test_returns_all_sizes_by_default(self):
        result = thumbnail(f"https://youtu.be/{VIDEO_ID}")
        assert result["status"] is True
        thumbs = result["result"]["thumbnails"]
        assert set(thumbs.keys()) == set(THUMBNAIL_SIZES)
        for size, url in thumbs.items():
            assert url == thumbnail_url(VIDEO_ID, size)

    def test_uses_injected_fetch_title(self):
        captured = {}

        def fake_fetch(vid):
            captured["vid"] = vid
            return "My Cool Video"

        result = thumbnail(
            f"https://youtu.be/{VIDEO_ID}", fetch_title=fake_fetch
        )
        assert result["result"]["title"] == "My Cool Video"
        assert captured["vid"] == VIDEO_ID

    def test_falls_back_to_video_id_when_fetch_title_returns_falsy(self):
        result = thumbnail(
            f"https://youtu.be/{VIDEO_ID}", fetch_title=lambda _vid: ""
        )
        assert result["result"]["title"] == VIDEO_ID

    def test_falls_back_to_video_id_when_fetch_title_raises(self):
        def boom(_vid):
            raise RuntimeError("oembed down")

        result = thumbnail(f"https://youtu.be/{VIDEO_ID}", fetch_title=boom)
        assert result["status"] is True
        assert result["result"]["title"] == VIDEO_ID

    def test_respects_explicit_sizes_subset(self):
        result = thumbnail(
            f"https://youtu.be/{VIDEO_ID}", sizes=["mqdefault", "hqdefault"]
        )
        thumbs = result["result"]["thumbnails"]
        assert set(thumbs.keys()) == {"mqdefault", "hqdefault"}

    def test_invalid_url_returns_failure_envelope(self):
        result = thumbnail("not a url")
        assert result["status"] is False
        assert "Invalid" in result["message"] or "invalid" in result["message"].lower()

    def test_empty_sizes_treated_as_all_sizes(self):
        result = thumbnail(f"https://youtu.be/{VIDEO_ID}", sizes=[])
        assert set(result["result"]["thumbnails"].keys()) == set(THUMBNAIL_SIZES)


# ---------------------------------------------------------------------------
# download_thumbnail — single download with injected http_get + filesystem
# ---------------------------------------------------------------------------


class TestDownloadThumbnail:
    def test_writes_file_and_returns_envelope(self, tmp_path):
        payload = b"\xff\xd8\xff\xe0fake-jpeg"

        def fake_http_get(url):
            assert url == thumbnail_url(VIDEO_ID, "maxresdefault")
            return payload

        result = download_thumbnail(
            f"https://youtu.be/{VIDEO_ID}",
            output_dir=str(tmp_path),
            http_get=fake_http_get,
            fetch_title=lambda _vid: "My Title",
        )

        assert result["status"] is True
        out = result["result"]
        assert out["id"] == VIDEO_ID
        assert out["title"] == "My Title"
        assert out["size"] == "maxresdefault"
        assert out["sizeBytes"] == len(payload)
        assert os.path.isfile(out["path"])
        with open(out["path"], "rb") as f:
            assert f.read() == payload
        # filename uses slugified title + size
        assert os.path.basename(out["path"]) == "my-title-maxresdefault.jpg"

    def test_invalid_url_returns_failure(self, tmp_path):
        result = download_thumbnail("bukan url", output_dir=str(tmp_path))
        assert result["status"] is False
        assert "message" in result

    def test_invalid_size_returns_failure(self, tmp_path):
        result = download_thumbnail(
            f"https://youtu.be/{VIDEO_ID}",
            size="giant",
            output_dir=str(tmp_path),
            http_get=lambda _url: b"",
        )
        assert result["status"] is False
        assert "size" in result["message"].lower() or "invalid" in result["message"].lower()

    def test_http_error_returns_failure(self, tmp_path):
        def boom(_url):
            raise ConnectionError("network down")

        result = download_thumbnail(
            f"https://youtu.be/{VIDEO_ID}",
            output_dir=str(tmp_path),
            http_get=boom,
            fetch_title=lambda _vid: "T",
        )
        assert result["status"] is False
        assert "network down" in result["message"]

    def test_custom_filename_overrides_slug(self, tmp_path):
        result = download_thumbnail(
            f"https://youtu.be/{VIDEO_ID}",
            output_dir=str(tmp_path),
            filename="custom.jpg",
            http_get=lambda _url: b"x",
            fetch_title=lambda _vid: "Ignored",
        )
        assert result["status"] is True
        assert os.path.basename(result["result"]["path"]) == "custom.jpg"

    def test_creates_output_dir_if_missing(self, tmp_path):
        nested = tmp_path / "deeply" / "nested" / "dir"
        result = download_thumbnail(
            f"https://youtu.be/{VIDEO_ID}",
            output_dir=str(nested),
            http_get=lambda _url: b"x",
            fetch_title=lambda _vid: "T",
        )
        assert result["status"] is True
        assert os.path.isfile(result["result"]["path"])

    def test_fetch_title_exception_falls_back_to_video_id(self, tmp_path):
        def boom(_vid):
            raise RuntimeError("oembed down")

        result = download_thumbnail(
            f"https://youtu.be/{VIDEO_ID}",
            output_dir=str(tmp_path),
            http_get=lambda _url: b"x",
            fetch_title=boom,
        )
        assert result["status"] is True
        assert result["result"]["title"] == VIDEO_ID

    def test_http_get_returning_text_is_encoded_as_bytes(self, tmp_path):
        result = download_thumbnail(
            f"https://youtu.be/{VIDEO_ID}",
            output_dir=str(tmp_path),
            http_get=lambda _url: "not-bytes-but-text",
            fetch_title=lambda _vid: "T",
        )
        assert result["status"] is True
        with open(result["result"]["path"], "rb") as f:
            assert f.read() == b"not-bytes-but-text"


# ---------------------------------------------------------------------------
# download_thumbnails — batch with optional zip
# ---------------------------------------------------------------------------


class TestDownloadThumbnails:
    def _two_urls(self):
        return [
            f"https://youtu.be/{VIDEO_ID}",
            "https://www.youtube.com/watch?v=AbCdEfGhIjK",
        ]

    def test_downloads_all_items(self, tmp_path):
        result = download_thumbnails(
            self._two_urls(),
            output_dir=str(tmp_path),
            http_get=lambda _url: b"img",
            fetch_title=lambda _vid: f"Title-{_vid}",
        )
        assert result["status"] is True
        r = result["result"]
        assert r["total"] == 2
        assert r["downloaded"] == 2
        assert r["failed"] == 0
        assert len(r["items"]) == 2
        for item in r["items"]:
            assert item["status"] is True
            assert os.path.isfile(item["result"]["path"])

    def test_mixed_success_and_failure(self, tmp_path):
        def flaky_http(url):
            if "AbCdEfGhIjK" in url:
                raise ConnectionError("boom")
            return b"img"

        result = download_thumbnails(
            self._two_urls(),
            output_dir=str(tmp_path),
            http_get=flaky_http,
            fetch_title=lambda _vid: "T",
        )
        r = result["result"]
        assert r["downloaded"] == 1
        assert r["failed"] == 1

    def test_creates_zip_when_requested(self, tmp_path):
        result = download_thumbnails(
            self._two_urls(),
            output_dir=str(tmp_path),
            zip=True,
            http_get=lambda _url: b"img",
            fetch_title=lambda _vid: "T",
        )
        r = result["result"]
        assert "zipPath" in r
        assert os.path.isfile(r["zipPath"])

    def test_no_zip_when_no_successes(self, tmp_path):
        result = download_thumbnails(
            ["bukan url"],
            output_dir=str(tmp_path),
            http_get=lambda _url: b"img",
        )
        r = result["result"]
        assert "zipPath" not in r
        assert r["downloaded"] == 0

    def test_single_url_not_in_list(self, tmp_path):
        result = download_thumbnails(
            f"https://youtu.be/{VIDEO_ID}",
            output_dir=str(tmp_path),
            http_get=lambda _url: b"img",
            fetch_title=lambda _vid: "T",
        )
        assert result["result"]["total"] == 1
        assert result["result"]["downloaded"] == 1

    def test_custom_zip_name(self, tmp_path):
        result = download_thumbnails(
            self._two_urls(),
            output_dir=str(tmp_path),
            zip=True,
            zip_name="bundle.zip",
            http_get=lambda _url: b"img",
            fetch_title=lambda _vid: "T",
        )
        assert os.path.basename(result["result"]["zipPath"]) == "bundle.zip"
