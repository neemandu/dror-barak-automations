"""The branded Word documents: Markdown in, a well-formed .docx out."""

import io
import zipfile
from xml.etree import ElementTree

from src.lib import branded_doc as bd


def _parts(blocks, title="אסטרטגיה שיווקית", subtitle="הוכן עבור מכללת דוגמה"):
    z = zipfile.ZipFile(io.BytesIO(bd.build(title, subtitle, blocks)))
    return {name: z.read(name) for name in z.namelist()}


def test_every_xml_part_is_well_formed_and_the_band_is_embedded():
    parts = _parts(bd.from_markdown("## כותרת\nטקסט [קישור](https://a.example?x=1&y=2)\n\n| a | b |\n|---|---|\n| 1 | 2 |"))
    for name, data in parts.items():
        if name.endswith((".xml", ".rels")):
            ElementTree.fromstring(data)  # raises on malformed XML
    assert parts["word/media/band.png"].startswith(b"\x89PNG")
    assert b'r:id="rIdHdr"' in parts["word/document.xml"] and b'r:id="rIdFtr"' in parts["word/document.xml"]
    assert b"PAGE" in parts["word/footer1.xml"]
    assert b'Target="https://a.example?x=1&amp;y=2"' in parts["word/_rels/document.xml.rels"]


def test_spaced_numbered_items_stay_one_list():
    blocks = bd.from_markdown("1. ראשון\n\n2. שני\n   שאלה לפגישה: מתי?\n\n3. שלישי\n\nסוף")
    lists = [b for b in blocks if b["t"] == "list"]
    assert len(lists) == 1 and len(lists[0]["items"]) == 3
    assert {"br": True} in lists[0]["items"][1], "an indented line continues its item"
    assert blocks[-1] == {"t": "p", "runs": [{"text": "סוף"}]}


def test_a_list_that_starts_mid_way_keeps_its_number():
    parts = _parts(bd.from_markdown("4. ד\n5. ה"))
    assert b'<w:startOverride w:val="4"/>' in parts["word/numbering.xml"]


def test_each_list_restarts_its_own_numbering():
    parts = _parts(bd.from_markdown("1. א\n2. ב\n\nפסקה\n\n1. ג"))
    assert parts["word/numbering.xml"].count(b"<w:num ") == 3  # the reserved one + two lists


def test_tables_quotes_and_rules():
    blocks = bd.from_markdown("| מדד | למה |\n|---|---|\n| עלות לליד | **בסיס** |\n\n> משפט המיצוב\n\n---\n\nסוף")
    assert [b["t"] for b in blocks] == ["table", "quote", "p"], "the rule is dropped"
    table = blocks[0]["rows"]
    assert len(table) == 2 and table[1][1] == [{"text": "בסיס", "bold": True}]


def test_heading_levels_shift_so_the_shallowest_is_one():
    blocks = bd.from_markdown("## סעיף\n### תת סעיף\n#### עמוק")
    assert [b["level"] for b in blocks] == [1, 2, 3]


def test_inline_runs():
    assert bd.runs("רגיל **מודגש** *נטוי* https://x.example/a.") == [
        {"text": "רגיל "}, {"text": "מודגש", "bold": True}, {"text": " "},
        {"text": "נטוי", "italic": True}, {"text": " "},
        {"text": "https://x.example/a", "url": "https://x.example/a"}, {"text": "."}]


def test_model_output_cannot_inject_markup():
    xml = _parts(bd.from_markdown("<w:p>טקסט</w:p> & עוד"))["word/document.xml"].decode()
    assert "&lt;w:p&gt;טקסט&lt;/w:p&gt; &amp; עוד" in xml


def test_hebrew_runs_are_marked_rtl_and_latin_ones_are_not():
    xml = _parts([{"t": "p", "runs": [{"text": "שלום"}, {"text": "Meta"}]}])["word/document.xml"].decode()
    hebrew, latin = xml.split("שלום")[0].rsplit("<w:r>", 1)[1], xml.split("Meta")[0].rsplit("<w:r>", 1)[1]
    assert "<w:rtl/>" in hebrew and "<w:rtl/>" not in latin


def test_hebrew_date():
    from datetime import date
    assert bd.hebrew_date(date(2026, 9, 23)) == "23 בספטמבר 2026"


def test_a_link_in_a_hebrew_line_keeps_its_trailing_slash():
    xml = _parts(bd.from_markdown("קישור: https://a.example/"))["word/document.xml"].decode()
    assert "https://a.example/\u200e</w:t>" in xml
