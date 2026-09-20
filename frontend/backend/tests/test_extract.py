"""Document extraction. Brief §5, plan §5 (PDF DoS).

Extraction runs once and its output *is* the coordinate system, so these tests
care about two things: that the text produced is the text we expect byte for
byte, and that the limits which stop a 2 MB upload from becoming 40 MB of
work actually fire.
"""

from __future__ import annotations

from email.message import EmailMessage

import pytest

from api.errors import UnsupportedMedia, ValidationFailed
from core.config import Settings, get_settings
from ml.text.extract import (
    APPLICATION_PDF,
    MESSAGE_RFC822,
    TEXT_CSV,
    TEXT_PLAIN,
    decode,
    extract,
    resolve_media_type,
)


@pytest.fixture
def settings() -> Settings:
    return get_settings()


# ── media type resolution ────────────────────────────────────────────────────


def test_declared_media_type_wins_when_it_is_supported() -> None:
    assert resolve_media_type("notes.bin", "text/plain") == TEXT_PLAIN


def test_charset_parameter_is_ignored() -> None:
    assert resolve_media_type("notes.txt", "text/plain; charset=utf-8") == TEXT_PLAIN


def test_extension_rescues_a_generic_content_type() -> None:
    """Browsers send application/octet-stream for .eml often enough that
    rejecting on the header alone would fail a real mailbox export."""
    assert resolve_media_type("thread.eml", "application/octet-stream") == MESSAGE_RFC822


def test_unknown_type_is_refused_with_the_allowlist() -> None:
    with pytest.raises(UnsupportedMedia) as caught:
        resolve_media_type("payload.exe", "application/x-msdownload")
    assert "supported" in caught.value.details


# ── plain text ───────────────────────────────────────────────────────────────


def test_plain_text_is_preserved_byte_for_byte(settings: Settings) -> None:
    body = "Meridian Supply LLC wired $48,200 to Advent Holdings.\n\nSecond paragraph.\n"
    result = extract("invoice.txt", body.encode(), content_type=TEXT_PLAIN, settings=settings)
    assert result.raw_text == body


def test_a_bom_is_stripped_rather_than_becoming_character_zero() -> None:
    """A leading U+FEFF would shift every offset in the document by one."""
    assert decode("﻿Meridian".encode()) == "Meridian"


def test_undecodable_bytes_degrade_rather_than_reject(settings: Settings) -> None:
    result = extract(
        "odd.txt", b"Meridian \xff\xfe Supply", content_type=TEXT_PLAIN, settings=settings
    )
    assert "Meridian" in result.raw_text


def test_empty_document_is_a_client_error(settings: Settings) -> None:
    with pytest.raises(ValidationFailed):
        extract("blank.txt", b"   \n  ", content_type=TEXT_PLAIN, settings=settings)


# ── csv ──────────────────────────────────────────────────────────────────────


def test_csv_rows_are_joined_and_their_offsets_recorded(settings: Settings) -> None:
    body = b"date,counterparty,amount\n2026-09-14,Advent Holdings,48200\n"
    result = extract("ledger.csv", body, content_type=TEXT_CSV, settings=settings)

    assert "Advent Holdings" in result.raw_text
    rows = result.meta["rows"]
    assert len(rows) == 2
    for row in rows:
        # The offsets are built from the running cursor, so they index the
        # text we are about to store — the same guarantee the generator gives.
        assert result.raw_text[row["char_start"] : row["char_end"]].count("\n") == 0


def test_csv_offsets_index_the_stored_text(settings: Settings) -> None:
    body = b"a,b\n1,2\n3,4\n"
    result = extract("t.csv", body, content_type=TEXT_CSV, settings=settings)
    sliced = [result.raw_text[r["char_start"] : r["char_end"]] for r in result.meta["rows"]]
    assert sliced == ["a | b", "1 | 2", "3 | 4"]


def test_csv_skips_rows_that_are_entirely_empty(settings: Settings) -> None:
    result = extract("t.csv", b"a,b\n\n,,\n1,2\n", content_type=TEXT_CSV, settings=settings)
    assert result.meta["row_count"] == 2


