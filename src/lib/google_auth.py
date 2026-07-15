"""Google authentication — service account with domain-wide delegation.

Google access tokens live for an hour, so a pasted ``GOOGLE_ACCESS_TOKEN`` stops
working before the first monthly billing run ever fires. Instead we hold a service
account's key and mint tokens on demand.

**Why impersonation.** A service account is not a person: it has no Contacts, and
any Drive file it creates is owned by *it*, not by Dror — invisible in his Drive,
and gone if the key is ever deleted. So it acts *as* Dror, via domain-wide
delegation. That is what ``GOOGLE_IMPERSONATE_SUBJECT`` is for, and it requires a
Google Workspace domain; a personal @gmail.com account cannot do it.

The key can be given as a file path (``GOOGLE_SERVICE_ACCOUNT_FILE``) or as the
JSON itself (``GOOGLE_SERVICE_ACCOUNT_JSON``). Lambda has no useful filesystem, so
the JSON form is what runs in production.

Tokens are cached until shortly before expiry: onboarding makes several Google
calls in a row and minting a token for each would be wasteful and rate-limited.

See docs/GOOGLE_SETUP.md for the console walkthrough.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from . import config

# Only what the automations actually do. Keep this list minimal: domain-wide
# delegation grants these scopes over the whole Workspace, so an extra scope here
# is real, standing access to Dror's data that nothing needs.
SCOPES = [
    "https://www.googleapis.com/auth/contacts",  # save a lead's phone
    "https://www.googleapis.com/auth/drive",  # client folders, templates, PDFs
    "https://www.googleapis.com/auth/forms.responses.readonly",  # questionnaire
]

_cache: dict[str, Any] = {"token": None, "expires_at": 0.0}


class GoogleAuthError(RuntimeError):
    """Raised when Google credentials are missing or cannot be used."""


def _key_info() -> dict[str, Any]:
    """The service-account key, from the JSON env var or the file path."""
    raw = config.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GoogleAuthError(
                "GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON. Paste the whole key "
                "file contents, including the braces."
            ) from exc

    path = config.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not path:
        raise GoogleAuthError(
            "No Google credentials. Set GOOGLE_SERVICE_ACCOUNT_JSON (the key file's "
            "contents) or GOOGLE_SERVICE_ACCOUNT_FILE (a path to it). "
            "See docs/GOOGLE_SETUP.md."
        )
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError as exc:
        raise GoogleAuthError(f"Service-account key not found at {path!r}.") from exc


def access_token(force_refresh: bool = False) -> str:
    """A valid Google access token, minted and cached as needed."""
    now = time.time()
    if not force_refresh and _cache["token"] and _cache["expires_at"] > now + 60:
        return str(_cache["token"])

    try:
        from google.oauth2 import service_account  # type: ignore[import-untyped]
        from google.auth.transport.requests import Request  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise GoogleAuthError(
            "The 'google-auth' package is required for live Google calls. "
            "Install it (pip install -r requirements.txt) or use --dry-run."
        ) from exc

    # Key first, then subject: that is the order they are set up in the guide, so
    # a half-configured deployment is told about the earlier missing piece rather
    # than the later one.
    info = _key_info()

    subject = config.get("GOOGLE_IMPERSONATE_SUBJECT")
    if not subject:
        raise GoogleAuthError(
            "GOOGLE_IMPERSONATE_SUBJECT is not set. The service account must act as "
            "a real Workspace user: it has no Contacts of its own, and Drive files "
            "it creates would be owned by it rather than by Dror. "
            "See docs/GOOGLE_SETUP.md."
        )
    try:
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES, subject=subject
        )
        creds.refresh(Request())
    except Exception as exc:  # noqa: BLE001 - surface Google's own message
        raise GoogleAuthError(
            f"Could not get a Google token as {subject!r}: {exc}\n"
            f"Usually this means domain-wide delegation is not set up for client id "
            f"{info.get('client_id')!r}, or a scope is missing. "
            f"See docs/GOOGLE_SETUP.md step 5."
        ) from exc

    _cache["token"] = creds.token
    _cache["expires_at"] = creds.expiry.timestamp() if creds.expiry else time.time() + 3000
    return str(creds.token)


def reset_cache() -> None:
    """Drop the cached token. For tests, and after a credentials change."""
    _cache["token"] = None
    _cache["expires_at"] = 0.0
