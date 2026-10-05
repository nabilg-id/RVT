"""Tests for the cancel button in the clipper page.

The endpoint exists and is tested in test_cancel.py. What was missing is the
browser half: a running job shows a Cancel button, clicking it calls the endpoint,
and the button disappears once the job is no longer running.
"""
from __future__ import annotations

from pathlib import Path

from clipper import app as A

STATIC = Path(A.__file__).resolve().parent / "static"
TEMPLATES = Path(A.__file__).resolve().parent / "templates"


def _js():
    return (STATIC / "app.js").read_text(encoding="utf-8")


def _html():
    return (TEMPLATES / "index.html").read_text(encoding="utf-8")


class TestTheButtonIsPresent:
    def test_the_page_has_a_cancel_control(self):
        assert 'id="cancelBtn"' in _html()

    def test_the_script_binds_it(self):
        assert 'cancelBtn' in _js()


class TestItCallsTheRightEndpoint:
    def test_it_posts_to_the_cancel_route(self):
        assert "/api/cancel/" in _js()

    def test_it_carries_the_current_job_id(self):
        assert "jobId" in _js()

    def test_it_does_not_use_get(self):
        """Cancelling must be a POST. A GET would let a prefetcher or a browser
        tab re-run the request and cancel a job by accident."""
        assert "_cancel(" not in _js() or 'method: "POST"' in _js()


class TestTheButtonHidesWhenItShould:
    def test_it_is_removed_when_the_job_finishes(self):
        """A button that survives the job is a button that does nothing."""
        assert "hidden" in _js() or "display" in _js()

    def test_a_terminal_status_hides_it(self):
        for status in ("done", "error", "cancelled"):
            assert status in _js()


class TestTheButtonDoesNotShipAsDefaultOn:
    def test_the_page_starts_with_the_button_hidden(self):
        """There is no running job when the page loads, so a visible Cancel
        button would be a lie."""
        body = _html()

        assert "cancelBtn" in body
        assert "hidden" in body
