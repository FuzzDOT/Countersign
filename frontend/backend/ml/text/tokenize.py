"""The only place document text is ever split.

docs/03-BACKEND-PLAN.md §3: offsets are the invariant everything else rests on.
`documents.raw_text` is the sole coordinate system in this database, and every
`char_start`/`char_end` anywhere — mentions, insight citations, attention
tokens — indexes into that exact string.

The rules, which `tests/test_offsets.py` enforces:

- Nothing calls `.split()`, `.strip()`, `.lower()` or any normalizer on
  document text to *locate* something. Normalization for a feature vector is
  fine; normalization that then gets used to compute an offset is not.
- Spans come from spaCy's `token.idx` and `doc.char_span`, never from
  `str.find()`. A generator or extractor that searched for a substring would be
  wrong the second time a company name appeared in one document, and wrong
  quietly.
- PDF and .eml extraction produce `raw_text` once, at upload. Nothing
  downstream re-extracts.

`TokenizedDoc.verify()` is cheap (one slice comparison per token) and runs on
every document the pipeline touches. Loud failure, no tolerance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Sentinel for a token with no syntactic head other than itself (the ROOT).
ROOT_HEAD = -1


@dataclass(frozen=True, slots=True)
class Token:
    """One token, positioned in the document's coordinate system."""

    surface: str
    char_start: int
    char_end: int
    pos: str
    dep: str
    # Index into `TokenizedDoc.tokens`, or ROOT_HEAD for the sentence root.
    head_idx: int
    # Index of the sentence in `TokenizedDoc.sentences` this token belongs to.
    sent_idx: int
    # Lowercased lemma. Stage 3's rule-based relation extractor keys its
    # verb patterns on this, and computing it here rather than there keeps
    # spaCy's output in one place — a lemmatizer run downstream would be a
    # second tokenization of text that already has authoritative offsets.
    lemma: str = ""

    @property
    def is_alpha(self) -> bool:
        return self.surface.isalpha()

    def as_dict(self) -> dict[str, Any]:
        return {
            "s": self.surface,
            "a": self.char_start,
            "b": self.char_end,
            "p": self.pos,
            "d": self.dep,
            "h": self.head_idx,
            "n": self.sent_idx,
            "l": self.lemma,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Token:
        return cls(
            surface=payload["s"],
            char_start=payload["a"],
            char_end=payload["b"],
            pos=payload["p"],
            dep=payload["d"],
            head_idx=payload["h"],
            sent_idx=payload["n"],
            lemma=payload.get("l", ""),
        )


@dataclass(frozen=True, slots=True)
class Span:
    """A half-open character range into the parent document's text."""

    char_start: int
    char_end: int
    # Inclusive range of token indices covered, for slicing without a search.
    token_start: int
    token_end: int

    def text_of(self, document_text: str) -> str:
        return document_text[self.char_start : self.char_end]

    def contains(self, char_start: int, char_end: int) -> bool:
        return self.char_start <= char_start and char_end <= self.char_end

    def as_dict(self) -> dict[str, int]:
        return {
            "a": self.char_start,
            "b": self.char_end,
            "ts": self.token_start,
            "te": self.token_end,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, int]) -> Span:
        return cls(
            char_start=payload["a"],
            char_end=payload["b"],
            token_start=payload["ts"],
            token_end=payload["te"],
        )


class OffsetIntegrityError(AssertionError):
    """Raised when a span no longer indexes the text it claims to.

    An AssertionError subclass on purpose: this is a broken invariant, not a
    recoverable condition, and no caller should be catching it.
    """


