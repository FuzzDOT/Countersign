"""Entity embeddings and coreference. Plan §1.5.

The merges here decide whether the demo's ownership cycle is one three-node
loop or six disconnected nodes, so the tests are as much about what must
*not* merge as about what must.
"""

from __future__ import annotations

import uuid

import numpy as np
import pytest

from ml.entities.coref import (
    NAMED_NAMESPACE,
    EntityResolver,
    entity_key,
    entity_uuid,
    id_namespace,
)
from ml.entities.embed import EMBEDDING_DIM, cosine, embed, normalize_name, trigrams

ORG_ID = uuid.UUID("00000000-0000-4000-8000-0000000000aa")


@pytest.fixture
def resolver() -> EntityResolver:
    return EntityResolver(ORG_ID, threshold=0.86)


# ── embeddings ───────────────────────────────────────────────────────────────


def test_embedding_is_a_unit_vector_of_the_declared_width() -> None:
    vector = embed("Meridian Supply LLC")
    assert vector.shape == (EMBEDDING_DIM,)
    assert float(np.linalg.norm(vector)) == pytest.approx(1.0, abs=1e-5)


def test_embedding_is_deterministic_across_calls() -> None:
    """Hashed, not fitted: a name first seen at hour 22 vectorizes exactly as
    one first seen at hour 3."""
    assert np.array_equal(embed("Advent Holdings"), embed("Advent Holdings"))


def test_normalization_drops_legal_suffixes_and_punctuation() -> None:
    assert normalize_name("Meridian Supply, LLC") == "meridian supply"
    assert normalize_name("Kestrel Registry Limited") == "kestrel registry"
    assert normalize_name("Gantry & Poole Fittings Co") == "gantry & poole fittings"


def test_trigrams_are_boundary_padded() -> None:
    """Without padding, the first and last characters get less context than
    the middle ones and short aliases score further from their parent."""
    assert trigrams("ab") == [" ab", "ab "]


def test_unrelated_companies_are_far_apart() -> None:
    assert cosine(embed("Meridian Supply"), embed("Advent Holdings")) < 0.2
    assert cosine(embed("Pinebrook Freight Co"), embed("Halcyon Tooling LLC")) < 0.2


# ── keys and ids ─────────────────────────────────────────────────────────────


def test_org_and_person_share_an_id_namespace() -> None:
    """The tagger genuinely disagrees with itself about held-out names.

    Keying on `(type, name)` split `Advent Holdings` across an ORG node and a
    PERSON node and broke the demo's ownership cycle, so the type is a vote
    over the cluster rather than part of its identity.
    """
    assert id_namespace("ORG") == id_namespace("PERSON") == NAMED_NAMESPACE
    assert entity_uuid(ORG_ID, "Advent Holdings", "ORG") == entity_uuid(
        ORG_ID, "Advent Holdings", "PERSON"
    )


def test_value_types_key_on_their_exact_text() -> None:
    """Merging `$4,000` with `$4,900` because they share trigrams would be
    nonsense — an amount is a value, not a party."""
    assert entity_key("$4,000", "MONEY") == "$4,000"
    assert entity_uuid(ORG_ID, "$4,000", "MONEY") != entity_uuid(ORG_ID, "$4,900", "MONEY")


def test_ids_are_stable_across_processes() -> None:
    """UUID5 over the namespace and key (plan §1.11): reseeding after
    `make nuke` must produce the same ids or the prerecorded briefing's
    insight links go dead."""
    assert str(entity_uuid(ORG_ID, "Meridian Supply LLC", "ORG")) == str(
        entity_uuid(ORG_ID, "Meridian Supply, LLC", "ORG")
    )


# ── resolution ───────────────────────────────────────────────────────────────


def test_suffix_variants_merge_on_the_normalized_key(resolver: EntityResolver) -> None:
    first = resolver.resolve("Meridian Supply LLC", "ORG")
    second = resolver.resolve("Meridian Supply, LLC", "ORG")
    third = resolver.resolve("Meridian Supply", "ORG")
    assert first.id == second.id == third.id
    assert resolver.summary()["entities"] == 1


