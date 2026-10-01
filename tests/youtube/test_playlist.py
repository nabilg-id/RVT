"""Tests for rch.youtube.playlist — playlist id extraction & data parsing.

The pure helpers (``extract_playlist_id``, ``parse_playlist_data``) are tested
directly. ``scrape`` is tested with an injected ``fetch_html`` returning a
hand-crafted HTML payload that embeds a minimal ``ytInitialData`` JSON blob.
"""
from __future__ import annotations

import json

import pytest

from rch.youtube.playlist import (
    extract_playlist_id,
    parse_playlist_data,
    scrape,
)

# ---------------------------------------------------------------------------
# extract_playlist_id — pure URL param extraction
# ---------------------------------------------------------------------------


class TestExtractPlaylistId:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://www.youtube.com/playlist?list=PLrAXtmRnQz5R", "PLrAXtmRnQz5R"),
            ("https://www.youtube.com/watch?v=abc&list=PL123", "PL123"),
            ("https://www.youtube.com/watch?list=PL456&v=xyz", "PL456"),
            ("https://youtu.be/abc?list=PL789", "PL789"),
            ("https://www.youtube.com/playlist?list=PLrAXtmRnQz5R&feature=share", "PLrAXtmRnQz5R"),
        ],
    )
    def test_extracts_id_from_known_formats(self, url, expected):
        assert extract_playlist_id(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=abc",
            "https://www.youtube.com/playlist",
            "",
            "bukan url",
            "https://www.youtube.com/playlist?list=",
        ],
    )
    def test_returns_none_when_no_list_param(self, url):
        assert extract_playlist_id(url) is None

    def test_non_string_input_returns_none(self):
        assert extract_playlist_id(None) is None  # type: ignore[arg-type]
        assert extract_playlist_id(123) is None  # type: ignore[arg-type]

    def test_case_insensitive_param(self):
        assert (
            extract_playlist_id("https://www.youtube.com/playlist?LIST=PLabc")
            == "PLabc"
        )


# ---------------------------------------------------------------------------
# parse_playlist_data — ytInitialData traversal
# ---------------------------------------------------------------------------


def _make_initial_data(*, title="My Playlist", author="Creator",
                       thumb_url="https://img.example.com/banner.jpg",
                       videos=None):
    """Build a minimal ytInitialData-shaped dict for tests."""
    videos = videos if videos is not None else [
        {
            "videoId": "vid1",
            "title": {"runs": [{"text": "First Video"}]},
            "lengthText": {"simpleText": "3:21"},
            "thumbnail": {"thumbnails": [{"url": "https://img/vid1.jpg"}]},
        },
        {
            "videoId": "vid2",
            "title": {"simpleText": "Second Video"},
            "lengthText": {"simpleText": "10:05"},
            "thumbnail": {"thumbnails": [{"url": "https://img/vid2.jpg"}]},
        },
    ]
    # Each video is wrapped in a playlistVideoRenderer and nested under
    # an itemSectionRenderer inside a sectionListRenderer. The traversal code
    # recurses arbitrarily deep so this flat list-of-dicts shape is enough.
    video_wrappers = [
        {"playlistVideoRenderer": v} for v in videos
    ]
    return {
        "header": {
            "playlistHeaderRenderer": {
                "title": {"simpleText": title},
                "ownerText": {"runs": [{"text": author}]},
                "playlistHeaderBanner": {
                    "thumbnailRenderer": {
                        "musicAlbumThumbnailRenderer": {
                            "thumbnail": {"thumbnails": [{"url": thumb_url}]}
                        }
                    }
                },
            }
        },
        "contents": {
            "sectionListRenderer": {
                "contents": [
                    {"itemSectionRenderer": {"contents": video_wrappers}}
                ]
            }
        },
    }


