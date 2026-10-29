"""A client's own version of the contract: Dror edits the standard one before sending.

The standard contract stays in ``templates/contract_he.html`` (its history in git).
A client's version is a full copy of that template with Dror's changes, kept in
the contract store (:mod:`src.lib.contract_store`): a copy, not a list of edits,
so a later change to the standard can never alter a contract a client already
holds. At sending it is frozen for the signing page (``contract_store.freeze_sent``),
so what the client reads and signs is what Dror approved, even if he edits again.

The unit of editing is a **block**: one top-level element of the contract (a
heading, a paragraph, a list, a separator). A block holding a placeholder is
**locked**: names, ID numbers, prices and the signatures come from ClickUp and the
signing page, so the price in clause 10 and the one in נספח א׳ cannot disagree,
and a contract still cannot go out with a blank where a term should be.

Clause numbers follow the edits: deleting clause 7 makes 8 the new 7, its
sub-clauses (8.1 -> 7.1) and the references to it ("בסעיף 8") with it. A reference
to a deleted clause becomes a placeholder that refuses to render, so the contract
cannot reach a client pointing at a clause that is gone.
"""

from __future__ import annotations

import difflib
import hashlib
import html
import re
from dataclasses import dataclass
from typing import Any, Optional

from . import contract

# The blocks Dror can rewrite, and what he can add.
EDITABLE = {"h2", "h3", "p", "ul", "ol"}
NEW_KINDS = {"h2", "p", "ul", "hr"}
_VOID = {"hr", "br", "img"}
_GAP = "\n\n  "  # between blocks, as the template writes it
MAX_TEXT = 6000
MAX_BLOCKS = 400

# Shown on a block that appears only for some clients.
SECTION_LABELS = {
    "has_strategy": "מופיע רק כשיש מחיר אסטרטגיה",
    "has_campaigns": "מופיע רק כשיש מחיר קמפיינים",
    "has_both": "מופיע רק כשיש את שני המחירים",
}

# How a locked value reads in the editor when the client's own value is not known yet.
FIELD_LABELS = {
    "client_name": "שם הלקוח", "client_business_id": "ת.ז / ח.פ", "client_address": "כתובת",
    "client_phone": "טלפון", "client_email": "מייל", "sign_date": "תאריך החתימה",
    "price_strategy": "מחיר אסטרטגיה", "price_campaigns": "מחיר קמפיינים", "price_total": "סך הכול",
}

_OPEN_DIV = re.compile(r'<div class="contract"[^>]*>\s*')
_MARKER = re.compile(r"\{\{([#/])(\w+)\}\}\s*")
_TAG = re.compile(r"<(\w+)")
_TRAIL = re.compile(r"\s*")
_NUM_H2 = re.compile(r"^(\d+)\.\s+")
_NUM_SUB = re.compile(r"^(\d+)\.(\d+)\s+")

# Renumbering, on the template's source.
_RENUM = re.compile(
    r"(?P<h2><h2>(?P<h2n>\d+)\.(?=\s))"
    r"|(?P<sub><(?P<subt>p|h3)>(?P<subc>\d+)\.(?P<subn>\d+)(?=\s))"
    r"|(?P<ph><h3>(?P<phc>\d+)\.(?=\{\{))"
    r"|(?P<ref>(?P<word>סעיפים|סעיף)(?P<sp>\s+)(?P<a>\d+)(?:-(?P<b>\d+))?)"
)
MISSING = "missing_clause_"


class Invalid(ValueError):
    """An edit that cannot be saved, said in Hebrew for the page."""


@dataclass
class Block:
    src: str           # the element and the whitespace after it
    tag: str           # h2, p, ul, ..., or "marker" for a {{#section}} line
    locked: bool
    section: str = ""  # the {{#flag}} it sits in, if any

    @property
    def element(self) -> str:
        return self.src.rstrip()


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def standard() -> str:
    return contract.load_template()


# ------------------------------------------------------------------ splitting