@dataclass(frozen=True, slots=True)
class TokenizedDoc:
    """Tokens and sentences over one immutable string.

    `text` is byte-for-byte `documents.raw_text`. It is never a normalized,
    trimmed, or re-extracted variant of it — if it were, every offset computed
    here would be silently wrong against the stored document.
    """

    text: str
    tokens: tuple[Token, ...]
    sentences: tuple[Span, ...]

    def __len__(self) -> int:
        return len(self.tokens)

    def verify(self) -> None:
        """Assert every span still slices back to the surface it recorded."""
        for index, token in enumerate(self.tokens):
            actual = self.text[token.char_start : token.char_end]
            if actual != token.surface:
                raise OffsetIntegrityError(
                    f"token {index} claims [{token.char_start}:{token.char_end}] == "
                    f"{token.surface!r}, but the text holds {actual!r}"
                )
            if not 0 <= token.sent_idx < len(self.sentences):
                raise OffsetIntegrityError(
                    f"token {index} points at sentence {token.sent_idx}, "
                    f"but there are {len(self.sentences)}"
                )
        for index, sentence in enumerate(self.sentences):
            if sentence.char_end <= sentence.char_start:
                raise OffsetIntegrityError(f"sentence {index} has a non-positive span")
            if sentence.char_end > len(self.text):
                raise OffsetIntegrityError(
                    f"sentence {index} ends at {sentence.char_end}, past the {len(self.text)}-char text"
                )

    # ── lookups that do not search text ──────────────────────────────────────

    def sentence_for(self, char_start: int, char_end: int) -> Span | None:
        """The sentence wholly containing a span, or None.

        Linear scan. At a few hundred sentences per document that is cheaper
        than maintaining an interval tree, and the alternative — locating the
        sentence by searching the text — is exactly what §3 forbids.
        """
        for sentence in self.sentences:
            if sentence.contains(char_start, char_end):
                return sentence
        return None

    def tokens_in(self, span: Span) -> tuple[Token, ...]:
        return self.tokens[span.token_start : span.token_end + 1]

    def token_indices_in(self, char_start: int, char_end: int) -> tuple[int, ...]:
        """Indices of tokens overlapping a character range.

        Overlap rather than containment: a tagger span may cut a token in half
        at a hyphen, and dropping it would lose the entity.
        """
        return tuple(
            index
            for index, token in enumerate(self.tokens)
            if token.char_start < char_end and token.char_end > char_start
        )

    def slice(self, char_start: int, char_end: int) -> str:
        return self.text[char_start:char_end]

    # ── serialization (the parse cache stores this, not a spaCy Doc) ─────────

    def as_dict(self) -> dict[str, Any]:
        return {
            "tokens": [t.as_dict() for t in self.tokens],
            "sentences": [s.as_dict() for s in self.sentences],
        }

    @classmethod
    def from_dict(cls, text: str, payload: dict[str, Any]) -> TokenizedDoc:
        """Rehydrate against the *caller's* text.

        The text is deliberately not stored in the cache entry: the cache is
        keyed by sha256 of the text, so storing it again would double the cache
        size to hold a value we already have — and would create a second copy
        that could disagree with `documents.raw_text`.
        """
        return cls(
            text=text,
            tokens=tuple(Token.from_dict(t) for t in payload["tokens"]),
            sentences=tuple(Span.from_dict(s) for s in payload["sentences"]),
        )


# ── line-aware segmentation ──────────────────────────────────────────────────
#
# Invoice and email headers have no terminal punctuation, so spaCy glues the
# whole header onto the first body sentence. That is not a cosmetic problem:
# `insights.sentence_text` is the citation a judge reads, and a 200-character
# "sentence" that is four header lines plus one claim is a bad citation. It
# also dilutes the sentence graph — measured on `meridian_shell_ring`, the
# band-A ownership edge that closes the demo's three-hop cycle came out
# NO_RELATION, and the header's address produced a spurious
# SHARES_ADDRESS_WITH between two companies that merely appeared on the same
# invoice.
#
# The split happens here, after the parse, rather than by constraining the
# parser with `is_sent_start`. Constraining it was tried: a header line with
# no verb gives every token its own tree root, and `Meridian Supply LLC` came
# back as three sentences.

