from src.lib.markdown_html import to_html


def test_headings_lists_emphasis_and_links():
    out = to_html("# כותרת\n\nטקסט **חשוב** ו-*נטוי*\n\n- א\n- ב\n\n1. אחד\n2. שניים\n\n[דרור](https://drorbrk.co.il)")
    assert "<h1>כותרת</h1>" in out and "<strong>חשוב</strong>" in out and "<em>נטוי</em>" in out
    assert "<ul><li>א</li><li>ב</li></ul>" in out and "<ol><li>אחד</li><li>שניים</li></ol>" in out
    assert '<a href="https://drorbrk.co.il">דרור</a>' in out


def test_model_output_cannot_inject_markup():
    assert "<script>" not in to_html("<script>alert(1)</script>")


def test_spaced_numbered_items_stay_one_list():
    out = to_html("1. ראשון\n\n2. שני\n   שאלה לפגישה: מתי?\n\n3. שלישי\n\nסוף")
    assert out.count("<ol>") == 1
    assert "<li>שני<br>שאלה לפגישה: מתי?</li><li>שלישי</li></ol><p>סוף</p>" in out


def test_a_list_that_starts_mid_way_keeps_its_number():
    assert '<ol start="4"><li>ד</li></ol>' in to_html("4. ד")


def test_tables_and_quotes_render_instead_of_showing_markup():
    out = to_html("| מדד | למה |\n|---|---|\n| עלות לליד | **בסיס** |\n\n> משפט המיצוב\n\nסוף")
    assert "<table><tr><th>מדד</th><th>למה</th></tr><tr><td>עלות לליד</td><td><strong>בסיס</strong></td></tr></table>" in out
    assert "<blockquote>משפט המיצוב</blockquote><p>סוף</p>" in out
    assert "|" not in out and "&gt;" not in out


def test_table_cells_are_escaped():
    assert "&lt;script&gt;" in to_html("| a |\n|---|\n| <script> |")
