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
import secrets
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


# Short codes are stored, not packed. 8 chars of url-safe base64 is ~48 bits of
# randomness: guessing one inside its two-week life is not a realistic attack, and
# a stored code can be revoked, which a self-contained token never can.
SHORT_CODE_BYTES = 6


def make_short_code(client_id: str, *, ttl: int = DEFAULT_TTL_SECONDS) -> str:
    """A short code standing for a client's contract, recorded in DynamoDB."""
    from . import idempotency

    code = _b64(secrets.token_bytes(SHORT_CODE_BYTES))
    store = idempotency._store()  # same table; a link is a short-lived record too
    key = f"signlink:{code}"
    # claim() writes with a TTL and refuses to overwrite — which doubles as
    # collision protection, however unlikely.
    if not store.claim(key, ttl):
        raise SigningError("could not allocate a signing code; try again")
    _remember(key, client_id, ttl)
    return code


def _remember(key: str, client_id: str, ttl: int) -> None:
    """Attach the client id to a stored code."""
    from . import config as _config
    from . import idempotency

    table = _config.get("IDEMPOTENCY_TABLE")
    if not table:
        # File store (local/dev): keep the mapping beside the claim.
        store = idempotency._FileStore()
        data = store._read()
        data.setdefault(key, {})["client_id"] = client_id
        store._write(data)
        return

    import boto3

    boto3.resource(
        "dynamodb", region_name=_config.get("AWS_REGION", "eu-central-1")
    ).Table(table).update_item(
        Key={"pk": key},
        UpdateExpression="SET client_id = :c",
        ExpressionAttributeValues={":c": client_id},
    )


def read_short_code(code: str) -> str:
    """The client id behind a short code, or raise."""
    from . import config as _config
    from . import idempotency

    key = f"signlink:{code}"
    table = _config.get("IDEMPOTENCY_TABLE")
    if not table:
        entry = idempotency._FileStore()._read().get(key) or {}
        client_id = entry.get("client_id")
        if not client_id:
            raise SigningError("this signing link is not valid")
        if entry.get("expires_at", 0) < time.time():
            raise SigningError("this signing link has expired")
        return str(client_id)

    import boto3

    item = (
        boto3.resource("dynamodb", region_name=_config.get("AWS_REGION", "eu-central-1"))
        .Table(table)
        .get_item(Key={"pk": key})
        .get("Item")
    )
    if not item or not item.get("client_id"):
        raise SigningError("this signing link is not valid")
    if float(item.get("expires_at", 0)) < time.time():
        # DynamoDB's TTL sweep is lazy — check rather than trust it.
        raise SigningError("this signing link has expired")
    return str(item["client_id"])


# ------------------------------------------------------- pending signatures

# How long a quote may sit unsigned before we stop chasing it. After the last
# reminder there is no point holding the record.
PENDING_TTL_SECONDS = 21 * 24 * 60 * 60


def mark_pending(client_id: str) -> None:
    """Record that a client has an unsigned contract, for the reminder job.

    Stores when the quote went out and how many reminders have been sent. Keyed by
    client, so re-sending a quote resets the clock rather than stacking records.
    """
    from . import idempotency

    previous = get_pending(client_id) or {}
    record: dict[str, Any] = {
        "client_id": client_id,
        "issued_at": int(time.time()),
        "reminders_sent": 0,
    }
    # A re-sent contract keeps its follow-up task (reminder_tasks moves its date),
    # rather than opening a second one next to it.
    if previous.get("reminder_task_id"):
        record["reminder_task_id"] = previous["reminder_task_id"]
    _put_pending(f"signpending:{client_id}", record)


def get_pending(client_id: str) -> Optional[dict[str, Any]]:
    return _get_pending(f"signpending:{client_id}")


def bump_reminders(client_id: str, count: int) -> None:
    rec = get_pending(client_id) or {"client_id": client_id, "issued_at": int(time.time())}
    rec["reminders_sent"] = count
    _put_pending(f"signpending:{client_id}", rec)


