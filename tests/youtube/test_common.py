"""Tests for rch.youtube.common — slugify & extract_video_id.

These mirror the contract of the legacy Node.js implementation in
the Node.js ``lib/youtube/video.js`` and the Node.js ``test/slugify.test.js``,
plus additional edge cases required for 100% coverage of security-critical
filename/URL helpers.
"""
from __future__ import annotations

import pytest

from rch.youtube.common import extract_video_id, slugify

# ---------------------------------------------------------------------------
# slugify — filename safety (critical: prevents path traversal & invalid names)
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_lowercases_text(self):
        assert slugify("Hello World") == "hello-world"

    def test_collapses_multiple_separators_into_single_hyphen(self):
        assert slugify("a   b") == "a-b"
        assert slugify("a---b") == "a-b"
        assert slugify("a___b") == "a-b"

    def test_strips_leading_and_trailing_hyphens(self):
        assert slugify("---hello---") == "hello"
        assert slugify("!!!hello!!!") == "hello"

    def test_replaces_non_alphanumeric_runs_with_hyphen(self):
        assert slugify("Hello, World!") == "hello-world"

    def test_path_traversal_is_neutralised(self):
        result = slugify("../../etc/passwd")
        assert ".." not in result
        assert "/" not in result
        assert "\\" not in result

    def test_backslash_is_stripped(self):
        result = slugify("a\\b\\c")
        assert "\\" not in result
        assert result == "a-b-c"

    def test_max_length_100(self):
        long = "a" * 300
        assert len(slugify(long)) == 100

    def test_returns_video_for_symbol_only_input(self):
        assert slugify("!!!---@@@") == "video"

    def test_returns_video_for_empty_string(self):
        assert slugify("") == "video"

    def test_handles_numeric_input(self):
        assert slugify(12345) == "12345"

    def test_strips_unicode_accents(self):
        # Non-ASCII (e.g. accented chars) are removed by [^a-z0-9].
        assert slugify("Café") == "caf"

    def test_preserves_digits_and_letters(self):
        assert slugify("Video 123 ABC") == "video-123-abc"

    def test_single_character(self):
        assert slugify("x") == "x"

    def test_whitespace_only_returns_video(self):
        assert slugify("    ") == "video"

    def test_none_input_coerced_to_string(self):
        # Matches Node.js behaviour: slugify calls .toString() on the input.
        # In Python, str(None) == "none" (JS would yield "null").
        assert slugify(None) == "none"

    def test_list_input_coerced_to_string(self):
        # Non-string inputs are stringified first, then normalised.
        assert slugify(["a", "b"]) == "a-b"

    def test_truncation_happens_after_normalisation(self):
        # 120 chars of valid content -> truncated to 100, no trailing hyphen.
        text = "a" * 120
        result = slugify(text)
        assert result == "a" * 100

    def test_truncation_does_not_leave_trailing_hyphen(self):
        # Construct input whose 101st char would be a separator.
        text = ("a" * 100) + " " + "b"
        result = slugify(text)
        assert len(result) == 100
        assert not result.endswith("-")

    def test_truncation_strips_trailing_hyphen_when_truncated_at_separator(self):
        # 99 a's then a separator then 'b': after normalisation the string is
        # 101 chars long (99a + '-' + 'b'); truncating to 100 leaves a trailing
        # '-' which must be removed. Exercises the post-truncation strip branch.
        text = ("a" * 99) + " b"
        result = slugify(text)
        assert len(result) == 99
        assert result == "a" * 99
        assert not result.endswith("-")


# ---------------------------------------------------------------------------
# extract_video_id — URL parsing (critical: input validation)
# ---------------------------------------------------------------------------


class TestExtractVideoId:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30s", "dQw4w9WgXcQ"),
            ("http://youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/e/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/v/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ],
    )
    def test_recognises_known_url_formats(self, url, expected):
        assert extract_video_id(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com",
            "",
            "https://youtu.be/shortid",
            "bukan url",
            "https://www.youtube.com/watch?v=tooshort",
            "https://www.youtube.com/watch?x=abc",
            "https://www.youtube.com/",
            None,
        ],
    )
    def test_returns_none_for_invalid_input(self, url):
        assert extract_video_id(url) is None

    def test_extracts_id_with_query_params_after(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123&t=42"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_case_insensitive_domain(self):
        assert (
            extract_video_id("HTTPS://WWW.YOUTUBE.COM/watch?v=dQw4w9WgXcQ")
            == "dQw4w9WgXcQ"
        )

    def test_id_with_uppercase_preserved(self):
        # Video ids are case-sensitive; the id itself must be preserved verbatim.
        assert (
            extract_video_id("https://youtu.be/AbCdEfGhIjK")
            == "AbCdEfGhIjK"
        )

    def test_non_string_input_returns_none(self):
        assert extract_video_id(12345) is None  # type: ignore[arg-type]
        assert extract_video_id(None) is None  # type: ignore[arg-type]
