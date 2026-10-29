"""A client's own version of the contract (src/lib/contract_edit.py).

What must hold: the standard contract is untouched by the machinery, a locked
block (names, prices, signatures) cannot be removed, numbering follows the edits,
a reference to a deleted clause stops the contract rather than pointing at
nothing, and the client signs the version that was sent, not a later edit.
"""

from __future__ import annotations

import re

import pytest

from src.lib import contract, contract_edit as ce, contract_store

CLIENT = {"name": "מכללת אלפא", "business_id": "514123456", "address": "הרצל 1", "phone": "0501111111",
          "email": "alpha@example.com", "price_strategy": 4000, "price_campaigns": 1500}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for key in contract.PROVIDER_FIELDS.values():
        monkeypatch.setenv(key, "פרט")
    monkeypatch.setenv("SIGN_LINK_SECRET", "test-secret-for-links")
    monkeypatch.setenv("SIGN_BASE_URL", "https://sign.example/dev")


def _fields(client=None):
    return contract.fields_from_client(client or CLIENT)


def _clause(blocks, n):
    """Indexes of clause n: its heading up to and including the separator after it."""
    start = next(i for i, b in enumerate(blocks) if b.element.startswith(f"<h2>{n}."))
    end = next(i for i in range(start, len(blocks)) if blocks[i].tag == "hr")
    return set(range(start, end + 1))


def _without(template, drop):
    _h, blocks, _t = ce.split(template)
    return ce.apply(template, [{"i": i} for i in range(len(blocks)) if i not in drop], ce.sha(template))


def _headers(text):
    return re.findall(r"<h2>(\d+)\.", text)


def test_splitting_is_lossless_and_the_standard_is_already_numbered():
    t = ce.standard()
    head, blocks, tail = ce.split(t)
    assert ce.join(head, blocks, tail) == t
    assert ce.renumber(t) == t, "renumbering the standard must change nothing"
    assert _headers(t) == [str(n) for n in range(1, 21)]


def test_the_standard_renders_as_before():
    """Clause 10's numbers moved into the template; the contract reads the same."""
    out = contract.render(_fields())
    for line in ("10.1 אסטרטגיה", "10.2 ניהול קמפיינים", "10.3 סך התמורה", "10.4 תשלום"):
        assert line in out
    single = contract.render(_fields({**CLIENT, "price_campaigns": 0}))
    assert "10.1 אסטרטגיה" in single and "10.2 תשלום" in single and "ניהול קמפיינים ופרסום" not in single


def test_every_block_with_a_placeholder_is_locked():
    _h, blocks, _t = ce.split(ce.standard())
    for b in blocks:
        if "{{" in b.src:
            assert b.locked, b.element[:60]
    assert any(not b.locked and b.tag == "p" for b in blocks)


def test_deleting_a_clause_renumbers_the_rest_and_the_payment_clause_follows():
    t = ce.standard()
    _h, blocks, _t = ce.split(t)
    new = _without(t, _clause(blocks, 7))
    assert _headers(new) == [str(n) for n in range(1, 20)]
    assert "כתיבת דפי נחיתה" not in new
    assert ce.summary_text(ce.compare(new)) == "נמחק סעיף: כתיבת דפי נחיתה"
    assert "<p>10.1 כל שינוי" not in new and "<p>19.1 כל שינוי" in new  # "שונות" was 20
    out = contract.render(_fields(), template=new)
    assert "9.1 אסטרטגיה" in out and "9.4 תשלום" in out
    # the payment clause still refers to clauses 3-5 and 6, which did not move
    assert "בסעיפים 3-5" in out and "בסעיף 6" in out


def test_a_reference_to_a_deleted_clause_refuses_with_a_reason():
    t = ce.standard()
    _h, blocks, _t = ce.split(t)
    new = _without(t, _clause(blocks, 6))  # the campaign price line refers to clause 6
    with pytest.raises(contract.ContractError) as exc:
        contract.render(_fields(), template=new)
    assert "סעיף 6" in str(exc.value)
    # a client without campaigns never sees that line, so their contract is fine
    contract.render(_fields({**CLIENT, "price_campaigns": 0}), template=new)


def test_references_follow_a_deleted_clause_before_them():
    t = ce.standard()
    _h, blocks, _t = ce.split(t)
    new = _without(t, _clause(blocks, 2))
    out = contract.render(_fields(), template=new)
    assert "בסעיפים 2-4" in out and "בסעיף 5" in out


