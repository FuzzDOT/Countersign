"""The BiLSTM-CRF tagger. Brief §15, plan §4 Stage 2.

Split three ways:

  * the CRF's maths, which is the part where a subtle bug produces plausible
    numbers rather than a crash
  * the BIO codec, which has to be an exact inverse or the F1 is wrong in a
    direction nobody notices
  * the weak labeling functions, whose output is the training signal

The trained checkpoint is exercised in `tests/test_offsets.py` and
`tests/test_pipeline.py`; here the model is built fresh and small so the
tests run in milliseconds and do not depend on weights being present.
"""

from __future__ import annotations

import pytest
import torch

from ml.tagger.labels import (
    ENTITY_TYPES,
    N_TAGS,
    OUTSIDE,
    TAGS,
    TokenSpan,
    spans_to_tags,
    tags_to_spans,
)
from ml.tagger.model import BiLSTMCRFTagger, TaggerConfig
from ml.tagger.vocab import build_vocab, encode_chars, encode_words, normalize_word

# ── tag alphabet ─────────────────────────────────────────────────────────────


def test_tag_alphabet_is_bio_over_the_brief_tag_set() -> None:
    assert N_TAGS == 1 + 2 * len(ENTITY_TYPES) == 13
    assert TAGS[0] == OUTSIDE
    assert len(set(TAGS)) == N_TAGS


def test_bio_encode_decode_round_trips() -> None:
    spans = [
        TokenSpan("ORG", 0, 3),
        TokenSpan("MONEY", 5, 7),
        TokenSpan("ORG", 9, 10),
    ]
    tags = spans_to_tags(spans, 12)
    decoded = [(s.entity_type, s.token_start, s.token_end) for s in tags_to_spans(tags)]
    assert decoded == [(s.entity_type, s.token_start, s.token_end) for s in spans]


def test_adjacent_spans_of_the_same_type_stay_separate() -> None:
    """`B-` has to open a new span even mid-run, or two companies listed back
    to back become one entity."""
    tags = spans_to_tags([TokenSpan("ORG", 0, 2), TokenSpan("ORG", 2, 4)], 4)
    assert tags == ["B-ORG", "I-ORG", "B-ORG", "I-ORG"]
    assert len(tags_to_spans(tags)) == 2


def test_orphan_inside_tag_opens_a_span_rather_than_being_dropped() -> None:
    """Viterbi under a learned transition matrix mostly avoids this. Mostly is
    not never, and dropping the span loses a real entity."""
    spans = tags_to_spans(["O", "I-ORG", "I-ORG", "O"])
    assert [(s.entity_type, s.token_start, s.token_end) for s in spans] == [("ORG", 1, 3)]


def test_overlapping_weak_spans_resolve_to_the_longer_one() -> None:
    tags = spans_to_tags([TokenSpan("ORG", 0, 3), TokenSpan("PERSON", 1, 2)], 4)
    assert tags == ["B-ORG", "I-ORG", "I-ORG", "O"]


def test_span_confidence_is_the_geometric_mean() -> None:
    """One uncertain token should drag a multi-token entity down: a name whose
    surname is a coin flip is not a name we are confident in."""
    spans = tags_to_spans(["B-ORG", "I-ORG"], [1.0, 0.25])
    assert spans[0].confidence == pytest.approx(0.5)


# ── vocabulary ───────────────────────────────────────────────────────────────


def test_digits_collapse_so_amounts_do_not_fill_the_vocabulary() -> None:
    """The *identity* of an amount says nothing about whether the token is
    MONEY, so the digits are erased. Their count is kept — a five-figure sum
    and a three-figure one are different shapes and that is real signal — so
    two amounts collapse onto one word type only when they are the same
    shape. The character CNN still sees the real digits either way.
    """
    assert normalize_word("$48,200.00") == normalize_word("$71,015.33")
    assert normalize_word("$48,200.00") != normalize_word("$715.33")
    assert "4" not in normalize_word("$48,200.00")
    assert normalize_word("Meridian") == "meridian"


