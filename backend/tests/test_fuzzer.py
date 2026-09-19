"""The adversarial fuzzer. Brief §15, plan §3 and §4 Stage 5.

Two things are load-bearing and get tested hardest:

**Perturbed text must never reach the database.** A variant in `documents`
would silently repoint every citation in the demo, and nothing downstream
would notice. `fragility_trials` stores no offsets for the same reason.

**The target span must survive the perturbation by arithmetic.** Every
family constructs its output and carries the citation through; if one
located the target by searching the new text, the first document containing
a repeated sentence would break it quietly.
"""

from __future__ import annotations

import random
import uuid

import pytest

from ml.fuzzer.base import Edit, apply_edits, overlaps, protected_spans
from ml.fuzzer.boilerplate import BoilerplateFamily
from ml.fuzzer.fragility import Trial, fragility_of, quartile_table
from ml.fuzzer.punctuation import PunctuationFamily
from ml.fuzzer.rename import RenameFamily
from ml.fuzzer.reorder import ReorderFamily
from ml.fuzzer.runner import all_families
from ml.fuzzer.synonym import SynonymFamily, candidates
from ml.tagger.infer import get_tagger
from ml.text.parse import parse

DOCUMENT = (
    "Invoice INV-4471\n"
    "Meridian Supply LLC\n"
    "Issued: 2026-09-14\n"
    "Bill to: Advent Holdings\n\n"
    "Meridian Supply LLC wired $48,200 to Advent Holdings on 2026-09-08.\n"
    "The settlement was recorded against the account in the usual way.\n"
    "Questions about this document should be directed to accounts payable.\n"
)
TARGET = "Meridian Supply LLC wired $48,200 to Advent Holdings on 2026-09-08."


@pytest.fixture(scope="module")
def parsed():  # type: ignore[no-untyped-def]
    doc = parse(DOCUMENT)
    return doc, get_tagger().tag(doc)


@pytest.fixture(scope="module")
def target(parsed):  # type: ignore[no-untyped-def]
    doc, _ = parsed
    span = next(s for s in doc.sentences if doc.text[s.char_start : s.char_end] == TARGET)
    return (span.char_start, span.char_end)


# ── the offset primitive ─────────────────────────────────────────────────────


def test_an_edit_before_the_target_moves_the_whole_span() -> None:
    variant = apply_edits("aaa TARGET zzz", [Edit(0, 3, "bbbbbb")], (4, 10), family="t")
    assert variant.target_text == "TARGET"


def test_an_edit_after_the_target_leaves_it_alone() -> None:
    variant = apply_edits("aaa TARGET zzz", [Edit(11, 14, "q")], (4, 10), family="t")
    assert variant.target_text == "TARGET"


def test_an_edit_inside_the_target_extends_it() -> None:
    variant = apply_edits("aaa TAR GET zzz", [Edit(7, 8, " -- ")], (4, 11), family="t")
    assert variant.target_text == "TAR -- GET"


def test_several_edits_compose() -> None:
    variant = apply_edits(
        "one two three four",
        [Edit(0, 3, "1"), Edit(4, 7, "22222"), Edit(14, 18, "4")],
        (8, 13),
        family="t",
    )
    assert variant.target_text == "three"


def test_overlapping_edits_are_a_hard_error() -> None:
    """A family that produces them has a bug, and the failure mode without
    this check is a citation two characters off that nobody notices."""
    with pytest.raises(AssertionError):
        apply_edits("abcdef", [Edit(0, 3, "x"), Edit(2, 5, "y")], (0, 3), family="t")


def test_overlap_helper_is_half_open() -> None:
    assert overlaps((5, 10), [(0, 6)])
    assert not overlaps((5, 10), [(0, 5)])
    assert not overlaps((5, 10), [(10, 20)])


# ── the families ─────────────────────────────────────────────────────────────


@pytest.mark.ml
@pytest.mark.parametrize("family_name", sorted(all_families()))
def test_every_family_keeps_the_target_span_slicing_correctly(  # type: ignore[no-untyped-def]
    parsed, target, family_name
) -> None:
    """The invariant plan §3 calls the fuzzer trap."""
    doc, tagging = parsed
    variant = all_families()[family_name].build(doc, tagging, target, random.Random(7))
    sliced = variant.target_text

    if family_name == "rename":
        # The parties are deliberately different; everything around them
        # must be the same sentence.
        assert "wired" in sliced and "$48,200" in sliced
    elif family_name in ("boilerplate", "reorder"):
        assert sliced == TARGET
    else:
        # synonym and punctuation rewrite inside the sentence, so the span
        # grows or shrinks but must still bracket the claim.
        assert "$48,200" in sliced


@pytest.mark.ml
def test_synonym_changes_words_but_not_entities(parsed, target) -> None:  # type: ignore[no-untyped-def]
    doc, tagging = parsed
    variant = SynonymFamily().build(doc, tagging, target, random.Random(3))
    assert variant.text != doc.text
    for mention in tagging.mentions:
        assert mention.surface in variant.text


def test_synonyms_come_from_wordnet_or_the_lexicon() -> None:
    assert candidates("payment", "NOUN")
    assert candidates("subsidiary", "NOUN")
    assert candidates("zzzznotaword", "NOUN") == ()


@pytest.mark.ml
def test_rename_replaces_parties_with_names_from_no_corpus(parsed, target) -> None:  # type: ignore[no-untyped-def]
    """Renaming into the held-out pool would sometimes pick a company already
    in the scenario and score a coreference merge as a relation loss."""
    from data.synth import names

    doc, tagging = parsed
    variant = RenameFamily().build(doc, tagging, target, random.Random(5))

    assert "Meridian Supply LLC" not in variant.text
    assert "Advent Holdings" not in variant.text
    assert variant.renames
    assert any(org in variant.text for org in names.FUZZ_ORGS)


