"""Tests for rch.core.http — HTTP retry with exponential backoff.

``requests`` is never allowed to reach the network here: the real
``requests.get``/``requests.post`` symbols are monkeypatched with scripted
fakes, and ``time.sleep`` is replaced so backoff is observed (not waited on).
The retry contract is a production risk — a 429 storm must not turn into a
tight request loop — so the attempt count and the exact backoff schedule are
asserted explicitly.
"""
from __future__ import annotations

import pytest
import requests

from rch.core import http as http_module
from rch.core.http import RETRYABLE_STATUS, http_get, http_post

_URL = "https://example.invalid/resource"


class FakeResponse:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(self, status_code=200, text="body", content=b"body"):
        self.status_code = status_code
        self.text = text
        self.content = content


class ScriptedTransport:
    """Replays a scripted list of outcomes and records every call."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outcome = self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def sleeps(monkeypatch):
    """Replace ``time.sleep`` with a recorder — backoff is asserted, not waited."""
    recorded = []
    monkeypatch.setattr(http_module.time, "sleep", recorded.append)
    return recorded


@pytest.fixture
def no_network(monkeypatch):
    """Fail loudly if any real transport is used without being scripted."""

    def forbidden(*args, **kwargs):
        raise AssertionError("network access attempted in tests")

    monkeypatch.setattr(requests, "get", forbidden)
    monkeypatch.setattr(requests, "post", forbidden)
    monkeypatch.setattr(requests, "request", forbidden)


class TestConstants:
    def test_retryable_status_set(self):
        assert RETRYABLE_STATUS == {429, 500, 502, 503, 504}


class TestHttpGetSuccess:
    def test_returns_response_on_first_ok(self, monkeypatch, sleeps, no_network):
        transport = ScriptedTransport([FakeResponse(200, "hello")])
        monkeypatch.setattr(requests, "get", transport)

        resp = http_get(_URL)

        assert resp.status_code == 200
        assert resp.text == "hello"

    def test_no_sleep_on_immediate_success(self, monkeypatch, sleeps, no_network):
        monkeypatch.setattr(requests, "get", ScriptedTransport([FakeResponse(200)]))

        http_get(_URL)

        assert sleeps == []

    def test_forwards_timeout_and_extra_kwargs(self, monkeypatch, sleeps, no_network):
        transport = ScriptedTransport([FakeResponse(200)])
        monkeypatch.setattr(requests, "get", transport)

        http_get(_URL, timeout=7, headers={"A": "b"})

        _, kwargs = transport.calls[0]
        assert kwargs["timeout"] == 7
        assert kwargs["headers"] == {"A": "b"}

    @pytest.mark.parametrize("status", [200, 201, 204, 301, 400, 401, 403, 404, 418])
    def test_non_retryable_status_returns_without_retry(self, status, monkeypatch,
                                                        sleeps, no_network):
        transport = ScriptedTransport([FakeResponse(status)])
        monkeypatch.setattr(requests, "get", transport)

        resp = http_get(_URL)

        assert resp.status_code == status
        assert len(transport.calls) == 1
        assert sleeps == []


class TestHttpGetRetryOnStatus:
    @pytest.mark.parametrize("status", sorted(RETRYABLE_STATUS))
    def test_retries_retryable_status_then_succeeds(self, status, monkeypatch,
                                                    sleeps, no_network):
        transport = ScriptedTransport([FakeResponse(status), FakeResponse(200)])
        monkeypatch.setattr(requests, "get", transport)

        resp = http_get(_URL, retries=3, base_delay=1.0)

        assert resp.status_code == 200
        assert len(transport.calls) == 2

    def test_raises_http_error_after_exhausting_retries(self, monkeypatch,
                                                        sleeps, no_network):
        transport = ScriptedTransport([FakeResponse(503)])
        monkeypatch.setattr(requests, "get", transport)

        with pytest.raises(requests.HTTPError) as excinfo:
            http_get(_URL, retries=2, base_delay=1.0)

        assert "HTTP 503" in str(excinfo.value)

    def test_attempt_count_is_retries_plus_one(self, monkeypatch, sleeps, no_network):
        transport = ScriptedTransport([FakeResponse(500)])
        monkeypatch.setattr(requests, "get", transport)

        with pytest.raises(requests.HTTPError):
            http_get(_URL, retries=4, base_delay=0.0)

        assert len(transport.calls) == 5

    def test_zero_retries_means_single_attempt(self, monkeypatch, sleeps, no_network):
        transport = ScriptedTransport([FakeResponse(500)])
        monkeypatch.setattr(requests, "get", transport)

        with pytest.raises(requests.HTTPError):
            http_get(_URL, retries=0)

        assert len(transport.calls) == 1
        assert sleeps == []

    def test_backoff_is_exponential_and_starts_at_base_delay(self, monkeypatch,
                                                             sleeps, no_network):
        transport = ScriptedTransport([FakeResponse(500)])
        monkeypatch.setattr(requests, "get", transport)

        with pytest.raises(requests.HTTPError):
            http_get(_URL, retries=3, base_delay=1.0)

        assert sleeps == [1.0, 2.0, 4.0]

    def test_base_delay_is_respected(self, monkeypatch, sleeps, no_network):
        transport = ScriptedTransport([FakeResponse(502)])
        monkeypatch.setattr(requests, "get", transport)

        with pytest.raises(requests.HTTPError):
            http_get(_URL, retries=2, base_delay=0.25)

        assert sleeps == [0.25, 0.5]

    def test_recovers_after_two_failures(self, monkeypatch, sleeps, no_network):
        transport = ScriptedTransport([FakeResponse(500), FakeResponse(429), FakeResponse(200)])
        monkeypatch.setattr(requests, "get", transport)

        resp = http_get(_URL, retries=3, base_delay=0.5)

        assert resp.status_code == 200
        assert len(transport.calls) == 3
        assert sleeps == [0.5, 1.0]

    def test_retryable_status_then_success_does_not_raise(self, monkeypatch,
                                                          sleeps, no_network):
        monkeypatch.setattr(requests, "get", ScriptedTransport([FakeResponse(429), FakeResponse(204)]))

        resp = http_get(_URL, retries=1, base_delay=0.0)

        assert resp.status_code == 204


class TestHttpGetRetryOnException:
    @pytest.mark.parametrize(
        "error",
        [
            requests.ConnectionError("connection reset"),
            requests.Timeout("timed out"),
        ],
    )
    def test_retries_transport_errors_then_succeeds(self, error, monkeypatch,
                                                    sleeps, no_network):
        transport = ScriptedTransport([error, FakeResponse(200)])
        monkeypatch.setattr(requests, "get", transport)

        resp = http_get(_URL, retries=2, base_delay=1.0)

        assert resp.status_code == 200
        assert len(transport.calls) == 2
        assert sleeps == [1.0]

    def test_raises_connection_error_after_exhausting_retries(self, monkeypatch,
                                                               sleeps, no_network):
        transport = ScriptedTransport([requests.ConnectionError("down")])
        monkeypatch.setattr(requests, "get", transport)

        with pytest.raises(requests.ConnectionError):
            http_get(_URL, retries=1, base_delay=0.0)

        assert len(transport.calls) == 2

    def test_raises_timeout_after_exhausting_retries(self, monkeypatch, sleeps, no_network):
        transport = ScriptedTransport([requests.Timeout("slow")])
        monkeypatch.setattr(requests, "get", transport)

        with pytest.raises(requests.Timeout):
            http_get(_URL, retries=0)

        assert len(transport.calls) == 1

    def test_backoff_schedule_for_exception_retries(self, monkeypatch, sleeps, no_network):
        transport = ScriptedTransport([requests.Timeout("slow")])
        monkeypatch.setattr(requests, "get", transport)

        with pytest.raises(requests.Timeout):
            http_get(_URL, retries=3, base_delay=2.0)

        assert sleeps == [2.0, 4.0, 8.0]

    def test_mixes_status_and_exception_failures(self, monkeypatch, sleeps, no_network):
        transport = ScriptedTransport(
            [FakeResponse(500), requests.ConnectionError("reset"), FakeResponse(200)]
        )
        monkeypatch.setattr(requests, "get", transport)

        resp = http_get(_URL, retries=3, base_delay=0.1)

        assert resp.status_code == 200
        assert sleeps == [0.1, 0.2]

    def test_non_retryable_exception_propagates_without_retry(self, monkeypatch,
                                                              sleeps, no_network):
        transport = ScriptedTransport([requests.TooManyRedirects("loop")])
        monkeypatch.setattr(requests, "get", transport)

        with pytest.raises(requests.TooManyRedirects):
            http_get(_URL, retries=3)

        assert len(transport.calls) == 1
        assert sleeps == []

    def test_unknown_exception_propagates_without_retry(self, monkeypatch, sleeps, no_network):
        transport = ScriptedTransport([ValueError("bad url")])
        monkeypatch.setattr(requests, "get", transport)

        with pytest.raises(ValueError):
            http_get(_URL, retries=3)

        assert len(transport.calls) == 1

    def test_raises_runtime_error_when_no_attempt_is_configured(self, monkeypatch,
                                                                sleeps, no_network):
        transport = ScriptedTransport([FakeResponse(200)])
        monkeypatch.setattr(requests, "get", transport)

        with pytest.raises(RuntimeError, match="http_get failed"):
            http_get(_URL, retries=-1)

        assert transport.calls == []
        assert sleeps == []


class TestHttpPost:
    def test_returns_response_on_first_ok(self, monkeypatch, sleeps, no_network):
        transport = ScriptedTransport([FakeResponse(200, "created")])
        monkeypatch.setattr(requests, "post", transport)

        resp = http_post(_URL)

        assert resp.status_code == 200
        assert resp.text == "created"
        assert sleeps == []

    def test_forwards_json_body_and_timeout(self, monkeypatch, sleeps, no_network):
        transport = ScriptedTransport([FakeResponse(200)])
        monkeypatch.setattr(requests, "post", transport)

        http_post(_URL, timeout=5, json={"a": 1})

        _, kwargs = transport.calls[0]
        assert kwargs["json"] == {"a": 1}
        assert kwargs["timeout"] == 5

    @pytest.mark.parametrize("status", sorted(RETRYABLE_STATUS))
    def test_retries_retryable_status_then_succeeds(self, status, monkeypatch,
                                                    sleeps, no_network):
        transport = ScriptedTransport([FakeResponse(status), FakeResponse(201)])
        monkeypatch.setattr(requests, "post", transport)

        resp = http_post(_URL, retries=3, base_delay=1.0)

        assert resp.status_code == 201
        assert len(transport.calls) == 2

    def test_raises_http_error_after_exhausting_retries(self, monkeypatch,
                                                        sleeps, no_network):
        transport = ScriptedTransport([FakeResponse(500)])
        monkeypatch.setattr(requests, "post", transport)

        with pytest.raises(requests.HTTPError) as excinfo:
            http_post(_URL, retries=2, base_delay=0.0)

        assert "HTTP 500" in str(excinfo.value)
        assert len(transport.calls) == 3

    def test_backoff_is_exponential(self, monkeypatch, sleeps, no_network):
        transport = ScriptedTransport([FakeResponse(429)])
        monkeypatch.setattr(requests, "post", transport)

        with pytest.raises(requests.HTTPError):
            http_post(_URL, retries=3, base_delay=0.5)

        assert sleeps == [0.5, 1.0, 2.0]

    def test_retries_connection_errors(self, monkeypatch, sleeps, no_network):
        transport = ScriptedTransport([requests.ConnectionError("down"), FakeResponse(200)])
        monkeypatch.setattr(requests, "post", transport)

        resp = http_post(_URL, retries=2, base_delay=1.0)

        assert resp.status_code == 200
        assert sleeps == [1.0]

    def test_raises_timeout_after_exhausting_retries(self, monkeypatch, sleeps, no_network):
        transport = ScriptedTransport([requests.Timeout("slow")])
        monkeypatch.setattr(requests, "post", transport)

        with pytest.raises(requests.Timeout):
            http_post(_URL, retries=1, base_delay=0.0)

        assert len(transport.calls) == 2

    def test_non_retryable_exception_propagates_without_retry(self, monkeypatch,
                                                              sleeps, no_network):
        transport = ScriptedTransport([requests.TooManyRedirects("loop")])
        monkeypatch.setattr(requests, "post", transport)

        with pytest.raises(requests.TooManyRedirects):
            http_post(_URL, retries=3)

        assert len(transport.calls) == 1

    def test_raises_runtime_error_when_no_attempt_is_configured(self, monkeypatch,
                                                                sleeps, no_network):
        transport = ScriptedTransport([FakeResponse(200)])
        monkeypatch.setattr(requests, "post", transport)

        with pytest.raises(RuntimeError, match="http_post failed"):
            http_post(_URL, retries=-1)

        assert transport.calls == []
        assert sleeps == []

    def test_get_and_post_do_not_interfere(self, monkeypatch, sleeps, no_network):
        get_transport = ScriptedTransport([FakeResponse(200, "got")])
        post_transport = ScriptedTransport([FakeResponse(201, "posted")])
        monkeypatch.setattr(requests, "get", get_transport)
        monkeypatch.setattr(requests, "post", post_transport)

        assert http_get(_URL).text == "got"
        assert http_post(_URL).text == "posted"
        assert len(get_transport.calls) == 1
        assert len(post_transport.calls) == 1
