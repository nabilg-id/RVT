"""Tests for ``YouTubeDownloader.download_many`` - the batch path the harvester
now uses instead of its own yt-dlp calls.

Batch is the one place where a single failure must not cost the whole run, so
error isolation and the checkpoint are the parts under test. ``download`` itself
is never reached: the batch passes an injected per-item callable, so these tests
touch no network and no yt-dlp.
"""
from __future__ import annotations

import json

import pytest

from clipper.services.youtube_downloader import YouTubeDownloader


def _downloader(tmp_path):
    return YouTubeDownloader(temp_dir=tmp_path / "temp")


def _ok(index):
    """A fake per-item download that records what it was asked for."""

    def _inner(url, **kwargs):
        return {"url": url, "index": index}

    return _inner


class TestSelection:
    def test_limit_truncates_the_list(self, tmp_path):
        d = _downloader(tmp_path)
        urls = [f"https://youtu.be/{i}" for i in range(10)]

        result = d.download_many(urls, limit=3, download_item=_ok(0))

        assert [r.url for r in result.items] == urls[:3]

    def test_blank_urls_are_dropped(self, tmp_path):
        d = _downloader(tmp_path)

        result = d.download_many(
            ["https://youtu.be/a", "", "   ", None, "https://youtu.be/b"],
            download_item=_ok(0),
        )

        assert [r.url for r in result.items] == [
            "https://youtu.be/a", "https://youtu.be/b",
        ]

    def test_duplicate_urls_are_downloaded_once(self, tmp_path):
        d = _downloader(tmp_path)

        result = d.download_many(
            ["https://youtu.be/a", "https://youtu.be/a"], download_item=_ok(0)
        )

        assert len(result.items) == 1

    def test_limit_zero_or_none_means_everything(self, tmp_path):
        d = _downloader(tmp_path)
        urls = [f"https://youtu.be/{i}" for i in range(4)]

        assert len(d.download_many(urls, limit=0, download_item=_ok(0)).items) == 4
        assert len(d.download_many(urls, limit=None, download_item=_ok(0)).items) == 4


class TestErrorIsolation:
    def test_one_failure_does_not_stop_the_rest(self, tmp_path):
        """A dead video must cost one entry, not the whole channel."""
        d = _downloader(tmp_path)

        def _boom(url, **kwargs):
            if "bad" in url:
                raise RuntimeError("video tidak tersedia")
            return {"url": url}

        result = d.download_many(
            ["https://youtu.be/good1", "https://youtu.be/bad", "https://youtu.be/good2"],
            download_item=_boom,
        )

        assert [r.url for r in result.ok] == [
            "https://youtu.be/good1", "https://youtu.be/good2",
        ]
        assert len(result.failed) == 1
        assert result.failed[0].error == "video tidak tersedia"

    def test_failure_message_keeps_the_url(self, tmp_path):
        d = _downloader(tmp_path)

        def _boom(url, **kwargs):
            raise ValueError("ditolak")

        result = d.download_many(["https://youtu.be/x"], download_item=_boom)

        assert result.failed[0].url == "https://youtu.be/x"

    def test_every_item_failing_is_still_a_normal_result(self, tmp_path):
        d = _downloader(tmp_path)

        def _boom(url, **kwargs):
            raise RuntimeError("semua mati")

        result = d.download_many(
            ["https://youtu.be/a", "https://youtu.be/b"], download_item=_boom
        )

        assert result.ok == []
        assert len(result.failed) == 2


class TestConcurrency:
    def test_concurrency_one_stays_sequential(self, tmp_path):
        """concurrency=1 is a debugging mode; it must not use a pool at all."""
        d = _downloader(tmp_path)
        order = []

        def _record(url, **kwargs):
            order.append(("start", url))
            order.append(("end", url))
            return {"url": url}

        d.download_many(
            ["https://youtu.be/a", "https://youtu.be/b", "https://youtu.be/c"],
            concurrency=1, download_item=_record,
        )

        # Interleaved would mean two were in flight at once.
        assert order == [
            ("start", "https://youtu.be/a"), ("end", "https://youtu.be/a"),
            ("start", "https://youtu.be/b"), ("end", "https://youtu.be/b"),
            ("start", "https://youtu.be/c"), ("end", "https://youtu.be/c"),
        ]

    def test_parallel_run_still_reports_every_item(self, tmp_path):
        d = _downloader(tmp_path)
        urls = [f"https://youtu.be/{i}" for i in range(8)]

        result = d.download_many(urls, concurrency=4, download_item=_ok(0))

        assert len(result.ok) == 8

    def test_result_order_follows_the_input_not_completion(self, tmp_path):
        """The caller matches results against its own list, so order is the
        input order even when a later item finishes first."""
        import time

        d = _downloader(tmp_path)

        def _slow_first(url, **kwargs):
            if url.endswith("/0"):
                time.sleep(0.15)
            return {"url": url}

        urls = [f"https://youtu.be/{i}" for i in range(4)]
        result = d.download_many(urls, concurrency=4, download_item=_slow_first)

        assert [r.url for r in result.ok] == urls


