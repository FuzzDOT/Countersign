"""Turning an uploaded file into `documents.raw_text`, exactly once.

Plan §3: extraction happens at upload and the string it produces is the sole
coordinate system for every offset in the database. Nothing downstream
re-extracts, re-decodes or re-normalizes — if it did, a citation stored at
hour 6 would point somewhere else at hour 20.

So this module has one job and two rules:

- It returns the text it built and nothing derived from it. Callers store that
  string verbatim.
- Text is *constructed* (headers, then a blank line, then the body; cells
  joined by a separator we chose), never reassembled by searching. Anything
  that wanted an offset into the result could compute it here from the running
  length, which is what the CSV path does for its rows.

The size limits are security, not tidiness (plan §5). A 2 MB PDF can extract to
tens of megabytes of text, so the per-file byte cap the brief specifies does
not bound the work — `max_pdf_pages`, `max_pdf_chars` and a per-page deadline
do.
"""

from __future__ import annotations

import contextlib
import csv
import io
import time
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from typing import Any

from api.errors import UnsupportedMedia, ValidationFailed
from core.config import Settings
from core.logging import get_logger

log = get_logger(__name__)

# Brief §5. Kept in sync with api/v1/documents.ALLOWED_MEDIA_TYPES, which is
# the request-level allowlist; this is the dispatch table.
TEXT_PLAIN = "text/plain"
TEXT_CSV = "text/csv"
APPLICATION_PDF = "application/pdf"
MESSAGE_RFC822 = "message/rfc822"

SUPPORTED_MEDIA_TYPES = frozenset({TEXT_PLAIN, TEXT_CSV, APPLICATION_PDF, MESSAGE_RFC822})

# Extension fallback, because browsers send `application/octet-stream` for
# `.eml` often enough that rejecting on media type alone would fail a judge's
# real mailbox export.
EXTENSION_MEDIA_TYPES: dict[str, str] = {
    ".txt": TEXT_PLAIN,
    ".text": TEXT_PLAIN,
    ".md": TEXT_PLAIN,
    ".csv": TEXT_CSV,
    ".tsv": TEXT_CSV,
    ".pdf": APPLICATION_PDF,
    ".eml": MESSAGE_RFC822,
}

# Headers kept in `raw_text` for a .eml, in this order. An email's routing
# metadata is evidence — "who sent the invoice" is frequently the whole point —
# so it is part of the document rather than something stripped before tagging.
EMAIL_HEADERS = ("From", "To", "Cc", "Date", "Subject")

# Encodings tried in order. The last one cannot fail, which is deliberate: a
# mis-decoded byte produces a replacement character in a string we then treat
# as canonical, and that is strictly better than refusing the upload — the
# offsets stay exact against what we stored either way.
DECODE_ORDER = ("utf-8-sig", "utf-8", "cp1252")


@dataclass(frozen=True, slots=True)
class Extracted:
    """The canonical text plus what we learned producing it."""

    raw_text: str
    title: str
    meta: dict[str, Any] = field(default_factory=dict)


# ── dispatch ─────────────────────────────────────────────────────────────────


def resolve_media_type(filename: str, declared: str | None) -> str:
    """Pick the media type to extract with.

    The extension wins over the browser's guess when the browser guessed
    something generic, and loses to it otherwise.
    """
    normalized = (declared or "").split(";")[0].strip().lower()
    if normalized in SUPPORTED_MEDIA_TYPES:
        return normalized

    lowered = filename.lower()
    for extension, media_type in EXTENSION_MEDIA_TYPES.items():
        if lowered.endswith(extension):
            return media_type

    raise UnsupportedMedia(
        f"{filename!r} is not a supported document type.",
        details={
            "filename": filename,
            "content_type": declared,
            "supported": sorted(SUPPORTED_MEDIA_TYPES),
        },
    )


