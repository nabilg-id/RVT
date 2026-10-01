"""HTTP client with retry + exponential backoff."""
import time

import requests

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def http_get(url, *, timeout=30, retries=3, base_delay=1.0, **kwargs):
    """GET with retry. Returns Response or raises after retries."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, timeout=timeout, **kwargs)
            if resp.status_code not in RETRYABLE_STATUS:
                return resp
            last_err = requests.HTTPError(f"HTTP {resp.status_code}")
        except (requests.ConnectionError, requests.Timeout) as e:
            last_err = e
        if attempt >= retries:
            break
        delay = base_delay * (2 ** attempt)
        time.sleep(delay)
    if last_err:
        raise last_err
    raise RuntimeError("http_get failed")


def http_post(url, *, timeout=30, retries=3, base_delay=1.0, **kwargs):
    """POST with retry."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(url, timeout=timeout, **kwargs)
            if resp.status_code not in RETRYABLE_STATUS:
                return resp
            last_err = requests.HTTPError(f"HTTP {resp.status_code}")
        except (requests.ConnectionError, requests.Timeout) as e:
            last_err = e
        if attempt >= retries:
            break
        delay = base_delay * (2 ** attempt)
        time.sleep(delay)
    if last_err:
        raise last_err
    raise RuntimeError("http_post failed")