def test_short_forms_merge_by_prefix(resolver: EntityResolver) -> None:
    """`meridian` vs `meridian supply` scores ~0.7 on char-3gram cosine,
    below the 0.86 threshold. Cosine alone leaves the demo's central company
    split in two, so the string heuristic carries this case."""
    full = resolver.resolve("Meridian Supply LLC", "ORG")
    short = resolver.resolve("Meridian", "ORG")
    assert short.id == full.id
    assert resolver.decisions[-1].method == "prefix"


def test_differing_second_tokens_do_not_merge(resolver: EntityResolver) -> None:
    """The sharp edge, bounded: neither name is a prefix of the other."""
    a = resolver.resolve("Pinebrook Freight Co", "ORG")
    b = resolver.resolve("Pinebrook Logistics Ltd", "ORG")
    assert a.id != b.id


def test_short_tokens_cannot_carry_a_prefix_merge(resolver: EntityResolver) -> None:
    a = resolver.resolve("Co Operative Holdings", "ORG")
    b = resolver.resolve("Co Venture Partners", "ORG")
    assert a.id != b.id


def test_entity_type_is_a_majority_vote(resolver: EntityResolver) -> None:
    resolver.resolve("Advent Holdings", "PERSON")
    for _ in range(3):
        resolver.resolve("Advent Holdings", "ORG")
    record = resolver.records()[0]
    assert record.entity_type == "ORG"
    assert record.mention_count == 4


def test_a_tie_in_the_type_vote_goes_to_org(resolver: EntityResolver) -> None:
    """A company mislabeled as a person breaks the ownership cycle; the
    reverse merely mislabels a signatory."""
    resolver.resolve("Advent Holdings", "PERSON")
    resolver.resolve("Advent Holdings", "ORG")
    assert resolver.records()[0].entity_type == "ORG"


def test_canonical_is_the_most_frequent_surface(resolver: EntityResolver) -> None:
    for _ in range(4):
        resolver.resolve("Meridian Supply LLC", "ORG")
    resolver.resolve("Meridian", "ORG")
    record = resolver.records()[0]
    assert record.canonical == "Meridian Supply LLC"
    assert record.aliases == ["Meridian"]


def test_values_never_merge_fuzzily(resolver: EntityResolver) -> None:
    a = resolver.resolve("$4,000", "MONEY")
    b = resolver.resolve("$4,900", "MONEY")
    assert a.id != b.id


def test_existing_entities_are_matched_rather_than_duplicated(resolver: EntityResolver) -> None:
    """Loading history and resolving into it has to hit the same row, or a
    second ingest doubles every company in the graph."""
    entity_id = entity_uuid(ORG_ID, "Meridian Supply LLC", "ORG")
    resolver.add_existing(
        entity_id=entity_id,
        canonical="Meridian Supply LLC",
        entity_type="ORG",
        aliases=["Meridian Supply"],
        mention_count=12,
        embedding=embed("meridian supply").tolist(),
    )
    again = resolver.resolve("Meridian Supply, LLC", "ORG")
    assert again.id == entity_id
    assert not again.is_new
    assert again.mention_count == 13


def test_resolution_order_does_not_change_the_merge_outcome() -> None:
    """Whichever surface arrives first, the cluster is one entity.

    The id is minted from the first key seen, so the two orders can produce
    different ids — but never a different *number* of entities, which is what
    the graph depends on.
    """
    forward = EntityResolver(ORG_ID, threshold=0.86)
    for surface in ("Meridian Supply LLC", "Meridian", "Meridian Supply"):
        forward.resolve(surface, "ORG")

    backward = EntityResolver(ORG_ID, threshold=0.86)
    for surface in ("Meridian", "Meridian Supply", "Meridian Supply LLC"):
        backward.resolve(surface, "ORG")

    assert len(forward.records()) == len(backward.records()) == 1
    assert forward.records()[0].canonical == backward.records()[0].canonical
