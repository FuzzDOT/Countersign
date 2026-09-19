"""Corpus invariants.

Every number the project reports traces back to this corpus, so these are not
cosmetic checks. The train/held-out split, the band spread and the offset
exactness are each load-bearing for a different claim, and each fails silently
if it breaks.
"""

from __future__ import annotations

import pytest

from data.synth import names
from data.synth.generate import generate
from data.synth.scenarios import SCENARIOS, SERVABLE_SCENARIOS
from data.synth.templates import Band, assert_template_coverage, templates_for

ALL_SCENARIOS = sorted(SCENARIOS)


def test_name_pools_are_disjoint() -> None:
    """The single most important invariant in the data layer.

    A name that appears in both pools means the model saw the eval set's
    entities during training, which contaminates the tagger F1, the relation
    metrics, the rename perturbation and the fragility correlation all at once.
    """
    names.assert_pools_disjoint()


def test_every_relation_has_a_template_in_every_band() -> None:
    assert_template_coverage()


def test_band_d_is_refused_for_the_training_split() -> None:
    """Not a style rule — held-out syntax in the training corpus would make
    vacuity flat and the fragility eval meaningless."""
    with pytest.raises(ValueError, match="held out of training"):
        templates_for("WIRED_FUNDS_TO", Band.D, split="train")


@pytest.mark.parametrize("scenario", ALL_SCENARIOS)
def test_scenario_generates_and_self_verifies(scenario: str) -> None:
    manifest = generate(scenario)
    manifest.verify()
    assert manifest.documents
    assert manifest.relations


@pytest.mark.parametrize("scenario", ALL_SCENARIOS)
def test_every_offset_indexes_into_raw_text(scenario: str) -> None:
    """The guarantee the whole citation feature rests on.

    `verify()` already checks this, but asserting it here means the failure
    message names the offending document rather than surfacing as a generator
    exception.
    """
    manifest = generate(scenario)
    for document in manifest.documents:
        for mention in document.mentions:
            assert (
                document.raw_text[mention.char_start : mention.char_end] == mention.surface
            ), f"{document.title}: mention {mention.surface!r} offset is wrong"
        for relation in document.relations:
            assert (
                document.raw_text[relation.char_start : relation.char_end]
                == relation.sentence_text
            ), f"{document.title}: citation span for {relation.relation} is wrong"


def test_training_corpus_has_no_held_out_syntax() -> None:
    manifest = generate("train_corpus")
    bands = {str(r.band) for r in manifest.relations}
    assert "D" not in bands, "training corpus contains band-D syntax"
    assert bands == {"A", "B", "C"}


def test_training_corpus_uses_no_held_out_names() -> None:
    manifest = generate("train_corpus")
    held_out = {n.casefold() for n in (*names.HELDOUT_ORGS, *names.HELDOUT_PERSONS)}
    for canonical in manifest.named_entities():
        assert canonical.casefold() not in held_out, (
            f"{canonical!r} is a held-out entity but appears in the training corpus"
        )


@pytest.mark.parametrize("scenario", SERVABLE_SCENARIOS)
def test_servable_scenarios_have_an_out_of_distribution_tail(scenario: str) -> None:
    """No band D means no vacuity variance, which means no fragility signal."""
    manifest = generate(scenario)
    band_d = [r for r in manifest.relations if str(r.band) == "D"]
    assert band_d, f"{scenario} has no band-D relations"
    share = len(band_d) / len(manifest.relations)
    assert 0.03 <= share <= 0.25, f"{scenario} band-D share is {share:.0%}, outside 3-25%"


def test_generation_is_deterministic() -> None:
    """Same seed, same bytes — on this machine and on the demo machine."""
    first, second = generate("meridian_shell_ring"), generate("meridian_shell_ring")
    assert [d.raw_text for d in first.documents] == [d.raw_text for d in second.documents]
    assert [d.document_id for d in first.documents] == [d.document_id for d in second.documents]
    assert first.routing_counts() == second.routing_counts()


def test_a_different_seed_changes_the_corpus() -> None:
    """Guards against the seed being ignored, which would make the
    determinism test above vacuous."""
    baseline = generate("meridian_shell_ring")
    varied = generate("meridian_shell_ring", seed=999)
    assert [d.raw_text for d in baseline.documents] != [d.raw_text for d in varied.documents]


def test_demo_scenario_contains_the_pinned_sentence() -> None:
    """The sentence the rehearsed script and the mission brief both quote."""
    manifest = generate("meridian_shell_ring")
    expected = (
        "Payment of $48,200 was routed through Advent Holdings "
        "on behalf of Meridian Supply LLC."
    )
    matches = [r for r in manifest.relations if r.sentence_text == expected]
    assert len(matches) == 1, "the pinned demo sentence is missing or duplicated"
    assert matches[0].routing == "escalate_now"
    assert matches[0].template_id == "wired.c1"


def test_demo_scenario_has_the_three_hop_ownership_cycle() -> None:
    manifest = generate("meridian_shell_ring")
    owned = {
        (r.subject_canonical, r.object_canonical)
        for r in manifest.relations
        if r.relation == "OWNED_BY"
    }
    assert ("Meridian Supply LLC", "Advent Holdings") in owned
    assert ("Advent Holdings", "Kestrel Registry Ltd") in owned
    assert ("Kestrel Registry Ltd", "Meridian Supply LLC") in owned


def test_planted_failure_has_exculpatory_context_outside_its_citation() -> None:
    """The mechanism of the documented failure, asserted rather than assumed.

    The benign explanation must sit immediately *after* the citation span. If
    it ever ended up inside the span, the cascade would see it, the failure
    would stop reproducing, and `documented_failures` would ship empty.
    """
    from data.synth.templates import EXCULPATORY_SENTENCE

    manifest = generate("meridian_shell_ring")
    failures = manifest.planted_failures()
    assert len(failures) == 1, "expected exactly one planted failure case"

    failure = failures[0]
    document = next(d for d in manifest.documents if failure in d.relations)

    citation = document.raw_text[failure.char_start : failure.char_end]
    assert EXCULPATORY_SENTENCE not in citation, (
        "the exculpatory sentence leaked into the citation span — the documented "
        "failure will no longer reproduce"
    )
    following = document.raw_text[failure.char_end : failure.char_end + len(EXCULPATORY_SENTENCE) + 2]
    assert EXCULPATORY_SENTENCE in following, (
        "the exculpatory sentence is not adjacent to the citation span"
    )
    assert failure.routing == "flag_for_review"
    assert failure.failure_note


def test_demo_scenario_escalation_rate_is_in_the_target_band() -> None:
    """Brief §14's definition of done: 8-20% escalation.

    Checked against ground truth here, not against the model — this asserts the
    *scenario* is calibrated to make the cascade argument, which is a property
    of the corpus and therefore testable at Stage 1.
    """
    manifest = generate("meridian_shell_ring")
    counts = manifest.routing_counts()
    escalated = counts.get("escalate_now", 0)
    share = escalated / len(manifest.relations)
    assert 0.08 <= share <= 0.20, f"ground-truth escalation rate is {share:.0%}"


def test_clean_baseline_escalates_nothing() -> None:
    """The control. If the gate flags anything here, it is crying wolf."""
    manifest = generate("clean_baseline")
    assert manifest.routing_counts().get("escalate_now", 0) == 0


def test_invoice_flood_is_large_enough_to_be_a_latency_test() -> None:
    manifest = generate("invoice_flood")
    assert len(manifest.documents) >= 100
    assert len(manifest.relations) >= 200
