"""Tests for the signing links, the signature capture, and the audit trail.

We replaced Fillout, so no neutral third party attests to these signatures any
more — Dror's evidence is whatever we record. These tests care about the two ways
that goes wrong: a link that lets the wrong person sign, and a signature we cannot
later prove was against these terms.
"""

from __future__ import annotations

import base64
import struct
import zlib

import pytest

from src.lib import signing


@pytest.fixture(autouse=True)
def _secret(monkeypatch, tmp_path):
    monkeypatch.setenv("SIGN_LINK_SECRET", "test-secret-for-links")
    monkeypatch.setenv("SIGN_BASE_URL", "https://sign.example/dev")
    monkeypatch.setenv("IDEMPOTENCY_PATH", str(tmp_path / "i.json"))
    monkeypatch.delenv("IDEMPOTENCY_TABLE", raising=False)
    yield


def make_png(w: int = 400, h: int = 120) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes((i * 7 + j) % 256 for j in range(w * 3)) for i in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


# ------------------------------------------------------------------- links


def test_token_round_trips():
    assert signing.read_token(signing.make_token("c1")) == "c1"


def test_a_tampered_token_is_rejected():
    # The attack: swap in another client's id to read or sign their contract.
    token = signing.make_token("c1")
    body, sig = token.split(".", 1)
    evil = signing._b64(b'{"c":"c2","e":9999999999}')
    with pytest.raises(signing.SigningError):
        signing.read_token(f"{evil}.{sig}")


def test_an_expired_link_is_rejected():
    with pytest.raises(signing.SigningError) as exc:
        signing.read_token(signing.make_token("c1", ttl=-1))
    assert "expired" in str(exc.value)


def test_a_link_signed_with_another_secret_is_rejected(monkeypatch):
    token = signing.make_token("c1")
    monkeypatch.setenv("SIGN_LINK_SECRET", "a-different-secret")
    with pytest.raises(signing.SigningError):
        signing.read_token(token)


@pytest.mark.parametrize("junk", ["", "nonsense", "a.b.c", "onlybody"])
def test_malformed_tokens_are_rejected(junk):
    with pytest.raises(signing.SigningError):
        signing.read_token(junk)


def test_no_secret_fails_closed(monkeypatch):
    # Without a secret every link is forgeable — worse than no signing page.
    monkeypatch.delenv("SIGN_LINK_SECRET", raising=False)
    with pytest.raises(signing.SigningError):
        signing.make_token("c1")


def test_sign_url_says_where_to_go():
    url = signing.sign_url("c1")
    assert url.startswith("https://sign.example/dev/sign?t=")


def test_sign_url_without_a_base_explains_itself(monkeypatch):
    monkeypatch.delenv("SIGN_BASE_URL", raising=False)
    monkeypatch.delenv("AWS_API_BASE_URL", raising=False)
    with pytest.raises(signing.SigningError) as exc:
        signing.sign_url("c1")
    assert "SIGN_BASE_URL" in str(exc.value)


# -------------------------------------------------------------- signatures


def test_a_drawn_signature_decodes():
    png = make_png()
    assert signing.decode_signature(data_url(png)) == png


def test_a_blank_canvas_is_not_a_signature():
    # An untouched canvas still produces a valid PNG. An unsigned contract must
    # never be able to masquerade as a signed one.
    tiny = make_png(2, 2)
    with pytest.raises(signing.SigningError) as exc:
        signing.decode_signature(data_url(tiny))
    assert "blank" in str(exc.value)


@pytest.mark.parametrize("bad", [
    "",
    "data:image/jpeg;base64,AAAA",           # only PNG from our canvas
    "data:image/png;base64,!!!not-base64!!!",
    "javascript:alert(1)",
    "https://evil.example/sig.png",           # must not fetch a remote image
])
def test_anything_that_is_not_our_png_is_rejected(bad):
    with pytest.raises(signing.SigningError):
        signing.decode_signature(bad)


def test_a_png_header_is_required_not_just_base64():
    fake = base64.b64encode(b"x" * 2000).decode()
    with pytest.raises(signing.SigningError):
        signing.decode_signature(f"data:image/png;base64,{fake}")


# ------------------------------------------------------------------ audit


def test_the_audit_hashes_the_exact_terms_shown():
    a = signing.audit_record("c1", "<p>סך של 4,900 ₪</p>", ip="1.2.3.4", user_agent="UA")
    b = signing.audit_record("c1", "<p>סך של 4,900 ₪</p>", ip="9.9.9.9", user_agent="other")
    # Same terms -> same fingerprint, whoever signed and from where.
    assert a["contract_sha256"] == b["contract_sha256"]

    c = signing.audit_record("c1", "<p>סך של 9,900 ₪</p>")
    assert c["contract_sha256"] != a["contract_sha256"], "changed terms must change the hash"


def test_the_audit_captures_who_and_when():
    a = signing.audit_record("c1", "terms", ip="203.0.113.7", user_agent="Mozilla/5.0")
    assert a["client_id"] == "c1"
    assert a["ip"] == "203.0.113.7"
    assert a["user_agent"] == "Mozilla/5.0"
    assert a["signed_at"].endswith("Z")


def test_a_long_user_agent_cannot_bloat_the_record():
    a = signing.audit_record("c1", "terms", user_agent="x" * 5000)
    assert len(a["user_agent"]) <= 300


def test_the_audit_travels_inside_the_document():
    # Evidence filed somewhere else is evidence nobody has to hand in an argument.
    a = signing.audit_record("c1", "terms", ip="1.2.3.4", user_agent="UA")
    out = signing.audit_html(a)
    assert a["contract_sha256"] in out
    assert "1.2.3.4" in out
    assert "אישור חתימה אלקטרונית" in out


def test_the_audit_escapes_what_it_records():
    # The user agent is attacker-controlled and lands in the signed PDF.
    a = signing.audit_record("c1", "terms", user_agent="<script>alert(1)</script>")
    out = signing.audit_html(a)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


# ------------------------------------------------------------------- page


def test_the_page_refuses_a_bad_link():
    from src import sign_page

    with pytest.raises(signing.SigningError):
        sign_page.handle_get("forged-token", dry_run=True)


def test_the_page_shows_the_contract_and_a_pad():
    from src import sign_page

    page = sign_page.handle_get(signing.make_token("c1"), dry_run=True)
    assert "הסכם התקשרות" in page
    assert "<canvas" in page
    assert 'dir="rtl"' in page


def test_the_page_asks_for_what_clickup_lacks():
    from src import sign_page

    page = sign_page.handle_get(signing.make_token("c1"), dry_run=True)
    for label in ("ת.ז / עוסק מורשה / ח.פ", "כתובת"):
        assert label in page


def test_signing_twice_files_one_contract(monkeypatch):
    from src import sign_page

    calls = []
    monkeypatch.setattr(sign_page, "_finalise",
                        lambda *a, **k: calls.append(1) or {"link": "x"})
    token = signing.make_token("c1")
    form = {"client_business_id": ["514111111"], "client_address": ["הרצל 1"],
            "client_email": ["a@b.co"], "client_phone": ["0501234567"],
            "signature": [data_url(make_png())]}
    sign_page.handle_post(token, form, dry_run=True)
    sign_page.handle_post(token, form, dry_run=True)
    assert len(calls) == 1, "a double submit must not file two signed contracts"
