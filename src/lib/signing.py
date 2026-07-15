"""Signing links and the evidence that a signature is real.

A signing link is a URL a client opens in a browser. It is unauthenticated by
nature — the client has no account — so the link itself is the credential. It is
therefore signed with ``SIGN_LINK_SECRET`` and carries an expiry: guessing a
client id must not be enough to open, alter, or sign someone's contract.

**On evidence.** We replaced Fillout, so nobody neutral is attesting to this
signature any more. If a client ever says "I never signed that", Dror's answer is
whatever we recorded. So each signature stores:

  * the exact contract HTML that was on screen, hashed — proving the terms
  * when it was signed, to the second, in UTC
  * the signer's IP and user agent
  * the drawn signature image itself

Under Israeli law (חוק חתימה אלקטרונית) a plain electronic signature is
admissible; what makes it persuasive is the audit trail around it. That is what
this module exists to produce.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from typing import Any, Optional

from . import config

# A quote is not an open-ended offer, and a link that works forever is a link that
# leaks. Two weeks is long enough for a client to think it over.
DEFAULT_TTL_SECONDS = 14 * 24 * 60 * 60


class SigningError(RuntimeError):
    """Raised when a signing link is invalid, expired, or tampered with."""


def _secret() -> str:
    secret = config.get("SIGN_LINK_SECRET")
    if not secret:
        # Fail closed: without a secret every link would be forgeable, which is
        # worse than no signing page at all.
        raise SigningError(
            "SIGN_LINK_SECRET is not set; refusing to issue or accept signing "
            'links. Generate one: python -c "import secrets;'
            'print(secrets.token_urlsafe(48))"'
        )
    return secret


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(client_id: str, *, ttl: int = DEFAULT_TTL_SECONDS) -> str:
    """A signed, expiring token identifying one client's contract."""
    payload = {"c": client_id, "e": int(time.time()) + ttl}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_secret().encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_token(token: str) -> str:
    """Return the client id in a token, or raise. Verifies before trusting."""
    try:
        body, sig = token.split(".", 1)
    except ValueError as exc:
        raise SigningError("malformed signing link") from exc

    expected = _b64(hmac.new(_secret().encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        raise SigningError("this signing link is not valid")

    try:
        payload = json.loads(_unb64(body))
    except Exception as exc:  # noqa: BLE001
        raise SigningError("malformed signing link") from exc

    if int(payload.get("e", 0)) < time.time():
        raise SigningError("this signing link has expired")
    return str(payload["c"])


def sign_url(client_id: str, *, ttl: int = DEFAULT_TTL_SECONDS) -> str:
    """The full URL to send a client."""
    base = config.get("SIGN_BASE_URL") or config.get("AWS_API_BASE_URL")
    if not base:
        raise SigningError(
            "SIGN_BASE_URL is not set — there is nowhere for the client to open "
            "the contract. Use the deployed API base, e.g. "
            "https://xxx.execute-api.eu-central-1.amazonaws.com/dev"
        )
    return f"{base.rstrip('/')}/sign?t={make_token(client_id, ttl=ttl)}"


_DATA_URL = re.compile(r"^data:image/png;base64,([A-Za-z0-9+/=]+)$")


def decode_signature(data_url: str) -> bytes:
    """Validate and decode a drawn signature.

    Strict about the shape: this string is inserted into a document and stored as
    evidence, so it must be a PNG we produced from a canvas and nothing else.
    """
    match = _DATA_URL.match((data_url or "").strip())
    if not match:
        raise SigningError("the signature is missing or not a PNG image")
    try:
        raw = base64.b64decode(match.group(1), validate=True)
    except Exception as exc:  # noqa: BLE001
        raise SigningError("the signature image is corrupt") from exc
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise SigningError("the signature image is not a PNG")
    # An empty canvas still produces a valid PNG. Reject it: an unsigned contract
    # must not be able to masquerade as a signed one.
    if len(raw) < 1000:
        raise SigningError("the signature appears to be blank")
    return raw


def audit_record(
    client_id: str,
    contract_html: str,
    *,
    ip: str = "",
    user_agent: str = "",
    signed_at: Optional[str] = None,
) -> dict[str, Any]:
    """The evidence stored alongside a signature.

    ``contract_sha256`` is the hash of exactly what the client saw. If the template
    later changes, this still proves which terms were on screen at signing — which
    is the whole question in a dispute.
    """
    from datetime import datetime, timezone

    return {
        "client_id": client_id,
        "signed_at": signed_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "ip": ip,
        "user_agent": user_agent[:300],
        "contract_sha256": hashlib.sha256(contract_html.encode("utf-8")).hexdigest(),
        "contract_bytes": len(contract_html.encode("utf-8")),
    }


def audit_html(record: dict[str, Any]) -> str:
    """The audit trail, rendered to sit inside the signed PDF itself.

    Kept in the document rather than only in a log: the PDF is what gets emailed,
    filed and produced in an argument, and evidence stored somewhere else is
    evidence nobody has to hand.
    """
    import html as _html

    def esc(v: Any) -> str:
        return _html.escape(str(v))

    return f"""
<hr>
<div class="audit" dir="rtl" style="font-size:11px;color:#555;line-height:1.6">
  <p><strong>אישור חתימה אלקטרונית</strong></p>
  <p>
    מזהה לקוח: {esc(record['client_id'])}<br>
    נחתם בתאריך (UTC): {esc(record['signed_at'])}<br>
    כתובת IP של החותם: {esc(record['ip'] or 'לא נרשמה')}<br>
    דפדפן: {esc(record['user_agent'] or 'לא נרשם')}<br>
    טביעת אצבע של המסמך (SHA-256): <code>{esc(record['contract_sha256'])}</code>
  </p>
  <p>טביעת האצבע מזהה באופן חד־ערכי את נוסח ההסכם שהוצג לחותם במעמד החתימה.</p>
</div>
"""