def test_vocab_is_deterministic_and_reserves_pad_and_unk() -> None:
    sentences = [["Meridian", "wired", "funds"], ["Advent", "wired", "funds"]]
    first = build_vocab(sentences, min_word_count=1)
    second = build_vocab(sentences, min_word_count=1)
    assert first.words == second.words
    assert first.words[:2] == ("<pad>", "<unk>")
    assert first.chars[:2] == ("<pad>", "<unk>")


def test_rare_words_fall_back_to_unk() -> None:
    vocab = build_vocab([["common", "common"], ["rare"]], min_word_count=2)
    ids = encode_words(["common", "rare"], vocab.word_index)
    assert ids[0] != 1
    assert ids[1] == 1


def test_char_truncation_keeps_both_ends() -> None:
    """Prefixes and suffixes carry the morphology — `$`, `Ltd`, `-X`. A window
    that keeps only the start throws away the legal suffix."""
    vocab = build_vocab([["abcdefghij"]], min_word_count=1)
    encoded = encode_chars(["abcdefghij"], vocab.char_index, max_len=4)[0]
    index = vocab.char_index
    assert encoded[:2] == [index["a"], index["b"]]
    assert encoded[2:4] == [index["i"], index["j"]]


# ── the CRF ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tiny_model() -> BiLSTMCRFTagger:
    torch.manual_seed(0)
    return BiLSTMCRFTagger(TaggerConfig(n_words=24, n_chars=16, lstm_hidden=8, dropout=0.0))


def _inputs(lengths: list[int]) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    width = max(lengths)
    mask = torch.tensor(
        [[i < length for i in range(width)] for length in lengths], dtype=torch.bool
    )
    words = torch.randint(1, 24, (len(lengths), width)) * mask
    chars = torch.randint(1, 16, (len(lengths), width, 20)) * mask.unsqueeze(2)
    return words, chars, mask


def test_forward_and_backward_scores_agree_at_every_position(tiny_model) -> None:  # type: ignore[no-untyped-def]
    """alpha_t + beta_t must sum to log Z at *every* t, for every sequence.

    This is the single strongest check on a CRF implementation: an off-by-one
    in the masking of either recursion breaks it at exactly one position,
    which no end-to-end accuracy number would ever reveal.
    """
    words, chars, mask = _inputs([7, 5, 3])
    with torch.no_grad():
        emissions, _ = tiny_model(words, chars, mask)
        alphas = tiny_model.crf._forward_alphas(emissions, mask)
        betas = tiny_model.crf._backward_betas(emissions, mask)
        log_z = tiny_model.crf.log_partition(emissions, mask)

    lengths = mask.sum(dim=1)
    for row in range(words.size(0)):
        for step in range(int(lengths[row])):
            total = float(torch.logsumexp(alphas[row, step] + betas[row, step], dim=0))
            assert total == pytest.approx(float(log_z[row]), abs=1e-3)


def test_marginals_are_probabilities(tiny_model) -> None:  # type: ignore[no-untyped-def]
    words, chars, mask = _inputs([6, 4])
    with torch.no_grad():
        emissions, _ = tiny_model(words, chars, mask)
        marginals = tiny_model.crf.marginals(emissions, mask)
    lengths = mask.sum(dim=1)
    for row in range(words.size(0)):
        for step in range(int(lengths[row])):
            assert float(marginals[row, step].sum()) == pytest.approx(1.0, abs=1e-4)
            assert float(marginals[row, step].min()) >= 0.0


def test_partition_dominates_any_single_path(tiny_model) -> None:
    """log Z is a sum over all paths, so no individual path can score above
    it. A sign error in the score function shows up here immediately."""
    words, chars, mask = _inputs([6, 4])
    with torch.no_grad():
        emissions, _ = tiny_model(words, chars, mask)
        paths = tiny_model.crf.viterbi(emissions, mask)
        width = words.size(1)
        padded = torch.tensor([path + [0] * (width - len(path)) for path in paths])
        score = tiny_model.crf.score(emissions, padded, mask)
        assert torch.all(score <= tiny_model.crf.log_partition(emissions, mask) + 1e-4)


