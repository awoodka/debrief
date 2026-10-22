"""Clean fetched text before it goes into a structured field.

Everything fetched is untrusted data that the triage model and the skill will read later, so text
fields are stored plain and bounded: HTML stripped, control and invisible characters removed
(zero-width and bidi overrides can hide text from a human reviewer), whitespace collapsed, and
length capped. Raw responses keep the original bytes.
"""
import re
from html import unescape

_TAGS = re.compile(r"<[^>]+>")
_BLOCK_TAGS = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|blockquote|pre|tr)\b[^>]*>", re.I)
_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def text(value, max_chars=None, keep_paragraphs=False):
    """Plain text from a possibly-HTML string. With keep_paragraphs, block elements become
    blank lines; otherwise all whitespace collapses to single spaces."""
    s = str(value or "")
    if "<" in s:
        if keep_paragraphs:
            s = _BLOCK_TAGS.sub("\n\n", s)
        s = _TAGS.sub(" ", s)
    s = unescape(s)
    s = _INVISIBLE.sub("", _CONTROL.sub("", s))
    if keep_paragraphs:
        paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n", s)]
        s = "\n\n".join(p for p in paras if p)
    else:
        s = " ".join(s.split())
    if max_chars and len(s) > max_chars:
        cut = s[: max_chars - 1]
        s = (cut.rsplit(" ", 1)[0] if " " in cut[-40:] else cut) + "…"
    return s