def set_reminder_task(client_id: str, task_id: str) -> None:
    """Remember the ClickUp task that holds this client's follow-up email."""
    rec = get_pending(client_id)
    if rec is not None:
        rec["reminder_task_id"] = task_id
        _put_pending(f"signpending:{client_id}", rec)


def clear_pending(client_id: str) -> None:
    """Called when a client signs — there is nothing left to remind about."""
    _delete_pending(f"signpending:{client_id}")


# ---------------------------------------------------- pending questionnaires

# The same three calls again for the strategy questionnaire onboarding emails.
# Kept as its own key rather than a flag on the signing record: a client can be
# chased for a signature and, weeks later, for a questionnaire, and one clearing
# the other would silence a chase that had not finished.


def mark_questionnaire_pending(client_id: str) -> None:
    """Record that a client owes us questionnaire answers, for the chase job."""
    _put_pending(f"questpending:{client_id}", {
        "client_id": client_id,
        "issued_at": int(time.time()),
        "reminders_sent": 0,
    })


def get_questionnaire_pending(client_id: str) -> Optional[dict[str, Any]]:
    return _get_pending(f"questpending:{client_id}")


def bump_questionnaire_reminders(client_id: str, count: int) -> None:
    rec = (get_questionnaire_pending(client_id)
           or {"client_id": client_id, "issued_at": int(time.time())})
    rec["reminders_sent"] = count
    _put_pending(f"questpending:{client_id}", rec)


def clear_questionnaire_pending(client_id: str) -> None:
    """Called when the questionnaire is submitted — nothing left to chase."""
    _delete_pending(f"questpending:{client_id}")


def _put_pending(key: str, value: dict[str, Any]) -> None:
    table = config.get("IDEMPOTENCY_TABLE")
    if not table:
        from . import idempotency

        store = idempotency._FileStore()
        data = store._read()
        data[key] = {**value, "expires_at": time.time() + PENDING_TTL_SECONDS}
        store._write(data)
        return
    import boto3

    boto3.resource("dynamodb", region_name=config.get("AWS_REGION", "eu-central-1")) \
        .Table(table).put_item(Item={
            "pk": key,
            "expires_at": int(time.time()) + PENDING_TTL_SECONDS,
            **value,
        })


def _get_pending(key: str) -> Optional[dict[str, Any]]:
    table = config.get("IDEMPOTENCY_TABLE")
    if not table:
        from . import idempotency

        return idempotency._FileStore()._read().get(key)
    import boto3

    item = boto3.resource("dynamodb", region_name=config.get("AWS_REGION", "eu-central-1")) \
        .Table(table).get_item(Key={"pk": key}).get("Item")
    if not item:
        return None
    return {k: (int(v) if hasattr(v, "to_integral_value") else v) for k, v in item.items()}


def _delete_pending(key: str) -> None:
    table = config.get("IDEMPOTENCY_TABLE")
    if not table:
        from . import idempotency

        store = idempotency._FileStore()
        data = store._read()
        data.pop(key, None)
        store._write(data)
        return
    import boto3

    boto3.resource("dynamodb", region_name=config.get("AWS_REGION", "eu-central-1")) \
        .Table(table).delete_item(Key={"pk": key})


def resolve(token: str) -> str:
    """The client id behind either link form.

    Long self-contained tokens carry a "."; short codes never do. Both are
    accepted so links already sent to clients keep working.
    """
    if "." in token:
        return read_token(token)
    return read_short_code(token)


def _link_token(client_id: str, ttl: int, short: bool) -> str:
    """A short code when the server's code table is configured, else a long
    self-contained token.

    A short code only exists where it was stored. Minted on a laptop without
    IDEMPOTENCY_TABLE it lands in a local file, and the Lambda that serves the
    page has never heard of it — the client opens a dead link. A long token
    carries everything and verifies anywhere with the same secret, so it is the
    safe choice whenever the code cannot reach the server.
    """
    if short and _codes_reach_the_server():
        return make_short_code(client_id, ttl=ttl)
    return make_token(client_id, ttl=ttl)


