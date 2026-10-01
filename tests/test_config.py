"""Tests for rch.config — env vars, .rchrc.json layering, yt-dlp arg building.

``CONFIG`` is resolved once at import time, so the precedence tests reload the
module with a monkeypatched cwd/home and restore it on teardown. That keeps the
on-disk ``.rchrc.json`` of the developer out of the assertions.
"""
from __future__ import annotations

import importlib
import json

import pytest

from rch import config as config_mod
from rch.config import BROWSER_UA, CONFIG, _get, _num_env, _str_env, build_ytdlp_args

_CONFIG_ENV_VARS = (
    "RCH_SLEEP_REQUESTS",
    "RCH_SLEEP_INTERVAL",
    "RCH_MAX_SLEEP_INTERVAL",
    "RCH_RETRIES",
    "RCH_USER_AGENT",
    "RCH_PROXY",
    "RCH_COOKIES",
    "RCH_LIMIT_RATE",
    "RCH_CONCURRENCY",
    "RCH_QUALITY",
)


@pytest.fixture
def home_path(tmp_path):
    return tmp_path / "home"


@pytest.fixture
def workdir(tmp_path):
    """A pre-created cwd so ``monkeypatch.chdir`` succeeds on Windows."""
    d = tmp_path / "cwd"
    d.mkdir()
    return d


@pytest.fixture
def reload_config(monkeypatch, home_path):
    """Reload ``rch.config`` with a clean env + cwd/home, then restore it."""
    for name in _CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config_mod.Path, "home", staticmethod(lambda: home_path))
    reloaded = importlib.reload(config_mod)
    yield reloaded
    monkeypatch.undo()
    importlib.reload(config_mod)


def _write_rcrc(directory, payload):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ".rchrc.json").write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# _num_env — numeric env coercion
# ---------------------------------------------------------------------------


class TestNumEnv:
    def test_returns_fallback_when_unset(self, monkeypatch):
        monkeypatch.delenv("RCH_TEST_NUM", raising=False)

        assert _num_env("RCH_TEST_NUM", 3) == 3

    def test_returns_fallback_when_empty_string(self, monkeypatch):
        monkeypatch.setenv("RCH_TEST_NUM", "")

        assert _num_env("RCH_TEST_NUM", 3) == 3

    def test_parses_integer_string(self, monkeypatch):
        monkeypatch.setenv("RCH_TEST_NUM", "7")

        assert _num_env("RCH_TEST_NUM", 3) == 7
        assert isinstance(_num_env("RCH_TEST_NUM", 3), int)

    def test_parses_float_string(self, monkeypatch):
        monkeypatch.setenv("RCH_TEST_NUM", "0.25")

        value = _num_env("RCH_TEST_NUM", 3)

        assert value == 0.25
        assert isinstance(value, float)

    def test_falls_back_on_non_numeric_value(self, monkeypatch):
        monkeypatch.setenv("RCH_TEST_NUM", "abc")

        assert _num_env("RCH_TEST_NUM", 3) == 3

    def test_negative_integer_allowed(self, monkeypatch):
        monkeypatch.setenv("RCH_TEST_NUM", "-2")

        assert _num_env("RCH_TEST_NUM", 3) == -2


# ---------------------------------------------------------------------------
# _str_env — string env fallback
# ---------------------------------------------------------------------------


class TestStrEnv:
    def test_returns_fallback_when_unset(self, monkeypatch):
        monkeypatch.delenv("RCH_TEST_STR", raising=False)

        assert _str_env("RCH_TEST_STR", "default") == "default"

    def test_returns_env_value_when_set(self, monkeypatch):
        monkeypatch.setenv("RCH_TEST_STR", "chrome")

        assert _str_env("RCH_TEST_STR", "default") == "chrome"

    def test_empty_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("RCH_TEST_STR", "")

        assert _str_env("RCH_TEST_STR", "default") == "default"


# ---------------------------------------------------------------------------
# _load_rcrc — precedence of cwd over home
# ---------------------------------------------------------------------------


class TestLoadRcrc:
    def test_empty_when_no_file_present(self, reload_config, monkeypatch, workdir):
        monkeypatch.chdir(workdir)

        assert reload_config._load_rcrc() == {}

    def test_reads_from_cwd(self, reload_config, monkeypatch, workdir):
        monkeypatch.chdir(workdir)
        _write_rcrc(workdir, {"quality": "1080p"})

        assert reload_config._load_rcrc() == {"quality": "1080p"}

    def test_reads_from_home_when_cwd_absent(self, reload_config, monkeypatch, workdir, home_path):
        monkeypatch.chdir(workdir)
        _write_rcrc(home_path, {"quality": "480p"})

        assert reload_config._load_rcrc() == {"quality": "480p"}

    def test_cwd_wins_over_home(self, reload_config, monkeypatch, workdir, home_path):
        monkeypatch.chdir(workdir)
        _write_rcrc(workdir, {"quality": "1080p"})
        _write_rcrc(home_path, {"quality": "480p"})

        assert reload_config._load_rcrc() == {"quality": "1080p"}

    def test_corrupt_cwd_file_falls_through_to_home(self, reload_config, monkeypatch, workdir, home_path):
        monkeypatch.chdir(workdir)
        (workdir / ".rchrc.json").write_text("{not json", encoding="utf-8")
        _write_rcrc(home_path, {"quality": "480p"})

        assert reload_config._load_rcrc() == {"quality": "480p"}

    def test_corrupt_file_only_yields_empty(self, reload_config, monkeypatch, workdir):
        monkeypatch.chdir(workdir)
        (workdir / ".rchrc.json").write_text("[[[", encoding="utf-8")

        assert reload_config._load_rcrc() == {}