def extract(
    filename: str,
    data: bytes,
    *,
    content_type: str | None,
    settings: Settings,
) -> Extracted:
    """Extract one uploaded file. Raises `AppError` subclasses, never bare."""
    media_type = resolve_media_type(filename, content_type)

    if media_type == APPLICATION_PDF:
        extracted = _extract_pdf(filename, data, settings=settings)
    elif media_type == TEXT_CSV:
        extracted = _extract_csv(filename, data)
    elif media_type == MESSAGE_RFC822:
        extracted = _extract_email(filename, data)
    else:
        extracted = _extract_text(filename, data)

    if not extracted.raw_text.strip():
        # `.strip()` on a *check*, never to compute an offset. An empty
        # document would produce zero mentions and a confusing "ingested, no
        # insights" card rather than an error the uploader can act on.
        raise ValidationFailed(
            f"{filename!r} contains no extractable text.",
            details={"fields": {"files": f"{filename}: no extractable text"}},
        )

    if len(extracted.raw_text) > settings.max_pdf_chars:
        raise ValidationFailed(
            f"{filename!r} extracts to more text than we accept.",
            details={
                "fields": {
                    "files": (
                        f"{filename}: {len(extracted.raw_text):,} characters, "
                        f"limit {settings.max_pdf_chars:,}"
                    )
                }
            },
        )

    return extracted


# ── per-format extraction ────────────────────────────────────────────────────


def decode(data: bytes) -> str:
    """Bytes to the string we are going to call canonical."""
    for encoding in DECODE_ORDER:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _title_from_filename(filename: str) -> str:
    # rsplit on the separator characters rather than Path(): a client-supplied
    # filename is untrusted and must never be interpreted as a path.
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return stem[:200] or "Untitled document"


def _extract_text(filename: str, data: bytes) -> Extracted:
    text = decode(data)
    return Extracted(
        raw_text=text,
        title=_title_from_filename(filename),
        meta={"extractor": "text", "filename": filename},
    )