def split(template: str) -> tuple[str, list[Block], str]:
    """``(head, blocks, tail)``, losslessly: ``join`` gives back the same text."""
    m = _OPEN_DIV.search(template)
    end = template.rfind("</div>")
    if not m or end < m.end():
        raise Invalid("מבנה החוזה לא מזוהה")
    head, inner, tail = template[:m.end()], template[m.end():end], template[end:]
    blocks: list[Block] = []
    stack: list[str] = []
    pos = 0
    while pos < len(inner):
        mk = _MARKER.match(inner, pos)
        if mk:
            if mk.group(1) == "#":
                stack.append(mk.group(2))
            elif stack:
                stack.pop()
            blocks.append(Block(mk.group(0), "marker", True))
            pos = mk.end()
            continue
        tm = _TAG.match(inner, pos)
        if not tm:  # stray text: kept as it is, never edited
            nxt = inner.find("<", pos + 1)
            nxt = len(inner) if nxt < 0 else nxt
            blocks.append(Block(inner[pos:nxt], "text", True, stack[-1] if stack else ""))
            pos = nxt
            continue
        tag = tm.group(1).lower()
        stop = _element_end(inner, pos, tag)
        stop = _TRAIL.match(inner, stop).end()
        src = inner[pos:stop]
        locked = "{{" in src or tag not in EDITABLE | {"hr"}
        blocks.append(Block(src, tag, locked, stack[-1] if stack else ""))
        pos = stop
    return head, blocks, tail


def _element_end(text: str, pos: int, tag: str) -> int:
    close = text.find(">", pos)
    if tag in _VOID:
        return close + 1
    depth = 0
    for m in re.finditer(rf"<(/?){tag}\b[^>]*>", text[pos:]):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return pos + m.end()
    raise Invalid(f"תגית {tag} לא נסגרת בחוזה")


def join(head: str, blocks: list[Block], tail: str) -> str:
    return head + "".join(b.src for b in blocks) + tail


# ------------------------------------------------------------ text in, text out


