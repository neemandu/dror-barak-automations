from src.lib.markdown_html import to_html


def test_headings_lists_emphasis_and_links():
    out = to_html("# כותרת\n\nטקסט **חשוב** ו-*נטוי*\n\n- א\n- ב\n\n1. אחד\n2. שניים\n\n[דרור](https://drorbrk.co.il)")
    assert "<h1>כותרת</h1>" in out and "<strong>חשוב</strong>" in out and "<em>נטוי</em>" in out
    assert "<ul><li>א</li><li>ב</li></ul>" in out and "<ol><li>אחד</li><li>שניים</li></ol>" in out
    assert '<a href="https://drorbrk.co.il">דרור</a>' in out


def test_model_output_cannot_inject_markup():
    assert "<script>" not in to_html("<script>alert(1)</script>")