class TestExtractText:
    """``_extract_text`` is the shared title/author reader for every text node."""

    def test_simple_text_wins(self):
        from rch.youtube.playlist import _extract_text

        assert _extract_text({"simpleText": "Judul"}) == "Judul"

    def test_runs_are_concatenated(self):
        from rch.youtube.playlist import _extract_text

        assert _extract_text({"runs": [{"text": "A"}, {"text": "B"}]}) == "AB"

    def test_non_dict_node_is_none(self):
        from rch.youtube.playlist import _extract_text

        assert _extract_text("string") is None
        assert _extract_text(None) is None
        assert _extract_text([{"text": "A"}]) is None

    def test_empty_simple_text_is_ignored(self):
        from rch.youtube.playlist import _extract_text

        assert _extract_text({"simpleText": ""}) is None

    def test_runs_of_empty_text_yield_none(self):
        from rch.youtube.playlist import _extract_text

        assert _extract_text({"runs": [{"text": ""}]}) is None

    def test_runs_missing_text_keys_yield_none(self):
        from rch.youtube.playlist import _extract_text

        assert _extract_text({"runs": [{"bold": True}]}) is None

    def test_non_dict_run_entries_are_skipped(self):
        from rch.youtube.playlist import _extract_text

        assert _extract_text({"runs": ["x", {"text": "OK"}]}) == "OK"

    def test_runs_of_only_non_dict_entries_yield_none(self):
        from rch.youtube.playlist import _extract_text

        assert _extract_text({"runs": ["a", "b"]}) is None

    def test_empty_runs_list_yields_none(self):
        from rch.youtube.playlist import _extract_text

        assert _extract_text({"runs": []}) is None

    def test_node_without_text_keys_is_none(self):
        from rch.youtube.playlist import _extract_text

        assert _extract_text({"other": "value"}) is None

    def test_non_string_simple_text_is_coerced(self):
        from rch.youtube.playlist import _extract_text

        assert _extract_text({"simpleText": 42}) == "42"

    def test_non_list_runs_is_none(self):
        from rch.youtube.playlist import _extract_text

        assert _extract_text({"runs": "not a list"}) is None


class TestParsePlaylistData:
    def test_extracts_title_author_thumbnail(self):
        data = _make_initial_data(title="Cool Mix", author="DJ", thumb_url="https://x/y.jpg")
        result = parse_playlist_data(data)
        assert result["title"] == "Cool Mix"
        assert result["author"] == "DJ"
        assert result["thumbnail"] == "https://x/y.jpg"

    def test_extracts_video_items(self):
        data = _make_initial_data()
        result = parse_playlist_data(data)
        assert len(result["items"]) == 2
        first = result["items"][0]
        assert first["videoId"] == "vid1"
        assert first["title"] == "First Video"
        assert first["lengthText"] == "3:21"
        assert first["thumbnail"] == "https://img/vid1.jpg"

    def test_title_from_runs_when_no_simple_text(self):
        data = _make_initial_data()
        data["header"]["playlistHeaderRenderer"]["title"] = {
            "runs": [{"text": "Runs Title"}]
        }
        result = parse_playlist_data(data)
        assert result["title"] == "Runs Title"

    def test_fallback_title_when_missing(self):
        data = _make_initial_data()
        del data["header"]["playlistHeaderRenderer"]["title"]
        result = parse_playlist_data(data)
        assert result["title"] == "Untitled Playlist"

    def test_fallback_author_from_owner_endpoint(self):
        data = _make_initial_data()
        del data["header"]["playlistHeaderRenderer"]["ownerText"]
        data["header"]["playlistHeaderRenderer"]["ownerEndpoint"] = {
            "browseEndpoint": {"canonicalBaseUrl": "/@Creator"}
        }
        result = parse_playlist_data(data)
        assert result["author"] == "/@Creator"

    def test_fallback_author_unknown_when_missing(self):
        data = _make_initial_data()
        del data["header"]["playlistHeaderRenderer"]["ownerText"]
        result = parse_playlist_data(data)
        assert result["author"] == "Unknown"

    def test_thumbnail_fallback_to_thumbnails_array(self):
        data = _make_initial_data()
        del data["header"]["playlistHeaderRenderer"]["playlistHeaderBanner"]
        # The thumbnails field can be either a bare list of thumbnail dicts
        # or a wrapper dict with a nested "thumbnails" key.
        data["header"]["playlistHeaderRenderer"]["thumbnails"] = [
            {"thumbnails": [{"url": "https://fallback.jpg"}]}
        ]
        result = parse_playlist_data(data)
        assert result["thumbnail"] == "https://fallback.jpg"

    def test_thumbnail_none_when_completely_missing(self):
        data = _make_initial_data()
        del data["header"]["playlistHeaderRenderer"]["playlistHeaderBanner"]
        result = parse_playlist_data(data)
        assert result["thumbnail"] is None

    def test_video_title_fallback_to_untitled(self):
        data = _make_initial_data(videos=[{"videoId": "x"}])
        result = parse_playlist_data(data)
        assert result["items"][0]["title"] == "Untitled Video"

    def test_video_length_none_when_missing(self):
        data = _make_initial_data(videos=[{"videoId": "x", "title": {"simpleText": "T"}}])
        result = parse_playlist_data(data)
        assert result["items"][0]["lengthText"] is None

    def test_video_thumbnail_none_when_missing(self):
        data = _make_initial_data(videos=[{"videoId": "x", "title": {"simpleText": "T"}}])
        result = parse_playlist_data(data)
        assert result["items"][0]["thumbnail"] is None

    def test_empty_data_returns_defaults(self):
        result = parse_playlist_data({})
        assert result["title"] == "Untitled Playlist"
        assert result["author"] == "Unknown"
        assert result["thumbnail"] is None
        assert result["items"] == []

    def test_none_input_returns_defaults(self):
        result = parse_playlist_data(None)  # type: ignore[arg-type]
        assert result["title"] == "Untitled Playlist"
        assert result["items"] == []