# ---------------------------------------------------------------------------
# _get — three-layer precedence
# ---------------------------------------------------------------------------


class TestGet:
    @pytest.fixture(autouse=True)
    def _rc(self, monkeypatch):
        monkeypatch.setattr(config_mod, "_rc", {})

    def test_env_wins_over_rcrc(self, monkeypatch):
        monkeypatch.setenv("RCH_TEST_GET", "from-env")
        monkeypatch.setattr(config_mod, "_rc", {"probe": "from-rcrc"})

        assert _get("probe", "RCH_TEST_GET", "fallback") == "from-env"

    def test_rcrc_used_when_env_absent(self, monkeypatch):
        monkeypatch.delenv("RCH_TEST_GET", raising=False)
        monkeypatch.setattr(config_mod, "_rc", {"probe": "from-rcrc"})

        assert _get("probe", "RCH_TEST_GET", "fallback") == "from-rcrc"

    def test_rcrc_empty_string_is_ignored(self, monkeypatch):
        monkeypatch.delenv("RCH_TEST_GET", raising=False)
        monkeypatch.setattr(config_mod, "_rc", {"probe": ""})

        assert _get("probe", "RCH_TEST_GET", "fallback") == "fallback"

    def test_falsy_but_present_rcrc_value_is_kept(self, monkeypatch):
        monkeypatch.delenv("RCH_TEST_GET", raising=False)
        monkeypatch.setattr(config_mod, "_rc", {"probe": 0})

        assert _get("probe", "RCH_TEST_GET", "fallback") == 0

    def test_fallback_when_nothing_set(self, monkeypatch):
        monkeypatch.delenv("RCH_TEST_GET", raising=False)

        assert _get("probe", "RCH_TEST_GET", "fallback") == "fallback"

    def test_empty_env_is_ignored(self, monkeypatch):
        monkeypatch.setenv("RCH_TEST_GET", "")
        monkeypatch.setattr(config_mod, "_rc", {"probe": "from-rcrc"})

        assert _get("probe", "RCH_TEST_GET", "fallback") == "from-rcrc"


# ---------------------------------------------------------------------------
# CONFIG — the resolved module-level dict
# ---------------------------------------------------------------------------


class TestConfigDict:
    def test_exposes_every_expected_key(self):
        assert set(CONFIG) == {
            "sleep_requests",
            "sleep_interval",
            "max_sleep_interval",
            "retries",
            "user_agent",
            "proxy",
            "cookies",
            "limit_rate",
            "concurrency",
            "quality",
        }

    def test_defaults_when_nothing_configured(self, reload_config, monkeypatch, workdir):
        monkeypatch.chdir(workdir)

        assert reload_config.CONFIG == {
            "sleep_requests": 0.5,
            "sleep_interval": 0.5,
            "max_sleep_interval": 2,
            "retries": 3,
            "user_agent": BROWSER_UA,
            "proxy": "",
            "cookies": "",
            "limit_rate": "",
            "concurrency": 2,
            "quality": "720p",
        }

    def test_numeric_keys_are_typed_from_env(self, reload_config, monkeypatch, workdir):
        monkeypatch.chdir(workdir)
        monkeypatch.setenv("RCH_RETRIES", "9")
        monkeypatch.setenv("RCH_SLEEP_REQUESTS", "1.5")
        monkeypatch.setenv("RCH_CONCURRENCY", "6")

        reloaded = importlib.reload(reload_config)

        assert reloaded.CONFIG["retries"] == 9
        assert isinstance(reloaded.CONFIG["retries"], int)
        assert reloaded.CONFIG["sleep_requests"] == 1.5
        assert reloaded.CONFIG["concurrency"] == 6

    def test_rcrc_feeds_config_when_env_absent(self, reload_config, monkeypatch, workdir):
        monkeypatch.chdir(workdir)
        _write_rcrc(workdir, {"quality": "480p", "concurrency": 4})

        reloaded = importlib.reload(reload_config)

        assert reloaded.CONFIG["quality"] == "480p"
        assert reloaded.CONFIG["concurrency"] == 4


# ---------------------------------------------------------------------------
# build_ytdlp_args — mitigation flags
# ---------------------------------------------------------------------------