def test_a_locked_block_cannot_be_deleted():
    t = ce.standard()
    _h, blocks, _t = ce.split(t)
    price = next(i for i, b in enumerate(blocks) if "{{price_strategy}}" in b.src and b.tag == "p")
    with pytest.raises(ce.Invalid):
        _without(t, {price})


def test_editing_a_paragraph_keeps_its_number_and_escapes_what_was_typed():
    t = ce.standard()
    _h, blocks, _t = ce.split(t)
    i = next(i for i, b in enumerate(blocks) if b.element.startswith("<p>11.3"))
    assert ce.editable(blocks[i])["num"] and not ce.editable(blocks[i])["text"].startswith("11.3")
    items = [{"i": j} for j in range(len(blocks))]
    items[i] = {"i": i, "text": "כל צד רשאי לסיים בהודעה של 30 יום <b>מראש</b>"}
    new = ce.apply(t, items, ce.sha(t))
    assert "<p>11.3 כל צד רשאי לסיים בהודעה של 30 יום &lt;b&gt;מראש&lt;/b&gt;</p>" in new
    assert ce.compare(new)["edited"] == 1 and ce.compare(new)["changes"] == 1
    assert ce.summary_text(ce.compare(new)) == "קטע אחד נערך"


def test_a_new_clause_takes_its_place_in_the_numbering():
    t = ce.standard()
    _h, blocks, _t = ce.split(t)
    after = max(_clause(blocks, 9))
    items = []
    for j in range(len(blocks)):
        items.append({"i": j})
        if j == after:
            items += [{"new": "h2", "text": "תנאים מיוחדים", "num": True},
                      {"new": "p", "text": "שורה ראשונה\nשורה שנייה"}, {"new": "hr"}]
    new = ce.apply(t, items, ce.sha(t))
    assert "<h2>10. תנאים מיוחדים</h2>" in new and "<h2>11. תמורה ותנאי תשלום</h2>" in new
    assert "<p>שורה ראשונה<br>שורה שנייה</p>" in new
    out = contract.render(_fields(), template=new)
    assert "11.1 אסטרטגיה" in out
    c = ce.compare(new)
    assert c["added_clauses"] == ["תנאים מיוחדים"] and c["added"] == 0 and c["deleted"] == 0
    assert c["marks"].count("new") == 3 and ce.summary_text(c) == "נוסף סעיף: תנאים מיוחדים"


def test_saving_over_a_newer_version_or_moving_blocks_is_refused():
    t = ce.standard()
    _h, blocks, _t = ce.split(t)
    items = [{"i": j} for j in range(len(blocks))]
    with pytest.raises(ce.Invalid):
        ce.apply(t, items, "stale")
    with pytest.raises(ce.Invalid):
        ce.apply(t, [items[1], items[0]] + items[2:], ce.sha(t))
    with pytest.raises(ce.Invalid):
        ce.apply(t, items + [{"new": "p", "text": "{{price_total}}"}], ce.sha(t))


def test_the_editor_shows_locked_values_and_hides_section_markers():
    view = ce.editor_blocks(ce.standard(), _fields())
    assert any(b.get("hidden") for b in view)
    price = next(b for b in view if b["locked"] and "4,000" in b.get("html", ""))
    assert "ce-chip" in price["html"] and price["section"]
    assert all("text" in b for b in view if not b["locked"] and b["kind"] in ce.EDITABLE)


def test_the_signing_page_renders_the_version_that_was_sent_not_a_later_edit():
    from src import sign_page
    from src.lib import signing

    t = ce.standard()
    _h, blocks, _t = ce.split(t)
    sent_version = _without(t, _clause(blocks, 7))
    contract_store.save_draft("c1", sent_version, base=ce.sha(t), summary="5 נמחקו", changes=5)
    contract_store.freeze_sent("c1")
    later = _without(t, _clause(blocks, 8))
    contract_store.save_draft("c1", later, base=ce.sha(t), summary="", changes=4)

    page = sign_page.handle_get(signing.make_token("c1"), dry_run=True)
    assert "כתיבת דפי נחיתה" not in page and "כתיבת תסריטים לסרטונים" in page
    # sending again with no version clears the frozen one: the standard is back
    contract_store.clear_draft("c1")
    contract_store.freeze_sent("c1")
    assert "כתיבת דפי נחיתה" in sign_page.handle_get(signing.make_token("c1"), dry_run=True)