# Words that end a line mid-sentence often enough that a break after them is
# more likely to be a wrapped line than a new segment.
CONTINUATION_WORDS = frozenset(
    {
        "and",
        "or",
        "but",
        "of",
        "to",
        "for",
        "with",
        "by",
        "from",
        "in",
        "on",
        "at",
        "the",
        "a",
        "an",
        "as",
        "that",
        "which",
        "than",
        "per",
    }
)

# Characters a genuinely new line tends to open with.
SEGMENT_OPENERS = "([{$\u201c\"'"


def _crosses_line(previous: Any) -> bool:
    return "\n" in previous.whitespace_ or "\n" in previous.text


def _opens_segment(previous: Any, token: Any) -> bool:
    """Whether a line break between two tokens starts a new segment.

    Conservative on both sides. A wrapped line almost always breaks after a
    function word or a comma; a header line almost always opens with a
    capital, a digit or a currency symbol. A PDF that wraps mid-sentence
    before a proper noun will still be split — accepted, and the citation is
    still byte-exact either way.
    """
    text = token.text
    if not text or text.isspace():
        return False
    if previous.text.casefold() in CONTINUATION_WORDS:
        return False
    if previous.text.endswith((",", "-", "/")):
        return False
    return text[0].isupper() or text[0].isdigit() or text[0] in SEGMENT_OPENERS


def _token_groups(spacy_doc: Any) -> list[list[int]]:
    """spaCy's sentences, subdivided at line boundaries."""
    groups: list[list[int]] = []
    current: list[int] = []

    for sent in spacy_doc.sents:
        for index in range(sent.start, sent.end):
            if (
                current
                and index > sent.start
                and _crosses_line(spacy_doc[index - 1])
                and _opens_segment(spacy_doc[index - 1], spacy_doc[index])
            ):
                groups.append(current)
                current = []
            current.append(index)
        if current:
            groups.append(current)
            current = []

    return groups


def build_tokenized_doc(text: str, spacy_doc: Any) -> TokenizedDoc:
    """Convert a spaCy Doc into our frozen representation.

    Kept here rather than in parse.py so that the spaCy-to-offset translation —
    the step where an offset bug would be introduced — lives beside the
    invariant it has to satisfy.
    """
    sentence_bounds: list[tuple[int, int, int, int]] = []
    sent_of_token: list[int] = [0] * len(spacy_doc)

    for group in _token_groups(spacy_doc):
        content = [index for index in group if not spacy_doc[index].text.isspace()]
        if not content:
            # A group of nothing but newlines is not a sentence. Its tokens
            # join the previous one so that every token still points at a
            # sentence that exists.
            if sentence_bounds:
                for index in group:
                    sent_of_token[index] = len(sentence_bounds) - 1
            continue

        first, last = content[0], content[-1]
        sentence_bounds.append(
            (
                spacy_doc[first].idx,
                spacy_doc[last].idx + len(spacy_doc[last].text),
                first,
                last,
            )
        )
        for index in group:
            sent_of_token[index] = len(sentence_bounds) - 1

    if not sentence_bounds and len(spacy_doc):
        # A document spaCy declines to segment still needs one sentence, or
        # every token would point at a sentence that does not exist.
        sentence_bounds.append((0, len(text), 0, len(spacy_doc) - 1))

    tokens = tuple(
        Token(
            surface=token.text,
            char_start=token.idx,
            char_end=token.idx + len(token.text),
            pos=token.pos_,
            dep=token.dep_,
            head_idx=ROOT_HEAD if token.head.i == token.i else token.head.i,
            sent_idx=sent_of_token[token.i],
            lemma=token.lemma_.casefold(),
        )
        for token in spacy_doc
    )

    sentences = tuple(
        Span(char_start=a, char_end=b, token_start=ts, token_end=te)
        for a, b, ts, te in sentence_bounds
    )

    doc = TokenizedDoc(text=text, tokens=tokens, sentences=sentences)
    doc.verify()
    return doc