def _cfg(monkeypatch, **overrides):
    base = dict(CONFIG)
    base.update(overrides)
    monkeypatch.setattr(config_mod, "CONFIG", base)
    return base


class TestBuildYtdlpArgs:
    def test_sleep_flags_present_by_default(self, monkeypatch):
        _cfg(monkeypatch)

        args = build_ytdlp_args(["-f", "best"])

        assert "--sleep-requests" in args
        assert "--sleep-interval" in args
        assert "--max-sleep-interval" in args
        assert "--retries" in args

    def test_no_sleep_omits_sleep_flags(self, monkeypatch):
        _cfg(monkeypatch)

        args = build_ytdlp_args(["-f", "best"], no_sleep=True)

        assert "--sleep-requests" not in args
        assert "--sleep-interval" not in args
        assert "--max-sleep-interval" not in args
        assert "--retries" in args

    def test_zero_sleep_values_are_skipped(self, monkeypatch):
        _cfg(monkeypatch, sleep_requests=0, sleep_interval=0, max_sleep_interval=0)

        args = build_ytdlp_args(["-f", "best"])

        assert "--sleep-requests" not in args
        assert "--sleep-interval" not in args
        assert "--max-sleep-interval" not in args

    def test_zero_retries_skips_retries_flag(self, monkeypatch):
        _cfg(monkeypatch, retries=0)

        assert "--retries" not in build_ytdlp_args(["-f", "best"])

    def test_numeric_flags_render_as_strings(self, monkeypatch):
        _cfg(monkeypatch, sleep_requests=0.5, retries=3)

        args = build_ytdlp_args(["-f", "best"])

        assert args[args.index("--sleep-requests") + 1] == "0.5"
        assert args[args.index("--retries") + 1] == "3"

    def test_user_agent_flag(self, monkeypatch):
        _cfg(monkeypatch, user_agent="UA/1.0")

        args = build_ytdlp_args([])

        assert args[args.index("--user-agent") + 1] == "UA/1.0"

    def test_blank_user_agent_omits_flag(self, monkeypatch):
        _cfg(monkeypatch, user_agent="")

        assert "--user-agent" not in build_ytdlp_args([])

    def test_proxy_flag(self, monkeypatch):
        _cfg(monkeypatch, proxy="http://127.0.0.1:8080")

        args = build_ytdlp_args([])

        assert args[args.index("--proxy") + 1] == "http://127.0.0.1:8080"

    def test_blank_proxy_omits_flag(self, monkeypatch):
        _cfg(monkeypatch, proxy="")

        assert "--proxy" not in build_ytdlp_args([])

    def test_limit_rate_flag(self, monkeypatch):
        _cfg(monkeypatch, limit_rate="2M")

        args = build_ytdlp_args([])

        assert args[args.index("--limit-rate") + 1] == "2M"

    def test_blank_limit_rate_omits_flag(self, monkeypatch):
        _cfg(monkeypatch, limit_rate="")

        assert "--limit-rate" not in build_ytdlp_args([])

    def test_explicit_cookies_argument_wins_over_config(self, monkeypatch):
        _cfg(monkeypatch, cookies="firefox")

        args = build_ytdlp_args([], cookies="chrome")

        assert args[args.index("--cookies-from-browser") + 1] == "chrome"

    def test_config_cookies_used_when_argument_empty(self, monkeypatch):
        _cfg(monkeypatch, cookies="edge")

        args = build_ytdlp_args([], cookies="")

        assert args[args.index("--cookies-from-browser") + 1] == "edge"

    def test_no_cookie_flag_when_both_empty(self, monkeypatch):
        _cfg(monkeypatch, cookies="")

        assert "--cookies-from-browser" not in build_ytdlp_args([])

    def test_base_args_are_preserved_in_order(self, monkeypatch):
        _cfg(monkeypatch, sleep_requests=0, sleep_interval=0, max_sleep_interval=0,
             retries=0, user_agent="", proxy="", limit_rate="", cookies="")

        args = build_ytdlp_args(["-f", "bv+ba", "-o", "out.%(ext)s"])

        assert args == ["-f", "bv+ba", "-o", "out.%(ext)s"]

    def test_base_args_are_not_mutated(self, monkeypatch):
        _cfg(monkeypatch)
        base = ["-f", "best"]

        build_ytdlp_args(base)

        assert base == ["-f", "best"]

    def test_empty_base_args_yields_only_extras(self, monkeypatch):
        _cfg(monkeypatch, sleep_requests=0, sleep_interval=0, max_sleep_interval=0,
             retries=0, user_agent="", proxy="", limit_rate="", cookies="")

        assert build_ytdlp_args([]) == []

    def test_extra_args_precede_base_args(self, monkeypatch):
        _cfg(monkeypatch, sleep_requests=0, sleep_interval=0, max_sleep_interval=0,
             retries=0, user_agent="", proxy="", limit_rate="", cookies="")

        args = build_ytdlp_args(["FIRST", "SECOND"])

        assert args == ["FIRST", "SECOND"]