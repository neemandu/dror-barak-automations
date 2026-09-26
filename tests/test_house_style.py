"""Text that goes out in Dror's name reads like a person wrote it.

Long dashes (— and –) are the giveaway of machine-written Hebrew. Our own copy
has none, in any string the code can emit (docstrings and comments are not
emitted and are not checked), and whatever Claude writes is cleaned on the way
out.
"""

import io
import re
import tokenize
from pathlib import Path

import pytest

from src.lib import email_templates, text_style

ROOT = Path(__file__).resolve().parents[1]
DASHES = ("—", "–")


def _runtime_strings(path: Path):
    """String literals that are not docstrings."""
    toks = list(tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline))
    skip = (tokenize.NL, tokenize.COMMENT)
    kinds = {tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", -1)}
    for i, t in enumerate(toks):
        if t.type not in kinds:
            continue
        prev = next((p for p in reversed(toks[:i]) if p.type not in skip), None)
        nxt = next((n for n in toks[i + 1:] if n.type not in skip), None)
        docstring = (t.type == tokenize.STRING
                     and (prev is None or prev.type in (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT))
                     and nxt is not None and nxt.type in (tokenize.NEWLINE, tokenize.ENDMARKER))
        if not docstring:
            yield t.start[0], t.string


def test_no_long_dash_in_any_string_the_code_can_emit():
    found = [f"{p.relative_to(ROOT)}:{line}"
             for p in sorted((ROOT / "src").rglob("*.py"))
             for line, s in _runtime_strings(p) if any(d in s for d in DASHES)]
    assert not found, f"long dash in: {found}"


def test_no_gendered_slash_in_any_string_the_code_can_emit():
    """"מלא/י", "נסה/י": our copy speaks neutrally ("ממלאים", "אפשר לנסות") instead."""
    slash = re.compile(r"[\u05d0-\u05ea]/[\u05d9\u05d4](?![\u05d0-\u05ea])")
    found = [f"{p.relative_to(ROOT)}:{line}"
             for p in sorted((ROOT / "src").rglob("*.py"))
             for line, s in _runtime_strings(p) if slash.search(s)]
    assert not found, f"gendered slash in: {found}"


@pytest.mark.parametrize("name", ["contract_he.html", "campaign_report_he.html"])
def test_no_long_dash_in_a_template_page(name):
    html = (ROOT / "templates" / name).read_text(encoding="utf-8")
    visible = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    assert not any(d in visible for d in DASHES)


_PARAMS = dict(client_name="מכללת דוגמה", cta_url="https://x.example/a", signed_at="23.9.2026",
               fingerprint="abc", month_label="ספטמבר 2026", spend="1,000 ₪", leads="10",
               cost_per_lead="100 ₪")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


@pytest.mark.parametrize("name", sorted(email_templates.TEMPLATES))
def test_every_email_reads_like_a_person_wrote_it(name):
    mail = email_templates.render(name, **_PARAMS)
    for part in (mail["subject"], mail["text"], mail["html"]):
        assert not any(d in part for d in DASHES)
    assert not re.search(r"/[יה]\b", mail["text"]), "no תמלא/י slashes"
    assert not _EMOJI.search(mail["subject"])


def test_client_emails_are_signed_and_internal_ones_are_not():
    client = email_templates.render("questionnaire", **_PARAMS)
    internal = email_templates.render("strategy_ready", **_PARAMS)
    assert email_templates.SIGNATURE_TITLE in client["html"] and email_templates.SIGNATURE_TITLE in client["text"]
    assert email_templates.SIGNATURE_TITLE not in internal["html"]
    assert f"cid:{email_templates.BAND_CID}" in client["html"]
    assert client["inline"][email_templates.BAND_CID].startswith(b"\x89PNG")


def test_humanize():
    assert text_style.humanize("2,500–6,000 ₪") == "2,500-6,000 ₪"
    assert text_style.humanize("העסק — לא הבעלים") == "העסק - לא הבעלים"
    assert text_style.humanize("— שורה") == "שורה"
    assert text_style.humanize("בלי מקפים.") == "בלי מקפים."


def test_every_claude_call_carries_the_house_style():
    from src.lib.clients.anthropic_ai import AnthropicClient

    ai = AnthropicClient(dry_run=True)
    ai.complete("x", system="אתה אנליסט")
    ai.complete("y")
    assert all(text_style.AI_STYLE in c["system"] for c in ai.calls)


def test_every_logged_action_has_a_hebrew_label():
    """The dashboard and the daily email show Dror what happened in his words;
    a new ``log_action("...")`` without a label would show him a code name."""
    import ast

    from src.lib import subjects

    missing = set()
    for p in (ROOT / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "log_action"
                    and node.args and isinstance(node.args[0], ast.Constant)):
                if node.args[0].value not in subjects.ACTION_LABELS:
                    missing.add(f"{p.relative_to(ROOT)}: {node.args[0].value}")
    assert not missing, sorted(missing)


def test_instantly_navigated_pages_keep_their_listeners_to_main():
    """A page that takes part in instant navigation runs its script again on every
    visit, and <main> is replaced; a listener on ``document`` would outlive the page
    and fire on the next one (the questionnaire list's "copy" on the leads' phones)."""
    import re as _re

    from src import dashboard, questionnaire_admin

    from src import clients_pages, contracts_pages

    pages = [dashboard._leads_page([], ""), dashboard._dashboard_page([], {}, ""),
             contracts_pages.contracts_page([], "", dry_run=True).encode(),
             contracts_pages.contract_page([], "", "42", dry_run=True).encode(),
             clients_pages.documents_page([], "", dry_run=True).encode(), clients_pages.clients_page([], "", dry_run=True).encode(),
             clients_pages.client_page([], "", "42", dry_run=True).encode()]
    for route in ("/admin/questionnaires", "/admin/responses"):
        pages.append(questionnaire_admin.handle("GET", route, {}, b"", {}, dry_run=True).body.encode())
    for page in pages:
        html = page.decode()
        assert " data-spa " in html or " data-spa>" in html
        script = _re.search(r"<script data-page-script>(.*?)</script>", html, _re.S)
        assert not script or "document.addEventListener" not in script.group(1)


def test_a_client_page_owns_its_layout_classes():
    """The design system's styles load on every page; a class the app shell styles
    (``.shell`` was one) would silently re-lay a client's questionnaire or signing page."""
    from src import questionnaire_page, sign_page, ui

    def classes(css):
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        return {c for sel in re.findall(r"([^{}]+)\{", css) for c in re.findall(r"\.([A-Za-z][\w-]*)", sel)}

    # the design system's own components and states, which a page tunes on purpose
    adjusted = {"public", "btn", "btn-lg", "input", "badge", "icon", "ltr", "spacer", "is-done"}
    shared = classes(ui.CSS) - adjusted
    for page in (questionnaire_page, sign_page):
        assert not classes(page.PAGE_CSS) & shared, f"{page.__name__}: {sorted(classes(page.PAGE_CSS) & shared)}"
