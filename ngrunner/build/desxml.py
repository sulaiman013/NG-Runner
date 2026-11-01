"""
The few XML tools the room writer, its products and its gates share: a balanced scanner over a .des's
tags, a start tag's attributes, one attribute changed, and numbers as Jim writes them.

Why not an XML library: a written room must keep the template's bytes wherever nothing changes (the
attribute order, the spacing, CRLF, the version line before the XML declaration), and Mozaik's files
are edited in place by Jim's own scripts the same way. BUILD 146-147 asks for a balanced-element scanner,
not a non-greedy regex (build_jobs_0914.py 14-29 is his); this one finds a tag's end past quoted values,
so a '>' inside an attribute never ends a tag. gates.py still parses every written room with an XML
parser, as Jim's builder does before it calls a room done (build_jobs_0914.py 159-163).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CRLF = "\r\n"

_TAG = re.compile(r"<(?:!--.*?--|\?.*?\?|(?P<close>/)?(?P<name>[A-Za-z_][\w.:-]*)"
                  r"(?P<attrs>(?:[^>\"']|\"[^\"]*\"|'[^']*')*?)(?P<empty>/)?)>", re.S)
_ATTR = re.compile(r'([\w:.-]+)="([^"]*)"')


class XmlError(Exception):
    """A room or product isn't the XML the writer expects: plain words."""


@dataclass(frozen=True)
class Element:
    """One element of a text: its name, where it starts and ends, and where its start tag ends."""
    name: str
    start: int
    end: int
    head_end: int

    def head(self, text: str) -> str:
        return text[self.start:self.head_end]

    def of(self, text: str) -> str:
        return text[self.start:self.end]


def elements(text: str, name: str, *, within: tuple[int, int] | None = None) -> list[Element]:
    """Every outermost element called name, any nested one of the same name inside it."""
    lo, hi = within or (0, len(text))
    out: list[Element] = []
    depth, start, head_end = 0, 0, 0
    for m in _TAG.finditer(text, lo, hi):
        if m["name"] != name:
            continue
        if m["close"]:
            depth -= 1
            if depth < 0:
                raise XmlError(f"a </{name}> closes nothing")
            if depth == 0:
                out.append(Element(name, start, m.end(), head_end))
        elif m["empty"]:
            if depth == 0:
                out.append(Element(name, m.start(), m.end(), m.end()))
        else:
            if depth == 0:
                start, head_end = m.start(), m.end()
            depth += 1
    if depth:
        raise XmlError(f"a <{name}> is never closed")
    return out


def one(text: str, name: str, within: tuple[int, int] | None = None) -> Element:
    found = elements(text, name, within=within)
    if len(found) != 1:
        raise XmlError(f"{len(found)} <{name}> where one is needed")
    return found[0]


def start_tags(text: str, name: str) -> list[str]:
    """Every start (or empty) tag called name, wherever it is."""
    return [m.group(0) for m in _TAG.finditer(text) if m["name"] == name and not m["close"]]


def attrs(tag: str) -> dict[str, str]:
    """A start tag's attributes, as written."""
    return dict(_ATTR.findall(tag))


def set_attr(tag: str, name: str, value: str) -> str:
    """The start tag with one attribute's value changed; it must be there exactly once."""
    pattern = re.compile(rf'(?<=\s){re.escape(name)}="[^"]*"')
    if len(pattern.findall(tag)) != 1:
        raise XmlError(f"{tag.split()[0]}> has no single {name}")
    return pattern.sub(lambda _: f'{name}="{value}"', tag, count=1)


def map_tags(text: str, name: str, change: object) -> str:
    """text with every start tag called name replaced by change(tag)."""
    def one_tag(m: re.Match[str]) -> str:
        if m["name"] != name or m["close"]:
            return m.group(0)
        return change(m.group(0))  # type: ignore[operator]
    return _TAG.sub(one_tag, text)


def num(value: float) -> str:
    """A number as BUILD 290-291 writes it: three places, trailing zeros stripped, never -0."""
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def crlf(text: str) -> str:
    """Every line ending CRLF (a live .des is pure CRLF, build_jobs_0914.py 10; the templates are LF)."""
    return re.sub(r"\r?\n", CRLF, text)
