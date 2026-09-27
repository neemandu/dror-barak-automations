"""A client's documents in their own ClickUp columns, and the date a send went out."""

from src.lib import client_files


class _Crm:
    def __init__(self, result=None, boom=False):
        self.result, self.boom, self.calls = result or {"id": "att1"}, boom, []

    def attach_file(self, cid, field, data, name):
        if self.boom:
            raise RuntimeError("ClickUp down")
        self.calls.append((cid, field, name))
        return self.result


def test_a_pdf_goes_in_its_column_with_an_ascii_name():
    crm = _Crm()
    assert client_files.attach_pdf(crm, "c1", "campaign_report_pdf", b"%PDF", tag="2026-08")
    assert crm.calls == [("c1", "campaign_report_pdf", "campaign-report-2026-08.pdf")]
    assert all(n.isascii() for _c, _f, n in crm.calls)


def test_a_missing_column_is_skipped_and_a_failure_logged_never_raised():
    assert not client_files.attach_pdf(_Crm({"skipped": "no field"}), "c1", "strategy_pdf", b"%PDF")

    logged = []

    class Auto:
        def log_action(self, action, status, **kw):
            logged.append((action, status))

    assert not client_files.attach_pdf(_Crm(boom=True), "c1", "strategy_pdf", b"%PDF", auto=Auto())
    assert logged == [("pdf_column_failed", "error")]


def test_a_doc_is_exported_to_pdf_for_its_column():
    crm = _Crm()
    assert client_files.attach_doc(crm, "c1", "social_report_pdf", "doc1", dry_run=True)
    assert crm.calls[0][1] == "social_report_pdf" and crm.calls[0][2].startswith("social-report-")
    assert not client_files.attach_doc(crm, "c1", "social_report_pdf", "", dry_run=True)


def test_sending_the_contract_dates_its_column(monkeypatch):
    from src.automations import send_quote
    from src.lib.clients.crm import CrmClient

    monkeypatch.setenv("SIGN_LINK_SECRET", "s")
    monkeypatch.setenv("SIGN_BASE_URL", "https://sign.example/dev")
    written = []
    monkeypatch.setattr(CrmClient, "update_fields", lambda self, cid, **f: written.append(f) or {})
    send_quote.send("42", dry_run=True)
    assert written and written[-1]["sub_status"] == "quote_sent" and written[-1]["quote_sent_at"] > 1.7e12
