"""HTTP helper with retry/backoff.

Wraps ``requests`` (imported lazily so the dry-run path needs no third-party
packages) and retries on transient network errors and retryable status codes
(429 and 5xx). Live API clients call ``request()``; dry-run clients never do.
"""

from __future__ import annotations

from typing import Any

from .retry import RetryableError, retry

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class HttpError(RuntimeError):
    """Non-retryable HTTP error (4xx other than the retryable set)."""

    def __init__(self, status: int, url: str, body: str):
        super().__init__(f"HTTP {status} for {url}: {body[:500]}")
        self.status = status
        self.url = url
        self.body = body


def _requests():
    try:
        import requests  # imported lazily; only needed for live calls
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "The 'requests' package is required for live API calls. "
            "Install it (pip install -r requirements.txt) or use --dry-run."
        ) from exc
    return requests


@retry(attempts=4, base_delay=0.5, exceptions=(RetryableError,))
def request(method: str, url: str, *, timeout: float = 30.0, **kwargs: Any):
    """Perform an HTTP request with retry on transient failures.

    Raises :class:`RetryableError` (so the ``retry`` decorator retries) on
    network errors and retryable status codes, and :class:`HttpError` on
    non-retryable 4xx responses. Returns the ``requests.Response`` on success.
    """
    requests = _requests()
    try:
        resp = requests.request(method, url, timeout=timeout, **kwargs)
    except requests.exceptions.RequestException as exc:
        raise RetryableError(f"network error for {url}: {exc}") from exc

    if resp.status_code in RETRYABLE_STATUS:
        raise RetryableError(f"retryable HTTP {resp.status_code} for {url}")
    if resp.status_code >= 400:
        raise HttpError(resp.status_code, url, resp.text)
    return resp
