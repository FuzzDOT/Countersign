"""Ground truth for the synthetic corpus.

The manifest is the reason this project can report numbers at all. Because we
author the documents, we know — exactly, not approximately — where every entity
span is, which entity pairs carry a relation, and which routing bucket each
insight *should* land in. That makes four otherwise-impossible things possible:

  * tagger token F1 (Stage 2) against gold spans
  * relation precision/recall (Stage 3) against gold triples
  * the routing confusion matrix (Stage 6) against gold buckets
  * `tests/test_offsets.py`, which asserts
    `raw_text[start:end] == surface` for every gold mention

**The honest caveat, stated here and repeated in the writeup.** Routing ground
truth is generator-authored, not human-adjudicated on real documents. We wrote
the fraud, so we know the answer. That is a real limitation of a 24-hour
project without annotators, and it is better said out loud than discovered by a
judge.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from data.synth.templates import Band


@dataclass(frozen=True, slots=True)
class GoldMention:
    """A tagged span, in document coordinates.

    `char_start`/`char_end` index into `GoldDocument.raw_text` and nothing
    else. They come from template rendering, never from a substring search.
    """

    surface: str
    entity_type: str
    char_start: int
    char_end: int
    canonical: str


@dataclass(frozen=True, slots=True)
class GoldRelation:
    relation: str
    subject_canonical: str
    object_canonical: str
    # The citation span: the sentence the relation was stated in.
    char_start: int
    char_end: int
    sentence_text: str
    band: Band
    # What the cascade *should* decide. The Stage 6 confusion matrix compares
    # the pipeline's decision against this.
    routing: str
    template_id: str
    # Hand-written mechanism note, present only on the planted failure case.
    # Surfaces as `documented_failures[].note` in GET /evals/routing.
    failure_note: str | None = None
    # Set on relations whose exculpatory context sits in the *next* sentence.
    has_adjacent_context: bool = False

    @property
    def is_planted_failure(self) -> bool:
        return self.failure_note is not None


@dataclass(frozen=True, slots=True)
class GoldDocument:
    index: int
    document_id: uuid.UUID
    source: str
    title: str
    # Canonical text. Every offset in this document indexes into this string.
    raw_text: str
    received_at: datetime
    mentions: tuple[GoldMention, ...]
    relations: tuple[GoldRelation, ...]

    def verify_offsets(self) -> None:
        """Assert the corpus is self-consistent before anything consumes it.

        Called by the generator on every document. Failing here at hour 1 costs
        a minute; failing silently means every citation in the demo points at
        the wrong sentence and nobody notices until a judge clicks one.
        """
        for mention in self.mentions:
            actual = self.raw_text[mention.char_start : mention.char_end]
            if actual != mention.surface:
                raise AssertionError(
                    f"{self.title}: mention offset mismatch at "
                    f"[{mention.char_start}:{mention.char_end}] — "
                    f"expected {mention.surface!r}, found {actual!r}"
                )
        for relation in self.relations:
            actual = self.raw_text[relation.char_start : relation.char_end]
            if actual != relation.sentence_text:
                raise AssertionError(
                    f"{self.title}: citation span mismatch at "
                    f"[{relation.char_start}:{relation.char_end}] — "
                    f"expected {relation.sentence_text!r}, found {actual!r}"
                )


@dataclass(frozen=True, slots=True)
class Manifest:
    scenario: str
    # "demo" scenarios are servable through the API; "train" is not, ever.
    split: str
    seed: int
    org_id: uuid.UUID
    documents: tuple[GoldDocument, ...] = field(default_factory=tuple)

    # ── derived views ────────────────────────────────────────────────────────

    @property
    def relations(self) -> tuple[GoldRelation, ...]:
        return tuple(r for doc in self.documents for r in doc.relations)

    @property
    def mentions(self) -> tuple[GoldMention, ...]:
        return tuple(m for doc in self.documents for m in doc.mentions)

    @property
    def entities(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for mention in self.mentions:
            seen.setdefault(mention.canonical, None)
        return tuple(seen)

    def named_entities(self) -> tuple[str, ...]:
        """Only ORG and PERSON.

        `entities` also contains MONEY, DATE and ACCOUNT_REF mentions, which
        are rows in the entities table (brief §2 lists them as entity types)
        but are values rather than parties — counting them as "companies in the
        graph" would be misleading.
        """
        seen: dict[str, None] = {}
        for mention in self.mentions:
            if mention.entity_type in ("ORG", "PERSON"):
                seen.setdefault(mention.canonical, None)
        return tuple(seen)

    def band_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for relation in self.relations:
            counts[str(relation.band)] = counts.get(str(relation.band), 0) + 1
        return counts

    def band_mix(self) -> dict[str, float]:
        total = len(self.relations)
        if total == 0:
            return {}
        return {band: n / total for band, n in self.band_counts().items()}

    def routing_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for relation in self.relations:
            counts[relation.routing] = counts.get(relation.routing, 0) + 1
        return counts

    def planted_failures(self) -> tuple[GoldRelation, ...]:
        return tuple(r for r in self.relations if r.is_planted_failure)

    def verify(self) -> None:
        """Whole-corpus invariants, checked on every generation."""
        for document in self.documents:
            document.verify_offsets()

        if self.split == "train":
            offenders = {str(r.band) for r in self.relations} - {"A", "B", "C"}
            if offenders:
                raise AssertionError(
                    f"training corpus contains held-out band(s) {sorted(offenders)}"
                )
        else:
            # A servable scenario with no out-of-distribution tail cannot
            # produce vacuity variance, and without vacuity variance the
            # fragility correlation is noise (plan §0).
            if not any(str(r.band) == "D" for r in self.relations):
                raise AssertionError(
                    f"scenario {self.scenario!r} has no band-D relations; vacuity would be "
                    "flat and the fragility correlation meaningless"
                )

    # ── serialization ────────────────────────────────────────────────────────

    def to_json(self) -> str:
        def encode(value: object) -> object:
            if isinstance(value, uuid.UUID):
                return str(value)
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, Band):
                return str(value)
            raise TypeError(f"unserializable: {type(value)!r}")

        return json.dumps(
            {
                "scenario": self.scenario,
                "split": self.split,
                "seed": self.seed,
                "org_id": str(self.org_id),
                "summary": {
                    "documents": len(self.documents),
                    "mentions": len(self.mentions),
                    "relations": len(self.relations),
                    "entities": len(self.entities),
                    "band_counts": self.band_counts(),
                    "routing_counts": self.routing_counts(),
                    "planted_failures": len(self.planted_failures()),
                },
                "documents": [asdict(d) for d in self.documents],
            },
            indent=2,
            default=encode,
        )

    def write(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.scenario}.manifest.json"
        path.write_text(self.to_json(), encoding="utf-8")
        return path