def _plain(fragment: str) -> list[str]:
    """The lines of a fragment as a person reads them."""
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    lines = [" ".join(line.split()) for line in text.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return lines


def _open_tag(element: str) -> str:
    return element[:element.find(">") + 1]


def _inner(element: str) -> str:
    return element[element.find(">") + 1:element.rfind("</")]


def editable(block: Block) -> dict[str, Any]:
    """What the editor shows for a block: ``{"text", "num"}`` (``num``: it carries a
    clause number, kept and renumbered rather than typed)."""
    el = block.element
    if block.tag in ("ul", "ol"):
        items = re.findall(r"<li\b[^>]*>(.*?)</li>", el, re.DOTALL)
        return {"text": "\n".join(" ".join(_plain(i)) for i in items), "num": False}
    text = "\n".join(_plain(_inner(el)))
    pattern = _NUM_H2 if block.tag == "h2" else _NUM_SUB
    m = pattern.match(text) if block.tag in ("h2", "h3", "p") else None
    return {"text": text[m.end():] if m else text, "num": bool(m)}


def build(tag: str, text: str, *, num: bool = False, open_tag: str = "") -> str:
    """A block from what Dror typed. Everything typed is escaped: it is text, never markup."""
    text = str(text or "").replace("\r", "")
    if "{{" in text or "}}" in text:
        raise Invalid("אי אפשר להשתמש בסוגריים מסולסלים כפולים בטקסט של החוזה")
    if len(text) > MAX_TEXT:
        raise Invalid("קטע ארוך מדי")
    lines = [" ".join(line.split()) for line in text.split("\n")]
    lines = [line for line in lines if line]
    if tag == "hr":
        return "<hr>"
    if not lines:
        raise Invalid("קטע ריק. כדי להסיר קטע מוחקים אותו")
    esc = [html.escape(line, quote=False) for line in lines]
    if tag in ("h2", "h3"):
        prefix = ("0. " if tag == "h2" else "0.0 ") if num else ""
        return f"<{tag}>{prefix}{' '.join(esc)}</{tag}>"
    if tag == "p":
        opening = open_tag if open_tag.startswith("<p") else "<p>"
        if num:  # a numbered paragraph is a plain one: renumbering looks for "<p>N.M"
            opening = "<p>"
        return f"{opening}{'0.0 ' if num else ''}{'<br>'.join(esc)}</p>"
    if tag in ("ul", "ol"):
        opening = open_tag if open_tag.startswith(f"<{tag}") else f"<{tag}>"
        items = "".join(f"\n    <li>{line}</li>" for line in esc)
        return f"{opening}{items}\n  </{tag}>"
    raise Invalid(f"סוג קטע לא מוכר: {tag}")


# ----------------------------------------------------------------- numbering


def renumber(template: str) -> str:
    """Clauses numbered in order, their sub-clauses and references with them.

    A new clause arrives as "0." and takes its place. A reference to a clause
    that no longer exists becomes ``{{missing_clause_N}}``, which
    :func:`contract.render` refuses with a reason, wherever it is shown."""
    olds = [m.group("h2n") for m in _RENUM.finditer(template) if m.group("h2")]
    mapping: dict[str, str] = {}
    for new, old in enumerate(olds, start=1):
        if old != "0" and olds.count(old) == 1:
            mapping[old] = str(new)
    state = {"clause": "", "n": 0, "seq": 0}

    def ref(n: str) -> str:
        return mapping.get(n) or ("{{" + MISSING + n + "}}")

    def sub(m: re.Match[str]) -> str:
        if m.group("h2"):
            state["seq"] += 1
            state["clause"], state["n"] = str(state["seq"]), 0
            return f"<h2>{state['clause']}."
        if m.group("sub"):
            if not state["clause"]:
                return m.group(0)
            state["n"] += 1
            return f"<{m.group('subt')}>{state['clause']}.{state['n']}"
        if m.group("ph"):
            return f"<h3>{state['clause'] or m.group('phc')}."
        out = f"{m.group('word')}{m.group('sp')}{ref(m.group('a'))}"
        return out + (f"-{ref(m.group('b'))}" if m.group("b") else "")

    return _RENUM.sub(sub, template)


# -------------------------------------------------------------------- saving


def apply(current: str, items: list[dict[str, Any]], base: str) -> str:
    """The contract after an editor save, numbered and checked.

    ``items`` is the contract in order, as the editor ends it: ``{"i": n}`` keeps
    block n of ``current``, ``{"i": n, "text": ...}`` rewrites it, ``{"new": kind,
    "text": ..., "num": bool}`` adds one. A block left out is deleted. Locked
    blocks must all be there, nothing may move, and ``base`` must be the version
    the editor opened (someone else may have saved in between)."""
    if base != sha(current):
        raise Invalid("החוזה נשמר בינתיים ממקום אחר. כדאי לרענן את הדף ולערוך שוב.")
    if not isinstance(items, list) or not items or len(items) > MAX_BLOCKS:
        raise Invalid("אין מה לשמור")
    head, blocks, tail = split(current)
    out: list[Block] = []
    last = -1
    for it in items:
        if not isinstance(it, dict):
            raise Invalid("בקשה לא תקינה")
        if "i" in it:
            try:
                idx = int(it["i"])
            except (TypeError, ValueError):
                raise Invalid("בקשה לא תקינה") from None
            if not 0 <= idx < len(blocks) or idx <= last:
                raise Invalid("סדר הקטעים השתנה. כדאי לרענן את הדף ולערוך שוב.")
            last = idx
            b = blocks[idx]
            if "text" in it and not b.locked and b.tag in EDITABLE:
                old = editable(b)
                trail = b.src[len(b.element):] or _GAP
                b = Block(build(b.tag, it["text"], num=old["num"], open_tag=_open_tag(b.element)) + trail,
                          b.tag, False, b.section)
            out.append(b)
        else:
            kind = str(it.get("new") or "")
            if kind not in NEW_KINDS:
                raise Invalid("סוג קטע לא מוכר")
            out.append(Block(build(kind, it.get("text", ""), num=bool(it.get("num"))) + _GAP, kind, False))
    kept = [i for i, b in enumerate(blocks) if b.locked]
    present = [int(it["i"]) for it in items if "i" in it and blocks[int(it["i"])].locked]
    if present != kept:
        raise Invalid("אי אפשר למחוק קטע עם פרטים מ-ClickUp (שמות, מחירים, חתימות)")
    result = renumber(join(head, out, tail))
    split(result)  # still a contract the editor can open
    return result


# ------------------------------------------------------------- what changed


def _norm(block: Block) -> str:
    text = re.sub(r"<(h2|h3|p)>\d+\.(\d+)?", r"<\1>", block.element)
    text = re.sub(r"(סעיפים|סעיף)\s+(\d+|\{\{\w+\}\})(-(\d+|\{\{\w+\}\}))?", r"\1", text)
    return " ".join(text.split())


def compare(draft: str, base: Optional[str] = None) -> dict[str, Any]:
    """What a client's version changes against the standard: counts, and a mark
    per block of the draft (``"edited"``, ``"new"`` or ``""``)."""
    _h, std, _t = split(base if base is not None else standard())
    _h, mine, _t = split(draft)
    a, b = [_norm(x) for x in std], [_norm(x) for x in mine]
    marks = [""] * len(mine)
    edited = added = deleted = 0

    def counts(blocks: list[Block]) -> int:
        return sum(1 for x in blocks if x.tag not in ("hr", "marker", "text"))

    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        if op in ("replace", "insert"):
            for j in range(j1, j2):
                marks[j] = "edited" if op == "replace" and j - j1 < i2 - i1 else "new"
            edited += counts(mine[j1:min(j2, j1 + (i2 - i1))]) if op == "replace" else 0
            added += counts(mine[j1 + (i2 - i1):j2]) if op == "replace" else counts(mine[j1:j2])
            if op == "replace":
                deleted += counts(std[i1 + (j2 - j1):i2])
        else:
            deleted += counts(std[i1:i2])
    return {"edited": edited, "added": added, "deleted": deleted,
            "changes": edited + added + deleted, "marks": marks}


def summary_text(c: dict[str, Any]) -> str:
    """"עריכה של 2 קטעים, 1 נוסף" or "" for the standard."""
    parts = []
    if c.get("edited"):
        parts.append(f"{c['edited']} נערכו")
    if c.get("added"):
        parts.append(f"{c['added']} נוספו")
    if c.get("deleted"):
        parts.append(f"{c['deleted']} נמחקו")
    return ", ".join(parts)


def marked(template: str, marks: list[str]) -> str:
    """The template with changed blocks wrapped for the preview (never sent)."""
    head, blocks, tail = split(template)
    out = []
    for b, mark in zip(blocks, marks):
        if mark and not b.locked and b.tag != "hr":
            label = "נערך" if mark == "edited" else "נוסף"
            out.append(Block(f'<div class="ce-mark ce-{mark}" data-mark="{label}">{b.element}</div>'
                             + b.src[len(b.element):], b.tag, False))
        else:
            out.append(b)
    return join(head, out, tail)


# ------------------------------------------------------------- the editor view


def _chip(value: str) -> str:
    return f'<span class="ce-chip">{html.escape(value)}</span>'


def display(block: Block, fields: dict[str, str]) -> str:
    """A block as the editor shows it: a locked block with its values filled in
    (or named, where the client fills them on the signing page)."""
    el = block.element
    if not block.locked:
        return el
    raw = contract.assets()

    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if name in raw:
            return raw[name]
        if name in contract.SIGNATURE_FIELDS:
            return ""
        if name.startswith("n_"):
            return html.escape(str(fields.get(name) or "1"))
        if name.startswith(MISSING):
            return _chip(f"סעיף שנמחק ({name[len(MISSING):]})")
        value = str(fields.get(name) or "").strip()
        return _chip(value or FIELD_LABELS.get(name, "פרטי נותן השירות" if name.startswith("provider_") else name))

    return re.sub(r"\{\{(\w+)\}\}", sub, el)


def editor_blocks(template: str, fields: dict[str, str]) -> list[dict[str, Any]]:
    """Every block with what the editor needs: index, kind, lock, its text, how it reads."""
    _h, blocks, _t = split(template)
    out = []
    for i, b in enumerate(blocks):
        if b.tag == "marker":  # {{#has_x}}: kept in place, never shown
            out.append({"i": i, "kind": b.tag, "locked": True, "hidden": True})
            continue
        item: dict[str, Any] = {"i": i, "kind": b.tag, "locked": b.locked, "html": display(b, fields),
                                "section": SECTION_LABELS.get(b.section, "") if b.section else ""}
        if not b.locked and b.tag in EDITABLE:
            item.update(editable(b))
        out.append(item)
    return out
