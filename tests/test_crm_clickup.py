"""Tests for the ClickUp-backed CRM client and its field mapping.

The mapping is the risky part: ClickUp returns custom fields as an untyped list of
ids and values, and the automations expect a flat client dict. A silent mistake
here writes a client's price into the wrong column, so these tests work off
realistic ClickUp payloads rather than the dry-run fixtures.
"""

from __future__ import annotations

import pytest

from src.lib import crm_fields
from src.lib.clients.crm import CrmClient

# What GET /list/{id}/field returns for a correctly set-up clients list.
LIST_FIELDS = [
    {"id": "f1", "name": "טלפון", "type": "phone"},
    {"id": "f2", "name": "מחיר חודשי", "type": "number"},
    {"id": "f3", "name": "תיקיית Drive", "type": "url"},
    {"id": "f4", "name": "חוזה חתום", "type": "url"},
    {"id": "f5", "name": "סטטוס משני", "type": "drop_down",
     "type_config": {"options": [
         {"id": "o1", "name": "פגישה ראשונית"},
         {"id": "o2", "name": "נשלח שאלון"},
         {"id": "o3", "name": "חתם"},
         {"id": "o4", "name": "בעבודה"},
     ]}},
    {"id": "f9", "name": "הערות של דרור", "type": "short_text"},  # not ours
]

# What GET /task/{id} returns.
TASK = {
    "id": "abc123",
    "name": "מכללת אלפא",
    "url": "https://app.clickup.com/t/abc123",
    "status": {"status": "לקוח פעיל"},
    "custom_fields": [
        {"id": "f1", "value": "+972501111111"},
        {"id": "f2", "value": 3500},
        {"id": "f3", "value": "https://drive.google.com/drive/folders/x"},
        {"id": "f5", "value": "o3"},          # dropdown stores the option id
        {"id": "f9", "value": "לא לגעת"},
        {"id": "f4", "value": None},          # set up but empty
    ],
}


@pytest.fixture
def client():
    c = CrmClient(dry_run=True)
    c._fields_cache = crm_fields.resolve_fields(LIST_FIELDS)
    c._statuses_cache = ["ליד", "לקוח פעיל", "מושהה", "הסתיים"]
    return c


# ------------------------------------------------------------ alias matching


@pytest.mark.parametrize("raw,expected", [
    ("לקוח פעיל", "active"), ("Active", "active"), ("ליד", "lead"),
    ("מושהה", "paused"), ("הסתיים", "finished"), ("to do", None),
])
def test_status_aliases(raw, expected):
    assert crm_fields.canonical_status(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("חתם", "signed"), ("נחתם", "signed"), ("בעבודה", "in_work"),
    ("פגישה ראשונית", "initial_meeting"), ("nonsense", None),
])
def test_sub_status_aliases(raw, expected):
    assert crm_fields.canonical_sub_status(raw) == expected


def test_resolve_fields_ignores_drors_own_columns():
    resolved = crm_fields.resolve_fields(LIST_FIELDS)
    assert set(resolved) == {"phone", "monthly_price", "drive_folder",
                             "signed_contract", "sub_status"}
    assert "הערות של דרור" not in [f["name"] for f in resolved.values()]


def test_missing_fields_reports_what_is_absent():
    assert crm_fields.missing_fields(LIST_FIELDS, ["phone", "email"]) == ["email"]


# ------------------------------------------------------------------ dropdowns


def test_dropdown_option_id_matches_by_alias_not_just_exact_label():
    field = LIST_FIELDS[4]
    assert crm_fields.dropdown_option_id(field, "חתם") == "o3"
    # The canonical value must resolve to Dror's Hebrew option.
    assert crm_fields.dropdown_option_id(field, "signed") == "o3"
    assert crm_fields.dropdown_option_id(field, "quote_sent") is None


def test_dropdown_label_reads_back_by_id_and_index():
    field = LIST_FIELDS[4]
    assert crm_fields.dropdown_label(field, "o3") == "חתם"
    assert crm_fields.dropdown_label(field, 0) == "פגישה ראשונית"
    assert crm_fields.dropdown_label(field, "nope") is None