def test_the_send_comment_says_which_version_went_out():
    from src.automations import send_quote

    msg = send_quote._message(CLIENT, "https://x", delivered=True, email=True, price=5500, reminder=False,
                              version={"summary": "1 נערכו"})
    assert "הנוסח שערכת ללקוח הזה (1 נערכו)" in msg
    assert "הנוסח הרגיל" in send_quote._message(CLIENT, "https://x", delivered=True, email=True,
                                                price=5500, reminder=False)


def test_a_broken_version_refuses_the_send(monkeypatch):
    from src.automations import send_quote
    from src.lib.actions import Refused
    from src.lib.clients.crm import CrmClient

    monkeypatch.setattr(CrmClient, "get_client", lambda self, cid: dict(CLIENT))
    t = ce.standard()
    _h, blocks, _t = ce.split(t)
    contract_store.save_draft("c9", _without(t, _clause(blocks, 6)), base=ce.sha(t), summary="", changes=4)
    with pytest.raises(Refused) as exc:
        send_quote.send("c9", dry_run=True)
    assert "סעיף 6" in str(exc.value)


# ------------------------------------------------------------- the dashboard


def _payload(template, drop=(), edit=None):
    _h, blocks, _t = ce.split(template)
    items = [{"i": i} for i in range(len(blocks)) if i not in drop]
    for it in items:
        if edit and it["i"] in edit:
            it["text"] = edit[it["i"]]
    return {"base": ce.sha(template), "items": items}


def test_saving_from_the_dashboard_keeps_a_version_and_reset_clears_it():
    from src import contracts_pages

    t = ce.standard()
    _h, blocks, _t = ce.split(t)
    status, out = contracts_pages.save_version("42", _payload(t, drop=_clause(blocks, 7)), dry_run=True)
    assert status == 200 and out["summary"] == "נמחק סעיף: כתיבת דפי נחיתה"
    saved = contract_store.draft("42")
    assert "כתיבת דפי נחיתה" not in saved["template"]
    # the page now previews this version, says so, and offers the way back
    page = contracts_pages.contract_page([], "", "42", dry_run=True)
    assert "נוסח מותאם" in page and "חזרה לנוסח הרגיל" in page
    paper = page[page.index('<article class="paper">'):page.index('</article>')]
    assert "כתיבת דפי נחיתה" not in paper and "<h2>7. כתיבת תסריטים" in paper
    # a second save against the old base is refused: someone saved in between
    status, out = contracts_pages.save_version("42", _payload(t), dry_run=True)
    assert status == 422
    assert contracts_pages.save_version("42", {"reset": True}, dry_run=True)[0] == 200
    assert contract_store.draft("42") is None


def test_saving_with_no_change_leaves_the_standard():
    from src import contracts_pages

    t = ce.standard()
    status, out = contracts_pages.save_version("42", _payload(t), dry_run=True)
    assert status == 200 and out["standard"] and contract_store.draft("42") is None


def test_the_editor_page_is_a_dashboard_page_with_locked_values():
    from src import contracts_pages

    page = contracts_pages.contract_page([], "", "42", dry_run=True, edit=True)
    assert 'id="ce"' in page and "ce-chip" in page and 'data-ce="save"' in page
    assert " data-spa" in page
    script = re.search(r"<script data-page-script>(.*?)</script>", page, re.S)
    assert not script or "document.addEventListener" not in script.group(1)


def test_the_task_gets_its_contract_page_link_once(monkeypatch):
    from src.lib import contract_link

    monkeypatch.setenv("DASHBOARD_URL", "https://x.example/dev/dashboard")
    assert contract_link.url_for("86ab") == "https://x.example/dev/contracts/86ab"

    class Crm:
        calls: list = []

        def update_fields(self, cid, **f):
            self.calls.append((cid, f))
            return {"written": f, "skipped": {}}

    crm = Crm()
    assert contract_link.ensure("86ab", crm=crm) is True
    assert contract_link.ensure("86ab", crm=crm) is False  # once per client
    assert crm.calls == [("86ab", {"dashboard_contract": "https://x.example/dev/contracts/86ab"})]


def test_a_list_without_the_link_column_is_skipped_quietly(monkeypatch):
    from src.lib import contract_link

    monkeypatch.setenv("DASHBOARD_URL", "https://x.example/dev/dashboard")

    class Crm:
        def update_fields(self, cid, **f):
            return {"written": {}, "skipped": {"dashboard_contract": "no such field on the list"}}

    assert contract_link.ensure("c2", crm=Crm()) is False
    monkeypatch.setenv("DASHBOARD_URL", "")
    assert contract_link.ensure("c3", crm=Crm()) is False