class TestCheckpoint:
    def test_checkpoint_records_each_finished_url(self, tmp_path):
        d = _downloader(tmp_path)
        ckpt = tmp_path / "done.json"

        d.download_many(
            ["https://youtu.be/a", "https://youtu.be/b"],
            checkpoint=ckpt, download_item=_ok(0),
        )

        saved = json.loads(ckpt.read_text(encoding="utf-8"))
        assert saved["done"] == ["https://youtu.be/a", "https://youtu.be/b"]

    def test_second_run_skips_completed_urls(self, tmp_path):
        d = _downloader(tmp_path)
        ckpt = tmp_path / "done.json"
        ckpt.write_text(json.dumps({"done": ["https://youtu.be/a"]}), encoding="utf-8")
        seen = []

        def _record(url, **kwargs):
            seen.append(url)
            return {"url": url}

        d.download_many(
            ["https://youtu.be/a", "https://youtu.be/b"],
            checkpoint=ckpt, download_item=_record,
        )

        assert seen == ["https://youtu.be/b"]

    def test_failed_urls_are_not_marked_done(self, tmp_path):
        """Marking a failure as done would make resume skip it forever."""
        d = _downloader(tmp_path)
        ckpt = tmp_path / "done.json"

        def _boom(url, **kwargs):
            raise RuntimeError("gagal")

        d.download_many(["https://youtu.be/a"], checkpoint=ckpt, download_item=_boom)

        assert json.loads(ckpt.read_text(encoding="utf-8"))["done"] == []

    def test_corrupt_checkpoint_is_ignored_not_fatal(self, tmp_path):
        """A half-written checkpoint from a killed run must not stop a new one."""
        d = _downloader(tmp_path)
        ckpt = tmp_path / "done.json"
        ckpt.write_text("{not json", encoding="utf-8")
        seen = []

        def _record(url, **kwargs):
            seen.append(url)
            return {"url": url}

        result = d.download_many(
            ["https://youtu.be/a"], checkpoint=ckpt, download_item=_record
        )

        assert seen == ["https://youtu.be/a"]
        assert len(result.ok) == 1

    def test_checkpoint_is_optional(self, tmp_path):
        d = _downloader(tmp_path)
        d.download_many(["https://youtu.be/a"], download_item=_ok(0))

    def test_checkpoint_appends_across_runs(self, tmp_path):
        d = _downloader(tmp_path)
        ckpt = tmp_path / "done.json"

        d.download_many(["https://youtu.be/a"], checkpoint=ckpt, download_item=_ok(0))
        d.download_many(["https://youtu.be/b"], checkpoint=ckpt, download_item=_ok(0))

        assert json.loads(ckpt.read_text(encoding="utf-8"))["done"] == [
            "https://youtu.be/a", "https://youtu.be/b",
        ]


class TestSummary:
    def test_counts_add_up(self, tmp_path):
        d = _downloader(tmp_path)

        def _mixed(url, **kwargs):
            if url.endswith("bad"):
                raise RuntimeError("x")
            return {"url": url}

        result = d.download_many(
            ["https://youtu.be/ok1", "https://youtu.be/bad", "https://youtu.be/ok2"],
            download_item=_mixed,
        )

        assert result.total == 3
        assert result.succeeded == 2
        assert result.failed_count == 1

    def test_report_is_json_serialisable(self, tmp_path):
        """The report is written to disk, so it must not carry live objects."""
        d = _downloader(tmp_path)

        def _mixed(url, **kwargs):
            if url.endswith("bad"):
                raise RuntimeError("x")
            return {"url": url, "path": str(tmp_path / "v.mp4")}

        result = d.download_many(
            ["https://youtu.be/ok", "https://youtu.be/bad"], download_item=_mixed
        )

        json.dumps(result.to_dict())

    def test_failure_entries_in_the_report_carry_the_error(self, tmp_path):
        d = _downloader(tmp_path)

        def _boom(url, **kwargs):
            raise RuntimeError("kapal karam")

        result = d.download_many(["https://youtu.be/x"], download_item=_boom)

        assert result.to_dict()["failed"][0]["error"] == "kapal karam"


class TestValidation:
    def test_concurrency_below_one_is_treated_as_one(self, tmp_path):
        d = _downloader(tmp_path)
        urls = [f"https://youtu.be/{i}" for i in range(3)]

        result = d.download_many(urls, concurrency=0, download_item=_ok(0))

        assert len(result.ok) == 3

    def test_empty_input_returns_an_empty_summary(self, tmp_path):
        d = _downloader(tmp_path)
        result = d.download_many([], download_item=_ok(0))

        assert result.total == 0
        assert result.to_dict()["items"] == []

    def test_a_missing_injected_downloader_is_an_explicit_error(self, tmp_path):
        """Forgetting the fake must fail loudly, not silently do nothing."""
        d = _downloader(tmp_path)

        with pytest.raises(ValueError, match="download_item"):
            d.download_many(["https://youtu.be/a"])