# ── email ────────────────────────────────────────────────────────────────────


def _message(body: str = "Payment of $48,200 was routed through Advent Holdings.") -> bytes:
    message = EmailMessage()
    message["From"] = "accounts@meridian.example"
    message["To"] = "ap@northgate.example"
    message["Subject"] = "Reference INV-4471"
    message["Date"] = "Mon, 14 Sep 2026 08:31:00 +0000"
    message.set_content(body)
    return message.as_bytes()


def test_email_headers_stay_in_raw_text(settings: Settings) -> None:
    """Brief §5 and plan §4: an email's routing metadata is evidence.

    "Who sent the invoice" is frequently the whole point, so the headers are
    part of the document the citation reader renders, not something stripped
    before tagging.
    """
    result = extract("mail.eml", _message(), content_type=MESSAGE_RFC822, settings=settings)
    assert result.raw_text.startswith("From: accounts@meridian.example")
    assert "Subject: Reference INV-4471" in result.raw_text
    assert "Advent Holdings" in result.raw_text


def test_email_subject_becomes_the_title(settings: Settings) -> None:
    result = extract("mail.eml", _message(), content_type=MESSAGE_RFC822, settings=settings)
    assert result.title == "Reference INV-4471"


def test_html_only_mail_is_stripped_to_text(settings: Settings) -> None:
    message = EmailMessage()
    message["From"] = "a@example.com"
    message["Subject"] = "HTML"
    message.set_content("<p>Advent <b>Holdings</b> paid</p>", subtype="html")
    result = extract("h.eml", message.as_bytes(), content_type=MESSAGE_RFC822, settings=settings)
    assert "<p>" not in result.raw_text
    assert "Advent" in result.raw_text


# ── pdf limits (plan §5) ─────────────────────────────────────────────────────


def _pdf(pages: int, text: str = "Meridian Supply LLC wired funds to Advent Holdings.") -> bytes:
    """A minimal multi-page PDF, written by hand.

    Hand-built rather than pulled from a fixture file so the page count is a
    parameter of the test rather than a property of a binary nobody can read
    in a diff.
    """
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    import io

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_pdf_page_cap_is_enforced(settings: Settings) -> None:
    """Stops at `max_pdf_pages` and says so.

    `_extract_pdf` is called directly rather than through `extract`, because
    blank pages yield no text and `extract` would reject the document as
    empty before the page accounting could be inspected. The cap is what is
    under test here, not the emptiness guard.
    """
    from ml.text.extract import _extract_pdf

    capped = settings.model_copy(update={"max_pdf_pages": 2})
    result = _extract_pdf("big.pdf", _pdf(6), settings=capped)

    assert result.meta["pages_total"] == 6
    assert result.meta["pages_read"] == 2
    assert result.meta["truncated"] is True


def test_pdf_under_the_cap_is_not_marked_truncated(settings: Settings) -> None:
    from ml.text.extract import _extract_pdf

    result = _extract_pdf("small.pdf", _pdf(3), settings=settings)
    assert result.meta["pages_read"] == 3
    assert result.meta["truncated"] is False


def test_unreadable_pdf_is_a_client_error_not_a_crash(settings: Settings) -> None:
    with pytest.raises(ValidationFailed):
        extract(
            "broken.pdf", b"%PDF-1.4 not really", content_type=APPLICATION_PDF, settings=settings
        )


def test_extracted_char_ceiling_rejects_an_oversized_document(settings: Settings) -> None:
    """A 2 MB PDF can extract to far more text than a 2 MB .txt file. The
    per-file byte cap does not catch that asymmetry; this does."""
    tiny = settings.model_copy(update={"max_pdf_chars": 1_000})
    body = ("Meridian Supply LLC wired funds. " * 200).encode()
    with pytest.raises(ValidationFailed) as caught:
        extract("long.txt", body, content_type=TEXT_PLAIN, settings=tiny)
    assert "limit" in str(caught.value.details)