def test_coerce_dropdown_refuses_unknown_option_rather_than_guessing():
    # Passing the raw string through would set the wrong option, silently.
    with pytest.raises(ValueError) as exc:
        crm_fields.coerce_value(LIST_FIELDS[4], "משהו אחר")
    assert "no option named" in str(exc.value)


def test_coerce_number_and_text():
    assert crm_fields.coerce_value(LIST_FIELDS[1], "3500") == 3500.0
    assert crm_fields.coerce_value(LIST_FIELDS[1], "") is None
    assert crm_fields.coerce_value(LIST_FIELDS[0], "+972") == "+972"


# -------------------------------------------------------------- task mapping


def test_task_maps_onto_the_client_dict_the_automations_expect(client):
    c = client._to_client(TASK)
    assert c["id"] == "abc123"
    assert c["name"] == "מכללת אלפא"
    assert c["phone"] == "+972501111111"
    assert c["monthly_price"] == 3500
    assert c["status"] == "active"          # 'לקוח פעיל' normalised
    assert c["sub_status"] == "signed"      # option id o3 -> 'חתם' -> signed
    assert c["drive_folder_url"] == "https://drive.google.com/drive/folders/x"
    assert c["url"] == "https://app.clickup.com/t/abc123"


def test_fields_not_configured_read_as_empty_not_missing_keys(client):
    # Automations index these directly; a KeyError would crash the run.
    c = client._to_client(TASK)
    for key in ("email", "service_type", "recordings_path", "morning_status",
                "morning_client_id", "signed_contract_url"):
        assert c[key] == ""


def test_first_name_falls_back_to_the_first_word(client):
    assert client._to_client(TASK)["first_name"] == "מכללת"


def test_unrecognised_status_passes_through_rather_than_becoming_empty(client):
    task = {**TASK, "status": {"status": "משהו חדש"}}
    assert client._to_client(task)["status"] == "משהו חדש"


def test_drive_path_that_is_not_a_url_yields_no_url(client):
    task = {**TASK, "custom_fields": [{"id": "f3", "value": "/Clients/Alpha"}]}
    c = client._to_client(task)
    assert c["drive_folder_path"] == "/Clients/Alpha"
    assert c["drive_folder_url"] == ""


def test_status_name_lookup_maps_canonical_to_drors_wording(client):
    assert client._status_name_for("active") == "לקוח פעיל"
    assert client._status_name_for("lead") == "ליד"
    assert client._status_name_for("nonexistent") is None


def test_list_active_clients_raises_when_no_status_maps_to_active():
    # Silently returning [] would look like "no clients to bill this month".
    c = CrmClient(dry_run=True)
    c._statuses_cache = ["to do", "in progress", "complete"]
    c.dry_run = False  # take the live branch without making a request
    c.list_id = "L1"
    with pytest.raises(ValueError) as exc:
        c.list_active_clients()
    assert "active" in str(exc.value)


# ------------------------------------------------------------------- writes


def test_update_fields_dry_run_records_rather_than_writes(client):
    result = client.update_fields("abc123", drive_folder_url="https://x")
    assert result["client_id"] == "abc123"


def test_caller_aliases_reach_the_right_field():
    from src.lib.clients.crm import _CALLER_ALIASES

    # onboarding writes drive_folder_url; the ClickUp column is 'תיקיית Drive'.
    assert _CALLER_ALIASES["drive_folder_url"] == "drive_folder"
    assert _CALLER_ALIASES["signed_contract_url"] == "signed_contract"


def test_update_fields_reports_unknown_fields_instead_of_dropping_them(monkeypatch, client):
    client.dry_run = False
    client.base_url = "https://api.clickup.test"
    client.token = "pk_test"
    sent: list[tuple[str, str]] = []

    def fake_request(method, url, **kwargs):
        sent.append((method, url))
        class R:
            @staticmethod
            def json():
                return {}
        return R()

    monkeypatch.setattr(client, "_request", fake_request)
    result = client.update_fields(
        "abc123", drive_folder_url="https://x", email="a@b.c", sub_status="signed"
    )
    assert "drive_folder_url" in result["written"]
    assert "sub_status" in result["written"]
    # 'email' is not set up on this list — surfaced, not silently swallowed.
    assert "email" in result["skipped"]
    assert len(sent) == 2