# ---------------------------------------------------------------------------
# scrape — orchestration with injected fetch_html
# ---------------------------------------------------------------------------


def _make_html(data):
    return f"<html><script>var ytInitialData = {json.dumps(data)};</script></html>"


class TestScrape:
    def test_returns_success_envelope(self):
        data = _make_initial_data(title="My Mix", author="DJ")
        result = scrape(
            "https://www.youtube.com/playlist?list=PL123",
            fetch_html=lambda _url: _make_html(data),
        )
        assert result["status"] is True
        r = result["result"]
        assert r["id"] == "PL123"
        assert r["url"] == "https://www.youtube.com/playlist?list=PL123"
        assert r["title"] == "My Mix"
        assert r["author"] == "DJ"
        assert r["type"] == "playlist"
        assert r["itemCount"] == 2
        assert len(r["items"]) == 2

    def test_invalid_url_returns_failure(self):
        result = scrape("https://www.youtube.com/watch?v=abc")
        assert result["status"] is False
        assert "playlist" in result["message"].lower()

    def test_missing_initial_data_returns_failure(self):
        result = scrape(
            "https://www.youtube.com/playlist?list=PL1",
            fetch_html=lambda _url: "<html>no data</html>",
        )
        assert result["status"] is False
        assert "ytInitialData" in result["message"]

    def test_malformed_json_returns_failure(self):
        result = scrape(
            "https://www.youtube.com/playlist?list=PL1",
            fetch_html=lambda _url: "<html><script>var ytInitialData = {bad json};</script></html>",
        )
        assert result["status"] is False

    def test_fetch_html_exception_returns_failure(self):
        def boom(_url):
            raise ConnectionError("network down")

        result = scrape(
            "https://www.youtube.com/playlist?list=PL1",
            fetch_html=boom,
        )
        assert result["status"] is False
        assert "network down" in result["message"]

    def test_passes_canonical_playlist_url_to_fetch(self):
        captured = {}

        def fake_fetch(url):
            captured["url"] = url
            return _make_html(_make_initial_data())

        scrape(
            "https://www.youtube.com/watch?v=abc&list=PLxyz",
            fetch_html=fake_fetch,
        )
        assert captured["url"] == "https://www.youtube.com/playlist?list=PLxyz"
