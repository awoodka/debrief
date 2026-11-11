import io
import re
import unicodedata

import pypdf

MAX_BYTES = 2_000_000
MAX_PAGES = 10

_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class ResumeError(Exception):
    pass


def text_from_upload(data):
    if len(data) > MAX_BYTES:
        raise ResumeError(f"That file is over {MAX_BYTES // 1_000_000} MB. Save a smaller copy, or paste the text into the box.")
    text = tidy(_pdf_text(data) if data.startswith(b"%PDF-") else _plain_text(data))
    if not text:
        raise ResumeError("No text found in that file. If it's a scanned PDF, paste the text into the box.")
    return text


def tidy(text):
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(_CONTROL.sub(" ", _INVISIBLE.sub("", line)).split()) for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _plain_text(data):
    if b"\x00" not in data:
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            pass
    raise ResumeError("That file isn't a PDF or plain text. Save it as a PDF (Word can), or paste the text into the box.")


def _pdf_text(data):
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ResumeError("That PDF is locked with a password. Save a copy without one, or paste the text into the box.")
        if len(reader.pages) > MAX_PAGES:
            raise ResumeError(f"That PDF has {len(reader.pages)} pages, and at most {MAX_PAGES} are read. "
                              "Paste the resume's text into the box instead.")
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except ResumeError:
        raise
    except Exception as e:  # a broken file can fail anywhere inside pypdf
        raise ResumeError("That PDF couldn't be read. Save it again as a PDF, or paste the text into the box.") from e
