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


def _get(name, env_name, fallback):
    v = os.environ.get(env_name)
    if v:
        return v
    v = _rc.get(name)
    if v is not None and v != "":
        return v
    return fallback


CONFIG = {
    "sleep_requests": float(_get("sleep_requests", "RCH_SLEEP_REQUESTS", 0.5)),
    "sleep_interval": float(_get("sleep_interval", "RCH_SLEEP_INTERVAL", 0.5)),
    "max_sleep_interval": float(_get("max_sleep_interval", "RCH_MAX_SLEEP_INTERVAL", 2)),
    "retries": int(_get("retries", "RCH_RETRIES", 3)),
    "user_agent": _get("user_agent", "RCH_USER_AGENT", BROWSER_UA),
    "proxy": _get("proxy", "RCH_PROXY", ""),
    "cookies": _get("cookies", "RCH_COOKIES", ""),
    "limit_rate": _get("limit_rate", "RCH_LIMIT_RATE", ""),
    "concurrency": int(_get("concurrency", "RCH_CONCURRENCY", 2)),
    "quality": _get("quality", "RCH_QUALITY", "720p"),
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
