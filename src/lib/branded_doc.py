"""The documents the system writes for Dror, as branded Word files.

The strategy, the social prep report and the questionnaire answers land in the
client's folder as Google Docs Dror edits before anything reaches the client.
They used to go in as HTML, and Drive's HTML import has no page header or footer:
each came out as a bare page of text. A ``.docx`` carries a real header (the
brand band on every page), a real footer (Dror's details and the page number)
and real styles, and Drive converts it into a Google Doc faithfully. The Docs API
could add the same after the fact, but it is not enabled in the Google project;
this needs nothing beyond the Drive access we already have.

The file is written by hand (it is a zip of a few XML parts) rather than with
python-docx: RTL needs the raw XML anyway (``w:bidi``, ``w:rtl``, ``w:bidiVisual``),
and it keeps lxml out of the Lambda package.

Content comes in as blocks, built from Markdown (what Claude writes) by
:func:`from_markdown`, or directly (the questionnaire answers).
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date
from pathlib import Path
from typing import Any, Optional
from xml.sax.saxutils import escape

from . import config

BAND = Path(__file__).resolve().parents[2] / "templates" / "assets" / "doc_band.png"

SITE = "drorbrk.co.il"
TAGLINE = "ליווי עסקים והגדלת מכירות"

FONT = "Heebo"
INK, MUTED, BRAND, BRAND_INK, LINE, SOFT = "1F2328", "5B6472", "1F6FD6", "1557AD", "DDE3EA", "EEF4FC"

_MONTHS = ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט",
           "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"]

Block = dict[str, Any]
Run = dict[str, Any]


def hebrew_date(d: Optional[date] = None) -> str:
    d = d or date.today()
    return f"{d.day} ב{_MONTHS[d.month - 1]} {d.year}"


# ------------------------------------------------------------------- markdown

_INLINE = re.compile(
    r"\*\*(?P<bold>.+?)\*\*"
    r"|\[(?P<ltext>[^\]]+)\]\((?P<lurl>https?://[^)\s]+)\)"
    r"|(?P<url>https?://[^\s<>()\]]+[^\s<>()\].,;:!?'\"])"
    r"|(?<![\*\w])\*(?!\s)(?P<ital>.+?)(?<!\s)\*(?![\*\w])"
)


def runs(text: str) -> list[Run]:
    """Inline Markdown (bold, italic, links, bare URLs) as styled runs."""
    out: list[Run] = []
    pos = 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            out.append({"text": text[pos:m.start()]})
        if m.group("bold") is not None:
            out.append({"text": m.group("bold"), "bold": True})
        elif m.group("ltext") is not None:
            out.append({"text": m.group("ltext"), "url": m.group("lurl")})
        elif m.group("url") is not None:
            out.append({"text": m.group("url"), "url": m.group("url")})
        else:
            out.append({"text": m.group("ital"), "italic": True})
        pos = m.end()
    if pos < len(text):
        out.append({"text": text[pos:]})
    return out


def _joined(lines: list[str]) -> list[Run]:
    out: list[Run] = []
    for i, line in enumerate(lines):
        if i:
            out.append({"br": True})
        out.extend(runs(line))
    return out


def from_markdown(markdown: str) -> list[Block]:
    """Headings, paragraphs, bullet/numbered lists, tables and quotes.

    Heading levels are shifted so the shallowest one is level 1: the document
    title is the title block, so a strategy whose sections are ``##`` still gets
    top-level section headings. A blank line ends a paragraph but not a list
    (Claude spaces its numbered items; closing the list there restarted every
    item at 1), and an indented line continues its list item. Horizontal rules
    are dropped: the headings already separate the sections.
    """
    blocks: list[Block] = []
    para: list[str] = []
    quote: list[str] = []
    rows: list[list[list[Run]]] = []
    lst: Optional[Block] = None

    def flush_para() -> None:
        if para:
            blocks.append({"t": "p", "runs": _joined(para)})
            para.clear()

    def flush_quote() -> None:
        if quote:
            blocks.append({"t": "quote", "runs": _joined(quote)})
            quote.clear()

    def flush_table() -> None:
        if rows:
            blocks.append({"t": "table", "rows": [list(r) for r in rows]})
            rows.clear()

    def flush_list() -> None:
        nonlocal lst
        if lst:
            blocks.append(lst)
        lst = None

    def flush_all() -> None:
        flush_para(); flush_quote(); flush_table(); flush_list()

    for raw in markdown.replace("\r\n", "\n").split("\n"):
        stripped = raw.strip()
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        bullet = re.match(r"^[-*•]\s+(.*)$", stripped)
        numbered = re.match(r"^(\d+)[.)]\s+(.*)$", stripped)
        if not stripped:
            flush_para(); flush_quote(); flush_table()
        elif stripped.startswith("|") and stripped.endswith("|") and len(stripped) > 1:
            flush_para(); flush_quote(); flush_list()
            cells = [c.strip() for c in stripped[1:-1].split("|")]
            if not all(re.fullmatch(r":?-{3,}:?", c) for c in cells):  # the |---| under the header
                rows.append([runs(c) for c in cells])
        elif stripped.startswith(">"):
            flush_para(); flush_table(); flush_list()
            quote.append(stripped[1:].strip())
        elif heading:
            flush_all()
            blocks.append({"t": "h", "level": len(heading.group(1)), "runs": runs(heading.group(2))})
        elif re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            flush_all()
        elif bullet or numbered:
            flush_para(); flush_quote(); flush_table()
            ordered = bool(numbered)
            if lst and lst["ordered"] != ordered:
                flush_list()
            if not lst:
                lst = {"t": "list", "ordered": ordered, "items": [],
                       "start": int(numbered.group(1)) if numbered else 1}
            lst["items"].append(runs(numbered.group(2) if numbered else bullet.group(1)))
        elif lst and raw[:1] in (" ", "\t"):
            lst["items"][-1] += [{"br": True}] + runs(stripped)
        else:
            flush_quote(); flush_table(); flush_list()
            para.append(stripped)
    flush_all()

    levels = [b["level"] for b in blocks if b["t"] == "h"]
    if levels:
        shift = min(levels) - 1
        for b in blocks:
            if b["t"] == "h":
                b["level"] = min(b["level"] - shift, 3)
    return blocks


# ----------------------------------------------------------------------- docx

_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"'
)
_HEBREW = re.compile(r"[֐-׿]")
_BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# A4, 2 cm side margins: 17 cm of text. The band spans it.
_PAGE_W, _PAGE_H, _MARGIN = 11906, 16838, 1134
_BAND_CX = 6120000                       # 17 cm in EMU
_BAND_CY = _BAND_CX * 150 // 2000        # the PNG is 2000 x 150


def _t(text: str) -> str:
    return escape(_BAD_XML.sub("", text))


class _Doc:
    """Accumulates the body XML and the hyperlink relationships it needs."""

    def __init__(self) -> None:
        self.body: list[str] = []
        self.links: list[str] = []
        self.nums: list[tuple[bool, int]] = []  # one numbering instance per list

    def link_id(self, url: str) -> str:
        self.links.append(url)
        return f"rIdL{len(self.links)}"

    def run(self, r: Run, *, size: int = 22, color: str = INK, bold: bool = False) -> str:
        if r.get("br"):
            return "<w:r><w:br/></w:r>"
        text = r.get("text") or ""
        b = bold or r.get("bold")
        props = [f'<w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}"/>']
        if b:
            props.append("<w:b/><w:bCs/>")
        if r.get("italic"):
            props.append("<w:i/><w:iCs/>")
        props.append(f'<w:color w:val="{BRAND if r.get("url") else color}"/>')
        props.append(f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>')
        if r.get("url"):
            props.append('<w:u w:val="single"/>')
        if _HEBREW.search(text):
            props.append("<w:rtl/>")
        xml = f'<w:r><w:rPr>{"".join(props)}</w:rPr><w:t xml:space="preserve">{_t(text)}</w:t></w:r>'
        if r.get("url"):
            xml = f'<w:hyperlink r:id="{self.link_id(r["url"])}">{xml}</w:hyperlink>'
        return xml

    def para(self, content: list[Run], *, style: str = "", size: int = 22, color: str = INK,
             bold: bool = False, extra_ppr: str = "") -> None:
        ppr = f'<w:pStyle w:val="{style}"/>' if style else ""
        self.body.append(f"<w:p><w:pPr>{ppr}<w:bidi/>{extra_ppr}</w:pPr>"
                         + "".join(self.run(r, size=size, color=color, bold=bold) for r in content)
                         + "</w:p>")

    def block(self, b: Block) -> None:
        kind = b["t"]
        if kind == "h":
            level = b["level"]
            size, color = {1: (32, BRAND_INK), 2: (26, INK), 3: (23, BRAND)}[level]
            self.para(b["runs"], style=f"Heading{level}", size=size, color=color, bold=True)
        elif kind == "p":
            self.para(b["runs"])
        elif kind == "quote":
            self.para(b["runs"], style="Quote", color=BRAND_INK, size=23)
        elif kind == "field":
            self.para([{"text": b["label"]}], style="FieldLabel", size=20, color=MUTED, bold=True)
            self.para(_joined(str(b["value"]).split("\n")), style="FieldValue")
        elif kind == "list":
            self.nums.append((b["ordered"], b.get("start", 1)))
            num_id = len(self.nums) + 1   # numId 1 is reserved below
            for item in b["items"]:
                self.para(item, style="ListParagraph",
                          extra_ppr=f'<w:numPr><w:ilvl w:val="0"/><w:numId w:val="{num_id}"/></w:numPr>')
        elif kind == "table":
            self.table(b["rows"])

    def table(self, rows: list[list[list[Run]]]) -> None:
        width = max(len(r) for r in rows)
        col = (_PAGE_W - 2 * _MARGIN) // width
        border = "".join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="{LINE}"/>'
                         for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
        xml = [f'<w:tbl><w:tblPr><w:bidiVisual/><w:tblW w:w="{col * width}" w:type="dxa"/>'
               f"<w:tblBorders>{border}</w:tblBorders>"
               '<w:tblCellMar><w:top w:w="90" w:type="dxa"/><w:start w:w="140" w:type="dxa"/>'
               '<w:left w:w="140" w:type="dxa"/><w:bottom w:w="90" w:type="dxa"/>'
               '<w:end w:w="140" w:type="dxa"/><w:right w:w="140" w:type="dxa"/></w:tblCellMar>'
               "</w:tblPr><w:tblGrid>" + f'<w:gridCol w:w="{col}"/>' * width + "</w:tblGrid>"]
        for i, row in enumerate(rows):
            head = i == 0
            xml.append("<w:tr>" + ("<w:trPr><w:tblHeader/></w:trPr>" if head else ""))
            for cell in row + [[]] * (width - len(row)):
                shade = f'<w:shd w:val="clear" w:color="auto" w:fill="{SOFT}"/>' if head else ""
                xml.append(f'<w:tc><w:tcPr><w:tcW w:w="{col}" w:type="dxa"/>{shade}</w:tcPr>'
                           '<w:p><w:pPr><w:bidi/><w:spacing w:before="0" w:after="0"/></w:pPr>'
                           + "".join(self.run(r, size=20, bold=head, color=BRAND_INK if head else INK)
                                     for r in cell)
                           + "</w:p></w:tc>")
            xml.append("</w:tr>")
        xml.append("</w:tbl>")
        self.body.append("".join(xml))
        self.body.append('<w:p><w:pPr><w:bidi/><w:spacing w:before="0" w:after="60"/></w:pPr></w:p>')


def _styles() -> str:
    def style(sid: str, name: str, ppr: str = "", rpr: str = "", kind: str = "paragraph") -> str:
        return (f'<w:style w:type="{kind}" w:styleId="{sid}"><w:name w:val="{name}"/>'
                f'<w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:bidi/>{ppr}</w:pPr><w:rPr>{rpr}</w:rPr></w:style>')

    def heading(level: int, size: int, color: str, before: int, border: bool = False) -> str:
        bdr = (f'<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="4" w:color="{LINE}"/></w:pBdr>'
               if border else "")
        return style(f"Heading{level}", f"heading {level}",
                     f'<w:keepNext/>{bdr}<w:spacing w:before="{before}" w:after="120"/>'
                     f'<w:outlineLvl w:val="{level - 1}"/>',
                     f'<w:b/><w:bCs/><w:color w:val="{color}"/><w:sz w:val="{size}"/><w:szCs w:val="{size}"/>')

    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {_NS}>'
        '<w:docDefaults><w:rPrDefault><w:rPr>'
        f'<w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:eastAsia="{FONT}" w:cs="{FONT}"/>'
        f'<w:color w:val="{INK}"/><w:sz w:val="22"/><w:szCs w:val="22"/>'
        '<w:lang w:val="he-IL" w:bidi="he-IL"/></w:rPr></w:rPrDefault>'
        '<w:pPrDefault><w:pPr><w:bidi/><w:spacing w:after="140" w:line="312" w:lineRule="auto"/>'
        '<w:jc w:val="start"/></w:pPr></w:pPrDefault></w:docDefaults>'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/>'
        '<w:pPr><w:bidi/></w:pPr></w:style>'
        + style("Title", "Title", '<w:spacing w:before="120" w:after="60"/>',
                f'<w:b/><w:bCs/><w:color w:val="{INK}"/><w:sz w:val="48"/><w:szCs w:val="48"/>')
        + style("Subtitle", "Subtitle",
                f'<w:pBdr><w:bottom w:val="single" w:sz="12" w:space="10" w:color="{BRAND}"/></w:pBdr>'
                '<w:spacing w:after="360"/>',
                f'<w:color w:val="{MUTED}"/><w:sz w:val="22"/><w:szCs w:val="22"/>')
        + heading(1, 32, BRAND_INK, 400, border=True)
        + heading(2, 26, INK, 300)
        + heading(3, 23, BRAND, 220)
        + style("Quote", "Quote",
                f'<w:pBdr><w:start w:val="single" w:sz="18" w:space="10" w:color="{BRAND}"/>'
                f'<w:right w:val="single" w:sz="18" w:space="10" w:color="{BRAND}"/></w:pBdr>'
                f'<w:shd w:val="clear" w:color="auto" w:fill="{SOFT}"/>'
                '<w:spacing w:before="120" w:after="200"/><w:ind w:start="240" w:end="240" w:right="240" w:left="240"/>',
                f'<w:color w:val="{BRAND_INK}"/>')
        + style("ListParagraph", "List Paragraph", '<w:spacing w:after="80"/><w:contextualSpacing/>')
        + style("FieldLabel", "Field Label", '<w:keepNext/><w:spacing w:before="200" w:after="20"/>',
                f'<w:b/><w:bCs/><w:color w:val="{MUTED}"/><w:sz w:val="20"/><w:szCs w:val="20"/>')
        + style("FieldValue", "Field Value", '<w:spacing w:after="120"/>')
        + style("Footer", "footer", '<w:jc w:val="center"/><w:spacing w:after="0"/>',
                f'<w:color w:val="{MUTED}"/><w:sz w:val="17"/><w:szCs w:val="17"/>')
        + "</w:styles>"
    )


def _numbering(nums: list[tuple[bool, int]]) -> str:
    def abstract(aid: int, fmt: str, text: str) -> str:
        return (f'<w:abstractNum w:abstractNumId="{aid}"><w:multiLevelType w:val="singleLevel"/>'
                f'<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="{fmt}"/>'
                f'<w:lvlText w:val="{text}"/><w:lvlJc w:val="start"/>'
                '<w:pPr><w:bidi/><w:ind w:start="720" w:right="720" w:hanging="360"/></w:pPr>'
                f'<w:rPr><w:color w:val="{BRAND}"/><w:b/><w:bCs/></w:rPr></w:lvl></w:abstractNum>')

    instances = ['<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>']
    for i, (ordered, start) in enumerate(nums, start=2):
        override = (f'<w:lvlOverride w:ilvl="0"><w:startOverride w:val="{start}"/></w:lvlOverride>'
                    if ordered else "")
        instances.append(f'<w:num w:numId="{i}"><w:abstractNumId w:val="{1 if ordered else 0}"/>{override}</w:num>')
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:numbering {_NS}>'
            + abstract(0, "bullet", "•") + abstract(1, "decimal", "%1.")
            + "".join(instances) + "</w:numbering>")


def _header() -> str:
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:hdr {_NS}>'
        '<w:p><w:pPr><w:bidi/><w:jc w:val="center"/><w:spacing w:after="0"/></w:pPr><w:r><w:drawing>'
        '<wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{_BAND_CX}" cy="{_BAND_CY}"/><wp:docPr id="1" name="דרור ברק"/>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic><pic:nvPicPr><pic:cNvPr id="1" name="band.png"/><pic:cNvPicPr/></pic:nvPicPr>'
        '<pic:blipFill><a:blip r:embed="rIdBand"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{_BAND_CX}" cy="{_BAND_CY}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
        '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p></w:hdr>'
    )


def footer_line() -> str:
    """Dror's details, as the footer of every document and report page."""
    digits = re.sub(r"\D", "", config.get("PROVIDER_PHONE", ""))
    phone = f"{digits[:3]}-{digits[3:]}" if len(digits) == 10 else digits
    name = config.get("PROVIDER_NAME") or "דרור ברק"
    return "   |   ".join(x for x in (name, TAGLINE, SITE, phone) if x)


