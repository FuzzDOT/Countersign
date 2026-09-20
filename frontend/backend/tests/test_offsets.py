"""The invariant everything else rests on. Plan §3, brief §15.

"If this breaks, nothing else in the project matters." So this file asserts,
across all three servable scenarios and the training corpus:

  raw_text[char_start:char_end] == surface

for every gold mention, every gold citation span, every token the tokenizer
emits, and every mention the tagger predicts. No tolerance, no `.strip()` in
the assertion — a citation that is off by one character is a citation that
points at the wrong evidence, and the demo's entire claim is that ours do not.

It also greps the source. The rule is that offsets are *constructed or looked
up*, never *searched for*, and the way that rule dies is someone reaching for
`.find()` at hour 19 because it is the obvious thing to do. A test that reads
the source is the only kind that catches it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from data.synth.generate import generate
from data.synth.scenarios import SCENARIOS
from ml.tagger.data import align_char_spans, iter_sentences
from ml.text.parse import parse
from ml.text.tokenize import OffsetIntegrityError, TokenizedDoc

BACKEND_ROOT = Path(__file__).resolve().parent.parent

ALL_SCENARIOS = sorted(SCENARIOS)
# The three servable scenarios plus the training split. `invoice_flood` is 120
# documents and parsing it is the slowest thing in the suite, so it is marked
# slow and the rest run everywhere.
FAST_SCENARIOS = ["meridian_shell_ring", "clean_baseline"]


@pytest.fixture(scope="module")
def parsed_demo() -> tuple[object, list[TokenizedDoc]]:
    manifest = generate("meridian_shell_ring")
    return manifest, [parse(d.raw_text) for d in manifest.documents]


# ── the corpus ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("scenario", ALL_SCENARIOS)
def test_gold_mentions_slice_back_to_their_surface(scenario: str) -> None:
    manifest = generate(scenario)
    for document in manifest.documents:
        for mention in document.mentions:
            actual = document.raw_text[mention.char_start : mention.char_end]
            assert actual == mention.surface, (
                f"{scenario}/{document.title}: mention at "
                f"[{mention.char_start}:{mention.char_end}] claims {mention.surface!r}, "
                f"text holds {actual!r}"
            )


@pytest.mark.parametrize("scenario", ALL_SCENARIOS)
def test_gold_citations_slice_back_to_their_sentence(scenario: str) -> None:
    manifest = generate(scenario)
    for document in manifest.documents:
        for relation in document.relations:
            actual = document.raw_text[relation.char_start : relation.char_end]
            assert actual == relation.sentence_text


@pytest.mark.parametrize("scenario", ALL_SCENARIOS)
def test_generation_is_byte_identical_across_runs(scenario: str) -> None:
    """Same seed, same bytes, same ids — on any machine.

    This is what lets the demo be rehearsed on one laptop and run on another,
    and what keeps the prerecorded briefing's `insight_id` values valid after
    `make nuke` (plan §1.11).
    """
    first, second = generate(scenario), generate(scenario)
    assert [d.raw_text for d in first.documents] == [d.raw_text for d in second.documents]
    assert [d.document_id for d in first.documents] == [d.document_id for d in second.documents]


# ── the tokenizer ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("scenario", FAST_SCENARIOS)
def test_every_token_slices_back_to_its_surface(scenario: str) -> None:
    manifest = generate(scenario)
    for document in manifest.documents:
        # `verify()` raises rather than returning False, so a failure names
        # the offending token instead of asserting `False is True`.
        parse(document.raw_text).verify()


@pytest.mark.parametrize("scenario", FAST_SCENARIOS)
def test_sentences_cover_every_gold_citation(scenario: str) -> None:
    """A citation span the tokenizer cannot locate is a citation we cannot
    highlight, which is the failure the reader would show as a blank box."""
    manifest = generate(scenario)
    missing: list[str] = []
    for document in manifest.documents:
        doc = parse(document.raw_text)
        for relation in document.relations:
            if doc.sentence_for(relation.char_start, relation.char_end) is None:
                missing.append(f"{document.title}: {relation.sentence_text[:60]!r}")
    # spaCy occasionally merges two of our sentences into one segment, which
    # means the gold span is *inside* a sentence rather than equal to it; the
    # citation still resolves. A span no sentence contains at all would not.
    assert not missing, "gold citations no sentence contains:\n  " + "\n  ".join(missing[:10])


def test_tokenized_doc_rejects_a_span_that_no_longer_matches() -> None:
    """The guard has to actually fire, or it is decoration."""
    doc = parse("Meridian Supply LLC wired $48,200 to Advent Holdings.")
    tampered = TokenizedDoc(
        text="a different string entirely",
        tokens=doc.tokens,
        sentences=doc.sentences,
    )
    with pytest.raises(OffsetIntegrityError):
        tampered.verify()


# ── the tagger ───────────────────────────────────────────────────────────────


@pytest.mark.ml
def test_predicted_mentions_slice_back_to_their_surface(parsed_demo) -> None:  # type: ignore[no-untyped-def]
    from ml.tagger.infer import get_tagger

    manifest, parsed = parsed_demo
    tagger = get_tagger()
    for document, doc in zip(manifest.documents, parsed, strict=True):
        tagging = tagger.tag(doc)
        tagging.verify()
        for mention in tagging.mentions:
            assert doc.text[mention.char_start : mention.char_end] == mention.surface
            assert document.raw_text is doc.text or document.raw_text == doc.text


@pytest.mark.ml
def test_predicted_mention_offsets_are_inside_their_sentence(parsed_demo) -> None:  # type: ignore[no-untyped-def]
    from ml.tagger.infer import get_tagger

    _, parsed = parsed_demo
    tagger = get_tagger()
    for doc in parsed[:8]:
        tagging = tagger.tag(doc)
        for mention in tagging.mentions:
            sentence = tagging.sentences[mention.sentence_index].unit.span
            assert sentence.char_start <= mention.char_start
            assert mention.char_end <= sentence.char_end


def test_gold_alignment_never_invents_a_span() -> None:
    """`align_char_spans` maps gold characters onto tokens.

    A gold span it cannot align is dropped, never clipped: a truncated name
    trained as ground truth is worse than one missing example.
    """
    manifest = generate("meridian_shell_ring")
    document = manifest.documents[0]
    doc = parse(document.raw_text)
    sentences = list(iter_sentences(doc))
    aligned = align_char_spans(
        doc,
        sentences,
        [(m.entity_type, m.char_start, m.char_end) for m in document.mentions],
    )
    for sentence, spans in zip(sentences, aligned, strict=True):
        for span in spans:
            assert 0 <= span.token_start < span.token_end <= len(sentence)


# ── the rule, enforced against the source ────────────────────────────────────

# Modules that handle document text and therefore must never locate an entity
# by searching. `data/synth` builds text with a running cursor; `ml/text`,
# `ml/tagger`, `ml/entities` and `workers` consume offsets the tokenizer
# recorded.
OFFSET_CRITICAL_PACKAGES = ("data/synth", "ml/text", "ml/tagger", "ml/entities", "workers")

# `str.find` and `str.rfind` exist for exactly one purpose — turning a surface
# back into an offset — and no other type has them, so any call is a
# violation. `.index` is shared with list and tuple, where it is ordinary, so
# it is a violation only when the receiver is named like document text.
BANNED_METHODS = frozenset({"find", "rfind"})
TEXT_RECEIVER = re.compile(r"\b(raw_text|text|surface|sentence_text|body|content)\b")

# The check runs over the AST rather than over lines, so a docstring
# explaining *why* `.find()` is forbidden does not trip the test that forbids
# it — which a line-based grep does, and which would push the explanation out
# of the code it explains.


def _python_sources() -> list[Path]:
    return [
        path
        for package in OFFSET_CRITICAL_PACKAGES
        for path in (BACKEND_ROOT / package).rglob("*.py")
    ]


def _label(path: Path) -> str:
    try:
        return str(path.relative_to(BACKEND_ROOT))
    except ValueError:
        return path.name


def _searching_calls(path: Path) -> list[str]:
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        method = node.func.attr
        receiver = ast.unparse(node.func.value)

        banned = method in BANNED_METHODS
        indexing_text = method == "index" and bool(TEXT_RECEIVER.search(receiver))
        if banned or indexing_text:
            offenders.append(f"{_label(path)}:{node.lineno}: {receiver}.{method}(...)")

    return offenders


def test_no_offset_critical_module_searches_text_for_a_span() -> None:
    offenders = [line for path in _python_sources() for line in _searching_calls(path)]
    assert not offenders, (
        "these calls locate something by searching text. Offsets must be "
        "constructed by the generator or read off a token the tokenizer "
        "positioned (docs/03-BACKEND-PLAN.md §3):\n  " + "\n  ".join(offenders)
    )


def test_offset_critical_packages_exist() -> None:
    """Guards the guard: a renamed package would make the check vacuous."""
    for package in OFFSET_CRITICAL_PACKAGES:
        assert (BACKEND_ROOT / package).is_dir(), f"{package} is gone — fix this test"
    assert len(_python_sources()) > 10


def test_the_source_check_would_catch_a_violation(tmp_path: Path) -> None:
    """A guard nobody has seen fail is a guard nobody knows works."""
    offending = tmp_path / "offender.py"
    offending.write_text("def locate(raw_text, name):\n    return raw_text.find(name)\n")
    assert _searching_calls(offending)

    innocent = tmp_path / "innocent.py"
    innocent.write_text("LABELS = ['a', 'b']\n\ndef rank(v):\n    return LABELS.index(v)\n")
    assert not _searching_calls(innocent)