def _extract_csv(filename: str, data: bytes) -> Extracted:
    """Row-joined, with the offset of every row recorded as it is written.

    A spreadsheet is a document to the tagger, so it has to become prose-ish
    text. Cells joined by `CELL_SEPARATOR` and rows by a newline gives spaCy
    something it can segment, and the `rows` offsets in `meta` let the frontend
    map a citation back to a source row without anyone searching the text for
    it later.
    """
    text = decode(data)
    cell_separator = " | "

    try:
        dialect: Any = csv.Sniffer().sniff(text[:4_096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel

    pieces: list[str] = []
    rows: list[dict[str, int]] = []
    cursor = 0

    for row in csv.reader(io.StringIO(text, newline=""), dialect):
        # Blank trailing cells carry no information and joining them produces
        # a line of separators, which spaCy then tries to parse.
        cells = [cell.replace("\n", " ") for cell in row if cell != ""]
        if not cells:
            continue
        line = cell_separator.join(cells)
        if pieces:
            pieces.append("\n")
            cursor += 1
        start = cursor
        pieces.append(line)
        cursor += len(line)
        rows.append({"char_start": start, "char_end": cursor})

    joined = "".join(pieces)
    if joined and not joined.endswith("\n"):
        joined += "\n"

    return Extracted(
        raw_text=joined,
        title=_title_from_filename(filename),
        meta={
            "extractor": "csv",
            "filename": filename,
            "row_count": len(rows),
            "rows": rows,
        },
    )


def _extract_email(filename: str, data: bytes) -> Extracted:
    """Headers first, then a blank line, then the body.

    Headers stay in `raw_text` so their offsets are real and the citation
    reader can highlight a sender. `email.policy.default` handles the MIME
    walk and the RFC 2047 header decoding, which is not worth hand-rolling.
    """
    # typeshed types `BytesParser` against `Message`, so `policy.default` —
    # which is what makes `get_body` and RFC 2047 header decoding available —
    # does not type-check. The runtime behaviour is the documented one.
    message = BytesParser(policy=policy.default).parsebytes(data)  # type: ignore[arg-type]

    header_lines: list[str] = []
    for name in EMAIL_HEADERS:
        value = message.get(name)
        if value:
            # Header values legitimately contain folded newlines; collapsing
            # them keeps one header per line, which is what the reader renders.
            flattened = " ".join(str(value).split())
            header_lines.append(f"{name}: {flattened}")

    body = ""
    try:
        part = message.get_body(preferencelist=("plain", "html"))  # type: ignore[attr-defined]
        if part is not None:
            payload = part.get_content()
            body = payload if isinstance(payload, str) else decode(bytes(payload))
            if part.get_content_type() == "text/html":
                body = _strip_html(body)
    except (KeyError, LookupError, ValueError) as exc:
        # A malformed MIME structure should degrade to "headers only", not 500.
        log.warning("email_body_unreadable", filename=filename, error=str(exc))

    raw_text = "\n".join(header_lines)
    if raw_text and body:
        raw_text += "\n\n"
    raw_text += body

    subject = message.get("Subject")
    title = " ".join(str(subject).split())[:200] if subject else _title_from_filename(filename)

    return Extracted(
        raw_text=raw_text,
        title=title,
        meta={
            "extractor": "email",
            "filename": filename,
            "from": str(message.get("From") or ""),
            "header_chars": len("\n".join(header_lines)),
        },
    )


def _strip_html(html: str) -> str:
    """Minimal tag stripper for HTML-only mail.

    Deliberately not a parser dependency. It runs on our own text before it
    becomes `raw_text`, and the result is what we store — there is no
    re-derivation later, so a crude-but-deterministic transform is safe here
    in a way that normalizing at read time would not be.
    """
    out: list[str] = []
    depth = 0
    for char in html:
        if char == "<":
            depth += 1
        elif char == ">":
            if depth:
                depth -= 1
                out.append(" ")
        elif depth == 0:
            out.append(char)
    return "".join(out)


def _extract_pdf(filename: str, data: bytes, *, settings: Settings) -> Extracted:
    """pypdf with three independent brakes.

    Page cap, character ceiling and a wall-clock deadline checked between
    pages. The deadline is cooperative: a single pathological page is not
    interruptible, which is a known and accepted limit — it bounds the damage
    to one page's worth of work rather than to a whole document's.
    """
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    started = time.monotonic()
    try:
        reader = PdfReader(io.BytesIO(data))
    except (PdfReadError, ValueError, OSError) as exc:
        raise ValidationFailed(
            f"{filename!r} is not a readable PDF.",
            details={"fields": {"files": f"{filename}: {exc}"}},
        ) from exc

    if reader.is_encrypted:
        # Trying the empty-password decrypt is worth one line; an actually
        # protected file is a user error with a clear message, not a 500.
        with contextlib.suppress(Exception):
            # pypdf raises several unrelated types here depending on the
            # encryption scheme and none of them mean anything useful — the
            # `is_encrypted` re-check below is the real answer.
            reader.decrypt("")
        if reader.is_encrypted:
            raise ValidationFailed(
                f"{filename!r} is password-protected.",
                details={"fields": {"files": f"{filename}: encrypted PDF"}},
            )

    total_pages = len(reader.pages)
    page_limit = min(total_pages, settings.max_pdf_pages)

    pieces: list[str] = []
    chars = 0
    pages_read = 0
    truncated = total_pages > page_limit

    for index in range(page_limit):
        if time.monotonic() - started > settings.pdf_timeout_seconds:
            truncated = True
            log.warning(
                "pdf_extract_deadline",
                filename=filename,
                pages_read=pages_read,
                pages_total=total_pages,
            )
            break
        try:
            page_text = reader.pages[index].extract_text() or ""
        except Exception as exc:
            log.warning("pdf_page_unreadable", filename=filename, page=index, error=str(exc))
            continue

        if pieces:
            pieces.append("\n\n")
            chars += 2
        pieces.append(page_text)
        chars += len(page_text)
        pages_read += 1

        if chars > settings.max_pdf_chars:
            truncated = True
            break

    raw_text = "".join(pieces)
    if len(raw_text) > settings.max_pdf_chars:
        # Truncate on a character boundary and record it, rather than raising:
        # a 200-page annual report is a plausible upload and half of it is more
        # useful than a rejection.
        raw_text = raw_text[: settings.max_pdf_chars]
        truncated = True

    return Extracted(
        raw_text=raw_text,
        title=_pdf_title(reader, filename),
        meta={
            "extractor": "pdf",
            "filename": filename,
            "pages_total": total_pages,
            "pages_read": pages_read,
            "truncated": truncated,
            "extract_ms": int((time.monotonic() - started) * 1000),
        },
    )


def _pdf_title(reader: Any, filename: str) -> str:
    try:
        title = (reader.metadata or {}).get("/Title")
    except Exception:
        title = None
    if isinstance(title, str) and title.strip():
        return " ".join(title.split())[:200]
    return _title_from_filename(filename)
