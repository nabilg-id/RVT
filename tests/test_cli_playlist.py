"""Tests for the ``rch playlist`` CLI command.

Without it the playlist scraper in ``rch.youtube.playlist`` is unreachable: the
legacy Node.js CLI had no ``playlist`` command either, but it was exposed as
the public ``youtube.playlist()`` library call. This command is what makes the
capability usable from a terminal.

The command is the playlist counterpart of ``rch info``: it lists the playlist's
videos and can export them through the shared CSV/JSON writer.
"""
from __future__ import annotations

import copy
import json

import pytest
from click.testing import CliRunner

from rch.cli import cli

PLAYLIST_URL = "https://www.youtube.com/playlist?list=PL123"

_ENVELOPE = {
    "status": True,
    "result": {
        "id": "PL123",
        "url": PLAYLIST_URL,
        "type": "playlist",
        "title": "My Mix",
        "author": "DJ",
        "thumbnail": "https://img/banner.jpg",
        "itemCount": 2,
        "items": [
            {"videoId": "vid1", "title": "Video Satu", "lengthText": "3:21",
             "thumbnail": "https://img/vid1.jpg"},
            {"videoId": "vid2", "title": "Video Dua", "lengthText": "10:05",
             "thumbnail": "https://img/vid2.jpg"},
        ],
    },
}


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def _hermetic_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def scraped(monkeypatch):
    """Patch the scrape call the command makes and record its arguments.

    Each test gets a deep copy so a test that edits the payload (e.g. to blank a
    title) cannot leak that edit into the next test.
    """
    seen = {}
    envelope = {"value": copy.deepcopy(_ENVELOPE)}

    def fake_scrape(url, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return envelope["value"]

    monkeypatch.setattr("rch.youtube.playlist.scrape", fake_scrape)
    return seen, envelope


class TestPlaylistCommand:
    def test_help_lists_the_command(self, runner):
        result = runner.invoke(cli, ["--help"])

        assert "playlist" in result.output

    def test_prints_the_playlist_title_and_author(self, runner, scraped):
        result = runner.invoke(cli, ["playlist", PLAYLIST_URL])

        assert result.exit_code == 0
        assert "My Mix" in result.output
        assert "DJ" in result.output

    def test_prints_the_item_count(self, runner, scraped):
        result = runner.invoke(cli, ["playlist", PLAYLIST_URL])

        assert "2" in result.output

    def test_prints_one_row_per_video_with_its_title(self, runner, scraped):
        result = runner.invoke(cli, ["playlist", PLAYLIST_URL])

        assert "vid1" in result.output
        assert "Video Satu" in result.output
        assert "vid2" in result.output

    def test_prints_a_machine_readable_duration(self, runner, scraped):
        """The user needs seconds, not the raw ``3:21`` ytInitialData string."""
        result = runner.invoke(cli, ["playlist", PLAYLIST_URL])

        assert "201" in result.output

    def test_prints_nothing_for_a_video_without_a_title(self, runner, scraped):
        _seen, envelope = scraped
        envelope["value"]["result"]["items"][0]["title"] = ""

        result = runner.invoke(cli, ["playlist", PLAYLIST_URL])

        assert result.exit_code == 0
        assert "vid1" in result.output

    def test_url_is_forwarded_to_the_scraper(self, runner, scraped):
        seen, _envelope = scraped

        runner.invoke(cli, ["playlist", PLAYLIST_URL])

        assert seen["url"] == PLAYLIST_URL

    def test_limit_truncates_the_listing(self, runner, scraped):
        result = runner.invoke(cli, ["playlist", PLAYLIST_URL, "--limit", "1"])

        assert "vid1" in result.output
        assert "vid2" not in result.output

    def test_csv_export_writes_every_column(self, runner, scraped, tmp_path):
        csv_file = tmp_path / "playlist.csv"

        result = runner.invoke(
            cli, ["playlist", PLAYLIST_URL, "--csv", str(csv_file)])

        assert result.exit_code == 0
        content = csv_file.read_text(encoding="utf-8")
        assert "vid1" in content
        assert "Video Satu" in content
        assert "201" in content

    def test_json_export_is_a_list_of_records(self, runner, scraped, tmp_path):
        json_file = tmp_path / "playlist.json"

        runner.invoke(cli, ["playlist", PLAYLIST_URL, "--json", str(json_file)])

        payload = json.loads(json_file.read_text(encoding="utf-8"))
        assert [r["id"] for r in payload] == ["vid1", "vid2"]
        assert payload[0]["duration"] == 201

    def test_limit_applies_to_the_export_too(self, runner, scraped, tmp_path):
        json_file = tmp_path / "playlist.json"

        runner.invoke(
            cli, ["playlist", PLAYLIST_URL, "--limit", "1", "--json", str(json_file)])

        payload = json.loads(json_file.read_text(encoding="utf-8"))
        assert [r["id"] for r in payload] == ["vid1"]

    def test_export_path_is_echoed(self, runner, scraped, tmp_path):
        csv_file = tmp_path / "p.csv"

        result = runner.invoke(cli, ["playlist", PLAYLIST_URL, "--csv", str(csv_file)])

        assert "CSV:" in result.output

    def test_scrape_failure_exits_with_code_1(self, runner, scraped):
        _seen, envelope = scraped
        envelope["value"] = {"status": False, "message": "ytInitialData not found"}

        result = runner.invoke(cli, ["playlist", PLAYLIST_URL])

        assert result.exit_code == 1
        assert "ytInitialData not found" in result.output

    def test_scrape_failure_without_message_uses_generic_text(self, runner, scraped):
        _seen, envelope = scraped
        envelope["value"] = {"status": False}

        result = runner.invoke(cli, ["playlist", PLAYLIST_URL])

        assert result.exit_code == 1

    def test_empty_playlist_exits_zero_and_lists_nothing(self, runner, scraped):
        _seen, envelope = scraped
        envelope["value"]["result"]["items"] = []
        envelope["value"]["result"]["itemCount"] = 0

        result = runner.invoke(cli, ["playlist", PLAYLIST_URL])

        assert result.exit_code == 0
        assert "vid1" not in result.output

    def test_missing_url_is_a_usage_error(self, runner):
        assert runner.invoke(cli, ["playlist"]).exit_code == 2

    def test_non_integer_limit_is_a_usage_error(self, runner, scraped):
        assert runner.invoke(
            cli, ["playlist", PLAYLIST_URL, "--limit", "abc"]).exit_code == 2

    def test_output_is_pure_text_without_a_json_blob(self, runner, scraped):
        """The listing is human-readable; a raw payload dump would be noise."""
        result = runner.invoke(cli, ["playlist", PLAYLIST_URL])

        assert "{'status'" not in result.output

    def test_does_not_write_a_report_file(self, runner, scraped, tmp_path):
        """Listing is a read operation, so it must not litter an output dir."""
        runner.invoke(cli, ["playlist", PLAYLIST_URL])

        assert not (tmp_path / "report.txt").exists()