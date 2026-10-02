"""Configuration: env vars + .rchrc.json + defaults."""
import json
import os
from pathlib import Path

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def _num_env(name, fallback):
    v = os.environ.get(name)
    if v is None or v == "":
        return fallback
    try:
        return float(v) if "." in v else int(v)
    except ValueError:
        return fallback


def _str_env(name, fallback):
    v = os.environ.get(name)
    return v if v else fallback


def _load_rcrc():
    candidates = [
        Path.cwd() / ".rchrc.json",
        Path.home() / ".rchrc.json",
    ]
    for p in candidates:
        try:
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


_rc = _load_rcrc()


def _coerce(value, cast, fallback):
    """Apply ``cast`` to a config value, falling back when it does not fit.

    The config file is user-owned, so a non-numeric value where a number is
    expected is an ordinary typo rather than an exceptional condition. This
    keeps a bad ``.rchrc.json`` from raising out of module import, which would
    otherwise take down every entrypoint including ``--version``.
    """
    try:
        return cast(value)
    except (TypeError, ValueError):
        return fallback


def _get(name, env_name, fallback):
    v = os.environ.get(env_name)
    if v:
        return v
    v = _rc.get(name)
    if v is not None and v != "":
        return v
    return fallback


def _get_num(name, env_name, fallback):
    """Read a numeric setting, coercing strings and tolerating bad values."""
    return _coerce(_get(name, env_name, fallback), float, fallback)


def _get_int(name, env_name, fallback):
    return _coerce(_get(name, env_name, fallback), int, fallback)


def _get_str(name, env_name, fallback):
    """Read a string setting, ignoring values that are not text."""
    v = _get(name, env_name, fallback)
    return v if isinstance(v, str) else fallback


CONFIG = {
    "sleep_requests": _get_num("sleep_requests", "RCH_SLEEP_REQUESTS", 0.5),
    "sleep_interval": _get_num("sleep_interval", "RCH_SLEEP_INTERVAL", 0.5),
    "max_sleep_interval": _get_num("max_sleep_interval", "RCH_MAX_SLEEP_INTERVAL", 2),
    "retries": _get_int("retries", "RCH_RETRIES", 3),
    "user_agent": _get_str("user_agent", "RCH_USER_AGENT", BROWSER_UA),
    "proxy": _get_str("proxy", "RCH_PROXY", ""),
    "cookies": _get_str("cookies", "RCH_COOKIES", ""),
    "limit_rate": _get_str("limit_rate", "RCH_LIMIT_RATE", ""),
    "concurrency": _get_int("concurrency", "RCH_CONCURRENCY", 2),
    "quality": _get_str("quality", "RCH_QUALITY", "720p"),
}


def build_ytdlp_args(base_args, *, no_sleep=False, cookies=""):
    """Append mitigation flags to yt-dlp args."""
    c = CONFIG
    extra = []
    if not no_sleep:
        if c["sleep_requests"] > 0:
            extra += ["--sleep-requests", str(c["sleep_requests"])]
        if c["sleep_interval"] > 0:
            extra += ["--sleep-interval", str(c["sleep_interval"])]
        if c["max_sleep_interval"] > 0:
            extra += ["--max-sleep-interval", str(c["max_sleep_interval"])]
    if c["retries"] > 0:
        extra += ["--retries", str(c["retries"])]
    if c["user_agent"]:
        extra += ["--user-agent", c["user_agent"]]
    if c["proxy"]:
        extra += ["--proxy", c["proxy"]]
    if c["limit_rate"]:
        extra += ["--limit-rate", c["limit_rate"]]
    ck = cookies or c["cookies"]
    if ck:
        extra += ["--cookies-from-browser", ck]
    return extra + list(base_args)