def _codes_reach_the_server() -> bool:
    """True when a short code will be readable by the Lambda that serves the link:
    it is stored in DynamoDB, or the link points at this machine (local dev)."""
    if config.get("IDEMPOTENCY_TABLE"):
        return True
    base = config.get("SIGN_BASE_URL") or config.get("AWS_API_BASE_URL") or ""
    host = base.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    return host in ("localhost", "127.0.0.1")


def sign_url(client_id: str, *, ttl: int = DEFAULT_TTL_SECONDS, short: bool = True) -> str:
    """The URL to send a client.

    Short by default: this goes in an email and a WhatsApp message, where a
    150-character URL of opaque base64 looks like something you should not click.
    """
    base = config.get("SIGN_BASE_URL") or config.get("AWS_API_BASE_URL")
    if not base:
        raise SigningError(
            "SIGN_BASE_URL is not set - there is nowhere for the client to open "
            "the contract. Use the deployed API base, e.g. "
            "https://xxx.execute-api.eu-central-1.amazonaws.com/dev"
        )
    token = _link_token(client_id, ttl, short)
    return f"{base.rstrip('/')}/sign?t={token}"


def questionnaire_url(client_id: str, *, ttl: int = DEFAULT_TTL_SECONDS) -> str:
    """The link to the client's strategy questionnaire. Same token scheme as
    signing — it carries the client id — pointed at the questionnaire page."""
    base = config.get("SIGN_BASE_URL") or config.get("AWS_API_BASE_URL")
    if not base:
        raise SigningError("SIGN_BASE_URL is not set - nowhere to host the form.")
    return f"{base.rstrip('/')}/questionnaire?t={_link_token(client_id, ttl, True)}"


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


def local_time(iso: str, *, seconds: bool = False) -> str:
    """``2026-09-24T09:36:22Z`` as ``24.09.2026, 12:36`` in Israel time."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Jerusalem"))
    except ValueError:
        return str(iso)
    return dt.strftime("%d.%m.%Y, %H:%M:%S" if seconds else "%d.%m.%Y, %H:%M")


def audit_html(record: dict[str, Any], *, client_name: str = "") -> str:
    """The audit trail, rendered to sit inside the signed PDF itself.

    Kept in the document rather than only in a log: the PDF is what gets emailed,
    filed and produced in an argument, and evidence stored somewhere else is
    evidence nobody has to hand.
    """
    import html as _html

    def esc(v: Any) -> str:
        return _html.escape(str(v))

    def ltr(v: Any) -> str:  # ids, times, addresses: left to right inside the Hebrew
        return f'<bdi dir="ltr">{esc(v)}</bdi>'

    signed = str(record.get("signed_at") or "")
    when = local_time(signed, seconds=True)
    cid = ltr(record["client_id"])
    rows = [
        ("הלקוח", f"{esc(client_name)} (מזהה {cid})" if client_name else cid),
        ("מועד החתימה", f"{ltr(when)} (שעון ישראל)" if when != signed else ltr(signed)),
        ("מועד מדויק (UTC)", ltr(signed)),
        ("כתובת IP", ltr(record["ip"]) if record.get("ip") else "לא נרשמה"),
        ("דפדפן", ltr(record["user_agent"]) if record.get("user_agent") else "לא נרשם"),
    ]
    cells = "".join(f"<tr><th>{esc(k)}</th><td>{v}</td></tr>" for k, v in rows)
    return f"""
<section class="audit" dir="rtl">
  <h2>אישור חתימה אלקטרונית</h2>
  <table>{cells}
    <tr><th>טביעת אצבע (SHA-256)</th><td><code dir="ltr">{esc(record['contract_sha256'])}</code></td></tr>
  </table>
  <p>טביעת האצבע מזהה באופן חד־ערכי את נוסח ההסכם שהוצג לחותם במעמד החתימה, כולל הפרטים והחתימה.</p>
</section>
"""