def _footer() -> str:
    line = footer_line()

    def r(text: str) -> str:
        rtl = "<w:rtl/>" if _HEBREW.search(text) else ""
        return f'<w:r><w:rPr>{rtl}</w:rPr><w:t xml:space="preserve">{_t(text)}</w:t></w:r>'

    page = ('<w:fldSimple w:instr=" PAGE "><w:r><w:t>1</w:t></w:r></w:fldSimple>')
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr {_NS}>'
        f'<w:p><w:pPr><w:pStyle w:val="Footer"/><w:bidi/>'
        f'<w:pBdr><w:top w:val="single" w:sz="4" w:space="8" w:color="{LINE}"/></w:pBdr></w:pPr>'
        f"{r(line)}</w:p>"
        f'<w:p><w:pPr><w:pStyle w:val="Footer"/><w:bidi/></w:pPr>{r("עמוד ")}{page}</w:p></w:ftr>'
    )


def build(title: str, subtitle: str, blocks: list[Block]) -> bytes:
    """The ``.docx`` bytes: title block, then ``blocks``, band header, footer."""
    doc = _Doc()
    doc.para([{"text": title}], style="Title", size=48, bold=True)
    doc.para([{"text": subtitle}], style="Subtitle", color=MUTED)
    for b in blocks:
        doc.block(b)

    body = "".join(doc.body)
    sect = (
        '<w:sectPr><w:headerReference w:type="default" r:id="rIdHdr"/>'
        '<w:footerReference w:type="default" r:id="rIdFtr"/>'
        f'<w:pgSz w:w="{_PAGE_W}" w:h="{_PAGE_H}"/>'
        f'<w:pgMar w:top="1800" w:right="{_MARGIN}" w:bottom="1500" w:left="{_MARGIN}" '
        'w:header="560" w:footer="560" w:gutter="0"/><w:bidi/></w:sectPr>'
    )
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {_NS}>'
                f"<w:body>{body}{sect}</w:body></w:document>")

    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    links = "".join(f'<Relationship Id="rIdL{i}" Type="{rel}/hyperlink" Target="{escape(u, {chr(34): "&quot;"})}" '
                    f'TargetMode="External"/>' for i, u in enumerate(doc.links, start=1))
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rIdStyles" Type="{rel}/styles" Target="styles.xml"/>'
        f'<Relationship Id="rIdNum" Type="{rel}/numbering" Target="numbering.xml"/>'
        f'<Relationship Id="rIdHdr" Type="{rel}/header" Target="header1.xml"/>'
        f'<Relationship Id="rIdFtr" Type="{rel}/footer" Target="footer1.xml"/>'
        f"{links}</Relationships>"
    )
    hdr_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rIdBand" Type="{rel}/image" Target="media/band.png"/></Relationships>'
    )
    wml = "application/vnd.openxmlformats-officedocument.wordprocessingml"
    types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        f'<Override PartName="/word/document.xml" ContentType="{wml}.document.main+xml"/>'
        f'<Override PartName="/word/styles.xml" ContentType="{wml}.styles+xml"/>'
        f'<Override PartName="/word/numbering.xml" ContentType="{wml}.numbering+xml"/>'
        f'<Override PartName="/word/header1.xml" ContentType="{wml}.header+xml"/>'
        f'<Override PartName="/word/footer1.xml" ContentType="{wml}.footer+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{rel}/officeDocument" Target="word/document.xml"/></Relationships>'
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("word/styles.xml", _styles())
        z.writestr("word/numbering.xml", _numbering(doc.nums))
        z.writestr("word/header1.xml", _header())
        z.writestr("word/_rels/header1.xml.rels", hdr_rels)
        z.writestr("word/footer1.xml", _footer())
        z.writestr("word/media/band.png", BAND.read_bytes())
    return buf.getvalue()


DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
