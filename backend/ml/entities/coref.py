"""Coreference: many surface forms, one entity row.

Plan §1.5: normalization heuristic first, cosine over the 256-dim char-3gram
embedding second, threshold 0.86. Deliberately classical — there is no model
to load and no training step, and the merge decision is one number a judge can
argue with.

Four properties that are decisions rather than accidents:

- **ORG and PERSON share one namespace, and the type is a vote.** The tagger
  is genuinely unsure whether `Advent Holdings` is a company or a person —
  that is the held-out pool doing its job — and it will call the same string
  both things in different sentences. Keying the cluster on the name and
  taking the majority type over its mentions keeps the entity whole. Keying on
  `(type, name)` instead splits the demo's ownership cycle across an ORG node
  and a PERSON node, which is how this was found.
- **Only named types get fuzzy matching.** MONEY, DATE, ACCOUNT_REF and
  TRANSACTION_TYPE are values, not parties. Merging `$4,000` with `$4,900`
  because they share trigrams would be nonsense, so those match exactly or
  not at all.
- **Resolution runs in memory over the org's entity set, not as a pgvector
  query per mention.** The HNSW index in `db/models.py` is real and is what a
  larger deployment would use, but a batch creates entities *while* it
  resolves against them — an in-batch mention has no row to query yet. One
  load at job start, a dense cosine against a few thousand rows, and the
  merges within a batch work the same way as the merges against history.
- **Entity ids are UUID5 over the cluster's normalized key** (plan §1.11), so
  reseeding the demo produces the same ids and the prerecorded briefing's
  `insight_id` values stay valid across `make nuke`.

Known and stated: this *will* merge two genuinely distinct companies with
similar names. `entities.aliases` is returned by the API for exactly that
reason — the merge is visible rather than hidden.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from core import ids
from core.logging import get_logger
from ml.entities.embed import embed, normalize_name

log = get_logger(__name__)

# Types whose identity is their exact string. No fuzzy matching, no voting.
VALUE_TYPES = frozenset({"MONEY", "DATE", "ACCOUNT_REF", "TRANSACTION_TYPE"})
NAMED_TYPES = frozenset({"ORG", "PERSON"})

# The id namespace ORG and PERSON share. Not a stored entity type — it exists
# so that an entity's id does not move when the type vote flips from PERSON to
# ORG on the fourth mention.
NAMED_NAMESPACE = "NAMED"

# Ties in the type vote go to the earlier entry. ORG first: the parties in
# this graph are overwhelmingly companies, and a company mislabeled as a
# person breaks the ownership cycle while the reverse merely mislabels a
# signatory.
TYPE_PRECEDENCE: tuple[str, ...] = ("ORG", "PERSON")

# A token has to be this long before it can carry a prefix merge on its own.
# Without it, `co` and `the` would merge half the corpus.
MIN_PREFIX_TOKEN_CHARS = 4


def id_namespace(entity_type: str) -> str:
    return NAMED_NAMESPACE if entity_type in NAMED_TYPES else entity_type


def entity_key(surface: str, entity_type: str) -> str:
    """The cluster key for a surface.

    Values key on their exact text; names key on the normalized form, so
    `Meridian Supply, LLC` and `Meridian Supply LLC` collide before the cosine
    path is ever reached.
    """
    if entity_type in VALUE_TYPES:
        return surface
    return normalize_name(surface) or surface.casefold()


def entity_uuid(org_id: uuid.UUID, surface: str, entity_type: str) -> uuid.UUID:
    """Deterministic id for the entity a surface belongs to (plan §1.11).

    Module-level because `data/synth/fixtures.py` mints entity ids the same
    way. If the two disagreed, an entity would have one id in mock mode and a
    different one live, and the frontend's mock-to-real switchover would
    silently lose every entity link.
    """
    return ids.entity_id(org_id, entity_key(surface, entity_type), id_namespace(entity_type))


@dataclass(slots=True)
class EntityRecord:
    """One resolved entity, in memory, during a job."""

    id: uuid.UUID
    namespace: str
    # The key the cluster was created under. Stable for the life of the
    # entity: the id is derived from it, so it must never be recomputed from
    # a later surface form.
    cluster_key: str
    embedding: np.ndarray | None
    surface_counts: Counter[str] = field(default_factory=Counter)
    type_counts: Counter[str] = field(default_factory=Counter)
    mention_count: int = 0
    is_new: bool = False
    dirty: bool = False

    @property
    def entity_type(self) -> str:
        """Majority vote over the mentions, ties broken by precedence.

        The tagger disagrees with itself about held-out names, and one
        mislabeled mention should not decide what a 34-mention company is.
        """
        if not self.type_counts:
            return self.namespace
        best = max(self.type_counts.values())
        tied = [t for t, n in self.type_counts.items() if n == best]
        if len(tied) == 1:
            return tied[0]
        for candidate in TYPE_PRECEDENCE:
            if candidate in tied:
                return candidate
        return sorted(tied)[0]

    @property
    def canonical(self) -> str:
        """The surface form to display.

        Most frequent, then longest, then lexicographic. Frequency first
        because band A always writes the full legal name and the aliases show
        up in the harder bands — so the common form is the complete one,
        which is what an analyst should see in the graph.
        """
        if not self.surface_counts:
            return self.cluster_key
        return max(
            self.surface_counts.items(),
            key=lambda item: (item[1], len(item[0]), item[0]),
        )[0]

    @property
    def aliases(self) -> list[str]:
        """Every other surface form seen, most frequent first.

        Returned by the API so a merge this module got wrong is visible to
        whoever is looking at the entity rather than buried.
        """
        canonical = self.canonical
        return sorted(
            (s for s in self.surface_counts if s != canonical),
            key=lambda s: (-self.surface_counts[s], s),
        )

    def observe(self, surface: str, entity_type: str) -> None:
        self.surface_counts[surface] += 1
        self.type_counts[entity_type] += 1
        self.mention_count += 1
        self.dirty = True


@dataclass(frozen=True, slots=True)
class MergeDecision:
    """Why a mention resolved the way it did. Logged, not returned by the API."""

    surface: str
    entity_id: uuid.UUID
    method: str  # "exact" | "prefix" | "cosine" | "new"
    score: float


class EntityResolver:
    """Resolves mention surfaces to entities for one organization."""

    def __init__(self, org_id: uuid.UUID, *, threshold: float) -> None:
        self.org_id = org_id
        self.threshold = threshold
        self._by_key: dict[tuple[str, str], EntityRecord] = {}
        self._by_id: dict[uuid.UUID, EntityRecord] = {}
        # Parallel arrays per namespace, so cosine is one matrix product
        # rather than a Python loop over every entity in the org.
        self._vectors: dict[str, list[np.ndarray]] = {}
        self._vector_owners: dict[str, list[uuid.UUID]] = {}
        self.decisions: list[MergeDecision] = []

    # ── loading existing rows ────────────────────────────────────────────────

    def add_existing(
        self,
        entity_id: uuid.UUID,
        canonical: str,
        entity_type: str,
        aliases: list[str],
        mention_count: int,
        embedding: list[float] | None,
    ) -> None:
        """Seed the index from `entities` rows already in the database.

        The cluster key is recomputed from `canonical`, which is safe because
        `canonical` is itself always one of the cluster's surfaces and
        `normalize_name` is idempotent — an entity loaded from the database
        resolves to the same key it was created under.
        """
        namespace = id_namespace(entity_type)
        key = entity_key(canonical, entity_type)
        record = EntityRecord(
            id=entity_id,
            namespace=namespace,
            cluster_key=key,
            embedding=np.asarray(embedding, dtype=np.float32) if embedding else None,
        )
        record.surface_counts[canonical] = max(mention_count, 1)
        record.type_counts[entity_type] = max(mention_count, 1)
        for alias in aliases:
            record.surface_counts.setdefault(alias, 1)
        record.mention_count = mention_count
        self._register(record, key)
        for alias in aliases:
            self._by_key.setdefault((namespace, entity_key(alias, entity_type)), record)

    # ── resolution ───────────────────────────────────────────────────────────

    def resolve(self, surface: str, entity_type: str) -> EntityRecord:
        namespace = id_namespace(entity_type)
        key = entity_key(surface, entity_type)

        existing = self._by_key.get((namespace, key))
        if existing is not None:
            existing.observe(surface, entity_type)
            self.decisions.append(MergeDecision(surface, existing.id, "exact", 1.0))
            return existing

        if namespace == NAMED_NAMESPACE:
            shortened = self._prefix_match(key)
            if shortened is not None:
                shortened.observe(surface, entity_type)
                self._by_key[(namespace, key)] = shortened
                self.decisions.append(MergeDecision(surface, shortened.id, "prefix", 1.0))
                return shortened

            match, score = self._nearest(key)
            if match is not None and score >= self.threshold:
                match.observe(surface, entity_type)
                # Index the new surface so the next occurrence is an exact
                # hit rather than another cosine scan.
                self._by_key[(namespace, key)] = match
                self.decisions.append(MergeDecision(surface, match.id, "cosine", score))
                return match

        record = EntityRecord(
            id=ids.entity_id(self.org_id, key, namespace),
            namespace=namespace,
            cluster_key=key,
            embedding=embed(key) if namespace == NAMED_NAMESPACE else None,
            is_new=True,
            dirty=True,
        )
        record.observe(surface, entity_type)
        self._register(record, key)
        self.decisions.append(MergeDecision(surface, record.id, "new", 0.0))
        return record

    def _prefix_match(self, key: str) -> EntityRecord | None:
        """Whole-token prefix containment, in either direction.

        `Meridian` and `Meridian Supply LLC` normalize to `meridian` and
        `meridian supply`, which share no suffix and score ~0.7 on char-3gram
        cosine — below the 0.86 threshold, so cosine alone leaves the demo's
        central company split across two graph nodes and breaks the ownership
        cycle. A shortened form of a company name is the commonest alias in
        real correspondence, so this is a string heuristic worth having
        alongside the cosine (plan §1.5 pairs the two deliberately).

        It is also the sharpest edge in this module: `Pinebrook Freight` and a
        hypothetical `Pinebrook Logistics` would *not* merge (neither is a
        prefix of the other), but `Pinebrook` and either one would. That is
        the known debt the brief already lists, made concrete.
        """
        tokens = key.split()
        if not tokens or len(tokens[0]) < MIN_PREFIX_TOKEN_CHARS:
            return None

        best: EntityRecord | None = None
        best_overlap = 0
        for (namespace, candidate_key), record in self._by_key.items():
            if namespace != NAMED_NAMESPACE or candidate_key == key:
                continue
            other = candidate_key.split()
            shorter, longer = (tokens, other) if len(tokens) < len(other) else (other, tokens)
            if not shorter or shorter != longer[: len(shorter)]:
                continue
            if len(shorter) > best_overlap:
                best, best_overlap = record, len(shorter)
        return best

    def _nearest(self, key: str) -> tuple[EntityRecord | None, float]:
        vectors = self._vectors.get(NAMED_NAMESPACE)
        if not vectors:
            return None, 0.0
        scores = np.vstack(vectors) @ embed(key)
        best = int(np.argmax(scores))
        return self._by_id[self._vector_owners[NAMED_NAMESPACE][best]], float(scores[best])

    # ── bookkeeping ──────────────────────────────────────────────────────────

    def _register(self, record: EntityRecord, key: str) -> None:
        self._by_key[(record.namespace, key)] = record
        self._by_id[record.id] = record
        if record.embedding is not None:
            self._vectors.setdefault(record.namespace, []).append(record.embedding)
            self._vector_owners.setdefault(record.namespace, []).append(record.id)

    # ── output ───────────────────────────────────────────────────────────────

    def records(self) -> list[EntityRecord]:
        return list(self._by_id.values())

    def new_records(self) -> list[EntityRecord]:
        return [record for record in self._by_id.values() if record.is_new]

    def dirty_records(self) -> list[EntityRecord]:
        return [record for record in self._by_id.values() if record.dirty]

    def summary(self) -> dict[str, int]:
        counts = Counter(decision.method for decision in self.decisions)
        return {
            "mentions": len(self.decisions),
            "entities": len(self._by_id),
            "merged_exact": counts.get("exact", 0),
            "merged_prefix": counts.get("prefix", 0),
            "merged_cosine": counts.get("cosine", 0),
            "created": counts.get("new", 0),
        }