def test_viterbi_returns_one_tag_per_real_token(tiny_model) -> None:  # type: ignore[no-untyped-def]
    words, chars, mask = _inputs([7, 5, 1])
    paths, marginals, hidden = tiny_model.decode(words, chars, mask)
    assert [len(p) for p in paths] == [7, 5, 1]
    assert marginals.shape == (3, 7, N_TAGS)
    assert hidden.shape == (3, 7, tiny_model.config.encoder_dim)


def test_loss_is_finite_and_decreases_when_fitted(tiny_model) -> None:  # type: ignore[no-untyped-def]
    """A CRF that cannot overfit eight sentences has a bug in its gradient."""
    words, chars, mask = _inputs([6, 6, 6, 6])
    tags = torch.randint(0, N_TAGS, (4, 6)) * mask

    optimizer = torch.optim.Adam(tiny_model.parameters(), lr=0.05)
    first = float(tiny_model.loss(words, chars, mask, tags))
    for _ in range(40):
        optimizer.zero_grad()
        loss = tiny_model.loss(words, chars, mask, tags)
        loss.backward()
        optimizer.step()
    last = float(tiny_model.loss(words, chars, mask, tags))

    assert first == pytest.approx(first)  # finite
    assert last < first * 0.5


def test_padding_cannot_be_decoded_as_a_tag(tiny_model) -> None:  # type: ignore[no-untyped-def]
    """Emissions at padded positions are masked to a large negative, so a
    short sequence in a wide batch cannot pick up phantom entities."""
    words, chars, mask = _inputs([6, 2])
    with torch.no_grad():
        emissions, _ = tiny_model(words, chars, mask)
    assert torch.all(emissions[1, 2:] < -1_000)


# ── weak supervision ─────────────────────────────────────────────────────────


def _labels(text: str) -> list[tuple[str, str]]:
    from ml.tagger.data import iter_sentences
    from ml.tagger.weak_supervision import label_document, training_gazetteer
    from ml.text.parse import parse

    doc = parse(text)
    sentences = list(iter_sentences(doc))
    labeled = label_document(doc, sentences, training_gazetteer())

    out: list[tuple[str, str]] = []
    for sentence, spans in zip(sentences, labeled, strict=True):
        offset = sentence.span.token_start
        for span in spans:
            first = doc.tokens[offset + span.token_start]
            last = doc.tokens[offset + span.token_end - 1]
            out.append((span.entity_type, doc.text[first.char_start : last.char_end]))
    return out


def test_regex_families_are_labeled() -> None:
    found = _labels(
        "Brightwater Industrial LLC wired $48,200.00 to Calderon Freight Co "
        "on 14 September 2026 under INV-4471 by ACH debit."
    )
    types = {entity_type for entity_type, _ in found}
    assert {"ORG", "MONEY", "DATE", "ACCOUNT_REF", "TRANSACTION_TYPE"} <= types


def test_held_out_names_are_not_labeled_by_the_training_gazetteer() -> None:
    """The whole point of the split (plan §0).

    If the gazetteer knew `Meridian Supply LLC`, band D would be testing
    nothing and the fragility correlation would be measuring noise.
    """
    found = _labels("Meridian Supply LLC wired funds to Advent Holdings.")
    assert not [surface for kind, surface in found if kind == "ORG"]


def test_a_span_never_opens_or_closes_on_whitespace() -> None:
    """spaCy emits `\\n\\n` as a token between an invoice header and its body.
    Letting a candidate start there produced spans two characters early."""
    found = _labels("Invoice INV-0001\n\nBrightwater Industrial LLC paid.")
    for _, surface in found:
        assert surface == surface.strip()


def test_trailing_punctuation_does_not_lose_an_abbreviated_suffix() -> None:
    """spaCy keeps the period on `Co.`, and the gold span covers that token.
    Refusing to match would drop the entity over a tokenizer detail."""
    found = _labels("The payment went to Calderon Freight Co.")
    assert any(kind == "ORG" and "Calderon Freight Co" in surface for kind, surface in found)


def test_a_comma_token_does_not_extend_a_span() -> None:
    found = _labels("Settlement method for this period is book transfer, effective 2026-09-14.")
    surfaces = [surface for kind, surface in found if kind == "TRANSACTION_TYPE"]
    assert surfaces == ["book transfer"]
