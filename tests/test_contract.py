"""Tests for rendering the contract.

This is the document a client signs and pays against, so the tests here care
about two things above all: that a contract never reaches a client with a hole in
it, and that the price says the same thing in both places it appears.
"""

from __future__ import annotations

import pytest

from src.lib import contract

CLIENT = {
    "name": "מכללת אלפא",
    "business_id": "514123456",
    "address": "הרצל 1, תל אביב",
    "phone": "+972501111111",
    "email": "alpha@example.com",
    "monthly_price": 4900,
}


def test_template_is_readable_and_has_the_expected_placeholders():
    found = contract.placeholders_in(contract.load_template())
    assert found == {
        "client_name", "client_business_id", "client_address", "client_phone",
        "client_email", "sign_date", "price_strategy", "price_campaigns",
        "price_total", "provider_signature", "client_signature",
    }


def test_prices_are_totalled_not_transcribed():
    f = contract.fields_from_client(CLIENT, price_strategy=4900, price_campaigns=2500)
    assert f["price_strategy"] == "4,900"
    assert f["price_campaigns"] == "2,500"
    assert f["price_total"] == "7,400"


def test_the_annex_and_clause_10_cannot_disagree():
    """Dror's original said 7,400 in clause 10 and 7,900 in the annex.

    Both now read the same placeholders, so the document cannot contradict itself
    about what the client owes.
    """
    f = contract.fields_from_client(CLIENT, price_strategy=4900, price_campaigns=3000)
    out = contract.render(f, signatures={"provider_signature": "", "client_signature": ""})
    assert out.count("7,900") == 2   # clause 10.3 and the annex total
    assert out.count("3,000") == 2   # clause 10.2 and the annex row
    assert "7,400" not in out


def test_render_fills_every_field():
    f = contract.fields_from_client(CLIENT, price_strategy=4900, price_campaigns=2500)
    out = contract.render(f, signatures={"provider_signature": "", "client_signature": ""})
    assert "מכללת אלפא" in out
    assert "514123456" in out
    assert "הרצל 1, תל אביב" in out
    assert "{{" not in out


@pytest.mark.parametrize("missing", ["client_name", "client_business_id",
                                     "client_address", "client_phone", "client_email"])
def test_a_blank_field_refuses_to_render(missing):
    # "סך של  ₪ + מע״מ" reaching a client is worse than an error in a log.
    f = contract.fields_from_client(CLIENT, price_strategy=4900, price_campaigns=2500)
    f[missing] = ""
    with pytest.raises(contract.ContractError) as exc:
        contract.render(f)
    assert missing in str(exc.value)


def test_missing_for_names_the_hebrew_labels():
    f = contract.fields_from_client({"name": "מכללת בטא"}, price_strategy=1, price_campaigns=0)
    missing = contract.missing_for(f)
    assert "ת.ז / ח.פ" in missing
    assert "כתובת" in missing
    assert "שם הלקוח" not in missing


def test_a_client_name_cannot_inject_markup_into_the_contract():
    # The name comes from ClickUp, which anyone with the list can edit. It is data:
    # it must never be able to alter the contract's own terms.
    nasty = {**CLIENT, "name": '<script>alert(1)</script><p>סעיף מזויף'}
    f = contract.fields_from_client(nasty, price_strategy=4900, price_campaigns=0)
    out = contract.render(f, signatures={"provider_signature": "", "client_signature": ""})
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_signatures_are_inserted_as_markup():
    f = contract.fields_from_client(CLIENT, price_strategy=4900, price_campaigns=0)
    out = contract.render(f, signatures={
        "provider_signature": '<img src="data:image/png;base64,AAA">',
        "client_signature": '<img src="data:image/png;base64,BBB">',
    })
    assert 'src="data:image/png;base64,BBB"' in out


def test_single_crm_price_does_not_get_silently_split():
    # ClickUp holds one number; the contract bills two lines. Guessing a split
    # would put a number in front of a client that nobody agreed.
    f = contract.fields_from_client(CLIENT)
    assert f["price_strategy"] == "4,900"
    assert f["price_campaigns"] == "0"
    assert f["price_total"] == "4,900"


def test_bad_prices_are_rejected():
    with pytest.raises(contract.ContractError):
        contract.fields_from_client(CLIENT, price_strategy="לא מספר", price_campaigns=0)
    with pytest.raises(contract.ContractError):
        contract.fields_from_client(CLIENT, price_strategy=-100, price_campaigns=0)


def test_prices_with_commas_from_clickup_are_understood():
    f = contract.fields_from_client(CLIENT, price_strategy="4,900", price_campaigns="2,500")
    assert f["price_total"] == "7,400"


def test_sign_date_defaults_to_today_but_can_be_pinned():
    f = contract.fields_from_client(CLIENT, price_strategy=1, price_campaigns=0,
                                    sign_date="15 / 07 / 2026")
    assert f["sign_date"] == "15 / 07 / 2026"


def test_contract_still_matches_drors_source_text():
    """Guards the transcription: the legal clauses must not drift silently."""
    from pathlib import Path

    source = Path(contract.template_path()).parents[1] / "docs" / "contract_source.txt"
    original = source.read_text(encoding="utf-8")
    rendered = contract.render(
        contract.fields_from_client(CLIENT, price_strategy=4900, price_campaigns=2500),
        signatures={"provider_signature": "", "client_signature": ""},
    )
    # Spot-check clauses that carry real obligations.
    for clause in [
        "14 ימי עסקים",                    # strategy delivery deadline
        "עד 4 פגישות עבודה בחודש",          # meetings included
        "הודעה מוקדמת בכתב של 14 ימים",     # notice period
        "1,000 ₪",                          # referral fee
        "REDACTED_BUSINESS_ID",                        # Dror's company number
        "מספר חשבון: REDACTED_ACCOUNT",               # bank details
    ]:
        assert clause in original, f"{clause!r} not in Dror's source — test is stale"
        assert clause in rendered, f"{clause!r} lost in transcription"