@pytest.mark.ml
def test_rename_is_consistent_across_a_document(parsed, target) -> None:  # type: ignore[no-untyped-def]
    """One replacement per entity, not per mention: renaming the same company
    differently in two sentences fragments it under coreference."""
    doc, tagging = parsed
    variant = RenameFamily().build(doc, tagging, target, random.Random(5))
    from data.synth import names

    used = [org for org in names.FUZZ_ORGS if org in variant.text]
    # Two organizations in this document, so exactly two replacements.
    assert len(used) == 2


@pytest.mark.ml
def test_boilerplate_surrounds_the_claim_without_touching_it(parsed, target) -> None:  # type: ignore[no-untyped-def]
    doc, tagging = parsed
    variant = BoilerplateFamily().build(doc, tagging, target, random.Random(1))
    assert variant.target_text == TARGET
    assert len(variant.text) > len(doc.text)


@pytest.mark.ml
def test_reorder_moves_the_claim_intact(parsed, target) -> None:  # type: ignore[no-untyped-def]
    doc, tagging = parsed
    variant = ReorderFamily().build(doc, tagging, target, random.Random(2))
    assert variant.target_text == TARGET
    assert variant.text != doc.text


@pytest.mark.ml
def test_punctuation_leaves_entity_surfaces_intact(parsed, target) -> None:  # type: ignore[no-untyped-def]
    """Only the structure degrades, so the parties are still findable and the
    family measures segmentation rather than tagging."""
    doc, tagging = parsed
    variant = PunctuationFamily().build(doc, tagging, target, random.Random(4))
    assert variant.text != doc.text
    for mention in tagging.mentions:
        assert mention.surface in variant.text


@pytest.mark.ml
@pytest.mark.parametrize("family_name", sorted(all_families()))
def test_families_are_reproducible(parsed, target, family_name) -> None:  # type: ignore[no-untyped-def]
    """Judges re-run things; the number on the slide must be the number on
    stage (plan §1.11)."""
    doc, tagging = parsed
    family = all_families()[family_name]
    first = family.build(doc, tagging, target, random.Random("seed"))
    second = family.build(doc, tagging, target, random.Random("seed"))
    assert first.text == second.text
    assert (first.target_start, first.target_end) == (second.target_start, second.target_end)


def test_protected_spans_cover_every_mention(parsed) -> None:  # type: ignore[no-untyped-def]
    _, tagging = parsed
    spans = protected_spans(tagging)
    assert len(spans) == len(tagging.mentions)


# ── scoring ──────────────────────────────────────────────────────────────────


def _trial(**kw) -> Trial:  # type: ignore[no-untyped-def]
    defaults = {
        "insight_id": uuid.uuid4(),
        "perturbation": "synonym",
        "variant": 0,
        "label_flipped": False,
        "conf_delta": 0.0,
        "relation_lost": False,
    }
    return Trial(**{**defaults, **kw})


def test_a_stable_insight_scores_zero() -> None:
    assert fragility_of([_trial(), _trial()]) == 0.0


def test_a_flip_outweighs_a_confidence_wobble() -> None:
    """Label flip is the failure that changes what an analyst is told."""
    flipped = fragility_of([_trial(label_flipped=True)])
    wobbled = fragility_of([_trial(conf_delta=-0.9)])
    assert flipped > wobbled


def test_a_lost_relation_scores_highest() -> None:
    lost = fragility_of([_trial(label_flipped=True, relation_lost=True, conf_delta=-0.8)])
    flipped = fragility_of([_trial(label_flipped=True)])
    assert lost > flipped
    assert lost <= 1.0


def test_fragility_is_bounded() -> None:
    worst = _trial(label_flipped=True, relation_lost=True, conf_delta=-1.0)
    assert 0.0 <= fragility_of([worst]) <= 1.0


def test_a_vacuous_trial_is_not_evidence_of_robustness() -> None:
    """A family that could not perturb a document has demonstrated nothing,
    and counting it as a pass is a free win nobody earned."""
    assert fragility_of([_trial(vacuous=True)]) == 0.0
    mixed = fragility_of([_trial(vacuous=True), _trial(label_flipped=True)])
    assert mixed == pytest.approx(0.5)


def test_quartiles_partition_the_insights() -> None:
    from ml.fuzzer.fragility import ScatterPoint

    points = [
        ScatterPoint(uuid.uuid4(), vacuity=i / 20, fragility=i / 20, routing="auto_file")
        for i in range(20)
    ]
    trials = {point.insight_id: [_trial(insight_id=point.insight_id)] for point in points}
    table = quartile_table(points, trials)
    assert [row["vacuity_quartile"] for row in table] == [1, 2, 3, 4]


# ── the correlation report ───────────────────────────────────────────────────


def test_an_empty_report_says_so_rather_than_inventing_a_number() -> None:
    from ml.fuzzer.fragility import interpretation
    from ml.stats import Correlation

    text = interpretation(Correlation(0.0, 0.0, 1.0, 0), [], [])
    assert "Too few insights" in text


def test_a_weak_correlation_is_reported_as_weak() -> None:
    """There is no branch in the fuzzer that improves a number, and the
    wording has to be willing to say so."""
    from ml.fuzzer.fragility import interpretation
    from ml.stats import Correlation

    text = interpretation(Correlation(0.05, 0.04, 0.9, 60), [], [])
    assert "not statistically significant" in text


def test_a_middling_correlation_gets_its_diagnosis() -> None:
    from ml.fuzzer.fragility import interpretation
    from ml.stats import Correlation

    text = interpretation(Correlation(0.45, 0.4, 0.001, 60), [], [])
    assert "out-of-distribution tail" in text
