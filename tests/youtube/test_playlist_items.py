"""Tests for the playlist item-normalisation layer.

``rch.youtube.playlist.scrape`` returns raw ``ytInitialData`` items
(``videoId``/``title``/``lengthText``/``thumbnail``). The CLI and the web GUI
need two things the raw shape cannot give them:

1. export-compatible metadata records (``id``/``duration``/``url``/...) so a
   playlist can be written to CSV/JSON by the shared
   :mod:`rch.core.export` writer, whose column set is fixed;
2. a length that is machine-readable — ``lengthText`` is a human ``"3:21"``.

These tests pin that translation before any of it exists.
"""
from __future__ import annotations

import pytest

from rch.youtube.playlist import (
    parse_length_text,
    playlist_metadata,
    to_metadata,
    watch_urls,
)

# ---------------------------------------------------------------------------
# parse_length_text — "3:21" -> 201
# ---------------------------------------------------------------------------


class TestParseLengthText:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("0:00", 0),
            ("3:21", 201),
            ("10:05", 605),
            ("1:02:03", 3723),
            ("2:00:00", 7200),
            ("59:59", 3599),
            ("  4:07  ", 247),
        ],
    )
    def test_parses_known_durations(self, text, expected):
        assert parse_length_text(text) == expected

    @pytest.mark.parametrize(
        "text",
        [None, "", "   ", "LIVE", "abc", "3:xx", "1:2:3:4", "-", "3:", ":21"],
    )
    def test_unparseable_input_is_none(self, text):
        """A malformed length must not become a fabricated duration.

        ``"3:"`` and ``":30"`` both contain one real number, and a permissive
        parse would report them as 3 s and 30 s — a wrong number reads as a
        real one, which is worse than no number.
        """
        assert parse_length_text(text) is None

    def test_non_string_input_is_none(self):
        assert parse_length_text(321) is None
        assert parse_length_text(["3:21"]) is None

    def test_seconds_are_zero_padded_two_digits(self):
        assert parse_length_text("1:5") == 65


# ---------------------------------------------------------------------------
# to_metadata — raw items -> export-shaped records keyed by video id
# ---------------------------------------------------------------------------


class TestToMetadata:
    def test_maps_the_export_column_keys(self):
        result = to_metadata([{
            "videoId": "vid1",
            "title": "Judul",
            "lengthText": "3:21",
            "thumbnail": "https://img/vid1.jpg",
        }])

        record = result["vid1"]
        assert record["id"] == "vid1"
        assert record["title"] == "Judul"
        assert record["duration"] == 201
        assert record["url"] == "https://youtu.be/vid1"
        assert record["thumbnail"] == "https://img/vid1.jpg"

    def test_records_carry_every_export_column(self):
        """``export.to_csv`` reads a fixed key set; a missing one becomes a silent blank."""
        from rch.core.export import _CSV_ITEM_KEYS

        record = to_metadata([{"videoId": "vid1"}])["vid1"]

        assert set(_CSV_ITEM_KEYS) <= set(record)

    def test_missing_title_falls_back_to_the_video_id(self):
        assert to_metadata([{"videoId": "vid1"}])["vid1"]["title"] == "vid1"

    def test_missing_length_yields_none_duration(self):
        assert to_metadata([{"videoId": "vid1"}])["vid1"]["duration"] is None

    def test_items_without_a_video_id_are_dropped(self):
        result = to_metadata([{"title": "Tanpa id"}, {"videoId": "vid1"}, None])

        assert list(result) == ["vid1"]

    def test_duplicate_ids_keep_the_first_record(self):
        result = to_metadata([
            {"videoId": "vid1", "title": "Pertama"},
            {"videoId": "vid1", "title": "Kedua"},
        ])

        assert result["vid1"]["title"] == "Pertama"

    def test_empty_input_yields_an_empty_map(self):
        assert to_metadata([]) == {}
        assert to_metadata(None) == {}

    def test_order_is_preserved(self):
        result = to_metadata([{"videoId": "a"}, {"videoId": "b"}, {"videoId": "c"}])

        assert list(result) == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# watch_urls
# ---------------------------------------------------------------------------


class TestWatchUrls:
    def test_builds_short_urls_in_order(self):
        assert watch_urls([{"videoId": "a"}, {"videoId": "b"}]) == [
            "https://youtu.be/a",
            "https://youtu.be/b",
        ]

    def test_items_without_an_id_are_skipped(self):
        assert watch_urls([{"title": "x"}, None, {"videoId": "a"}]) == [
            "https://youtu.be/a",
        ]

    def test_empty_input_yields_an_empty_list(self):
        assert watch_urls([]) == []
        assert watch_urls(None) == []


# ---------------------------------------------------------------------------
# playlist_metadata — scrape + normalise, with the empty/failure guards
# ---------------------------------------------------------------------------


def _items(count=2):
    return [{
        "videoId": f"vid{i}",
        "title": f"Video {i}",
        "lengthText": "3:21",
        "thumbnail": f"https://img/vid{i}.jpg",
    } for i in range(count)]


def _scrape_envelope(items=None, **overrides):
    payload = {
        "id": "PL123",
        "url": "https://www.youtube.com/playlist?list=PL123",
        "type": "playlist",
        "title": "My Mix",
        "author": "DJ",
        "thumbnail": "https://img/banner.jpg",
        "itemCount": 2,
        "items": _items() if items is None else items,
    }
    payload.update(overrides)
    return {"status": True, "result": payload}


class TestPlaylistMetadata:
    def test_returns_the_scrape_payload_unchanged(self):
        envelope = _scrape_envelope()

        result = playlist_metadata("u", scrape=lambda url, **kw: envelope)

        assert result == envelope

    def test_limits_the_item_list(self):
        envelope = _scrape_envelope()

        result = playlist_metadata("u", {"limit": 1}, scrape=lambda url, **kw: envelope)

        assert len(result["result"]["items"]) == 1
        assert result["result"]["itemCount"] == 1

    def test_limit_does_not_mutate_the_scraped_payload(self):
        """The scraped envelope is the caller's data; trimming must copy, not edit."""
        envelope = _scrape_envelope()

        playlist_metadata("u", {"limit": 1}, scrape=lambda url, **kw: envelope)

        assert len(envelope["result"]["items"]) == 2

    def test_absent_limit_keeps_every_item(self):
        envelope = _scrape_envelope()

        result = playlist_metadata("u", {}, scrape=lambda url, **kw: envelope)

        assert result["result"]["itemCount"] == 2

    @pytest.mark.parametrize("limit", [0, -1, None, "", "abc"])
    def test_non_positive_or_invalid_limits_are_ignored(self, limit):
        envelope = _scrape_envelope()

        result = playlist_metadata("u", {"limit": limit}, scrape=lambda url, **kw: envelope)

        assert result["result"]["itemCount"] == 2

    def test_failure_envelope_passes_through_untouched(self):
        failure = {"status": False, "message": "ytInitialData not found"}

        assert playlist_metadata("u", {"limit": 5}, scrape=lambda url, **kw: failure) is failure

    def test_result_without_items_is_tolerated(self):
        """A payload that somehow lost ``items`` must not raise at the call site."""
        envelope = _scrape_envelope()
        del envelope["result"]["items"]

        result = playlist_metadata("u", {"limit": 5}, scrape=lambda url, **kw: envelope)

        assert result["result"]["items"] == []
        assert result["result"]["itemCount"] == 0

    def test_non_dict_result_is_tolerated(self):
        result = playlist_metadata("u", {}, scrape=lambda url, **kw: {"status": True,
                                                                    "result": "teks"})

        assert result["result"] == "teks"