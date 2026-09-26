"""The operator guide published to ClickUp: one page per section, generated."""

from __future__ import annotations

from src.tools import sync_guide


def test_the_guide_splits_into_a_page_per_section():
    md = ('<div dir="rtl">\n\n# מדריך\n\nפתיחה קצרה\n\n## מבוא\nטקסט\n\n'
          '## נהלים\n### Meta\nשלבים\n\n</div>\n')
    pages = sync_guide.sections(md)
    assert [n for n, _ in pages] == [sync_guide.FIRST_PAGE, "מבוא", "נהלים"]
    assert pages[0][1].startswith(sync_guide.NOTICE) and "פתיחה קצרה" in pages[0][1]
    assert "### Meta" in pages[2][1]  # subsections stay inside their page
    assert all("<div" not in c and "# מדריך" not in c for _, c in pages)


def test_the_real_guide_has_its_meta_procedure_page():
    pages = dict(sync_guide.sections(sync_guide.GUIDE.read_text(encoding="utf-8")))
    assert "חיבור חשבון מודעות Meta" in pages["נהלים תפעוליים"]
    assert "580646099266175" in pages["נהלים תפעוליים"]
