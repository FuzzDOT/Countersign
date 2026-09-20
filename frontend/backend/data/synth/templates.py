"""Sentence templates, banded by difficulty.

**Offsets come from construction, not from searching.** A template is a tuple
of literal strings and `Slot` objects; rendering concatenates them and records
each slot's exact `(char_start, char_end)` as it goes. Nothing ever calls
`str.find()` to locate an entity, which is how the corpus can be the ground
truth for `tests/test_offsets.py` rather than another thing that needs
verifying. A `find()`-based generator breaks silently the first time an entity
name appears twice in one sentence.

**The bands are the epistemic spread.** Brief §4.2 wants vacuity to mean
something, and a model can only be uncertain about constructions it has not
seen. The four bands, and roughly what they should produce:

  A  canonical   active voice, adjacent arguments, verbs seen in training
  B  varied      passive voice, intervening clauses, aliased surface forms
  C  indirect    nominalized relations, third-party routing, appositives
  D  OOD         syntax held out of training entirely, plus held-out names

Band D templates are never used in the `train_corpus` scenario. That is
enforced in `templates_for()` rather than left to the caller to remember.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Band(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


TRAINABLE_BANDS: frozenset[Band] = frozenset({Band.A, Band.B, Band.C})
"""Bands the tagger and relation model are allowed to train on."""

# Target share of relations per band in a servable scenario (plan §0).
BAND_MIX: dict[Band, float] = {Band.A: 0.40, Band.B: 0.30, Band.C: 0.20, Band.D: 0.10}


@dataclass(frozen=True, slots=True)
class Slot:
    """A filled hole in a template.

    `label` is the entity tag to record as a gold mention, or None for text
    that should be substituted but not tagged — addresses and day counts are
    real text with no label in brief §15's tag set.

    `role` names which binding fills the slot: `subject` and `object` are the
    relation's arguments; the rest are incidental entities that still need
    tagging.
    """

    key: str
    label: str | None = None
    role: str | None = None


@dataclass(frozen=True, slots=True)
class Template:
    id: str
    relation: str
    band: Band
    parts: tuple[str | Slot, ...]

    @property
    def slot_keys(self) -> tuple[str, ...]:
        return tuple(p.key for p in self.parts if isinstance(p, Slot))


@dataclass(frozen=True, slots=True)
class RenderedMention:
    surface: str
    entity_type: str
    char_start: int
    char_end: int
    canonical: str
    role: str | None


@dataclass(frozen=True, slots=True)
class RenderedSentence:
    text: str
    mentions: tuple[RenderedMention, ...]
    template_id: str
    relation: str
    band: Band


@dataclass(frozen=True, slots=True)
class Binding:
    """What fills one slot: the surface form written into the text, plus the
    canonical entity it resolves to."""

    surface: str
    canonical: str
    entity_type: str | None


def render(
    template: Template,
    bindings: dict[str, Binding],
    *,
    offset: int = 0,
) -> RenderedSentence:
    """Render a template, recording exact character offsets for every mention.

    `offset` is the sentence's start position in the enclosing document, so the
    returned mentions are already in document coordinates and no caller has to
    add anything.
    """
    missing = [key for key in template.slot_keys if key not in bindings]
    if missing:
        raise KeyError(f"template {template.id} needs bindings for {missing}")

    pieces: list[str] = []
    mentions: list[RenderedMention] = []
    cursor = offset

    for part in template.parts:
        if isinstance(part, str):
            pieces.append(part)
            cursor += len(part)
            continue

        binding = bindings[part.key]
        surface = binding.surface
        start = cursor
        end = cursor + len(surface)
        pieces.append(surface)
        cursor = end

        # `label is None` means substitute the text but record no mention —
        # addresses, day counts, and other untagged literals.
        if part.label is not None:
            mentions.append(
                RenderedMention(
                    surface=surface,
                    entity_type=binding.entity_type or part.label,
                    char_start=start,
                    char_end=end,
                    canonical=binding.canonical,
                    role=part.role,
                )
            )

    return RenderedSentence(
        text="".join(pieces),
        mentions=tuple(mentions),
        template_id=template.id,
        relation=template.relation,
        band=template.band,
    )


# ── slot shorthands ──────────────────────────────────────────────────────────

SUBJ_ORG = Slot("subject", "ORG", "subject")
OBJ_ORG = Slot("object", "ORG", "object")
SUBJ_PERSON = Slot("subject", "PERSON", "subject")
AMOUNT = Slot("amount", "MONEY")
DATE = Slot("date", "DATE")
ACCOUNT = Slot("account", "ACCOUNT_REF")
TXTYPE = Slot("txtype", "TRANSACTION_TYPE")
ADDRESS = Slot("address", None)  # real text, no label in the tag set
DAYS = Slot("days", None)


# ── WIRED_FUNDS_TO ───────────────────────────────────────────────────────────

WIRED_FUNDS_TO: tuple[Template, ...] = (
    Template(
        "wired.a1",
        "WIRED_FUNDS_TO",
        Band.A,
        (SUBJ_ORG, " wired ", AMOUNT, " to ", OBJ_ORG, " on ", DATE, "."),
    ),
    Template(
        "wired.a2",
        "WIRED_FUNDS_TO",
        Band.A,
        (SUBJ_ORG, " sent a payment of ", AMOUNT, " to ", OBJ_ORG, "."),
    ),
    Template(
        "wired.a3",
        "WIRED_FUNDS_TO",
        Band.A,
        (SUBJ_ORG, " paid ", OBJ_ORG, " ", AMOUNT, " by ", TXTYPE, " on ", DATE, "."),
    ),
    Template(
        "wired.b1",
        "WIRED_FUNDS_TO",
        Band.B,
        ("A payment of ", AMOUNT, " was wired to ", OBJ_ORG, " by ", SUBJ_ORG, " on ", DATE, "."),
    ),
    Template(
        "wired.b2",
        "WIRED_FUNDS_TO",
        Band.B,
        (
            "On ",
            DATE,
            ", following internal approval, ",
            SUBJ_ORG,
            " transferred ",
            AMOUNT,
            " to ",
            OBJ_ORG,
            " under reference ",
            ACCOUNT,
            ".",
        ),
    ),
    # The demo sentence. Pinned by id in the meridian_shell_ring plan so the
    # rehearsed script and the prerecorded fallback transcript stay valid.
    Template(
        "wired.c1",
        "WIRED_FUNDS_TO",
        Band.C,
        (
            "Payment of ",
            AMOUNT,
            " was routed through ",
            OBJ_ORG,
            " on behalf of ",
            SUBJ_ORG,
            ".",
        ),
    ),
    Template(
        "wired.c2",
        "WIRED_FUNDS_TO",
        Band.C,
        (
            "Settlement of ",
            ACCOUNT,
            " — ",
            AMOUNT,
            ", cleared via ",
            OBJ_ORG,
            " at the direction of ",
            SUBJ_ORG,
            " — completed ",
            DATE,
            ".",
        ),
    ),
    Template(
        "wired.d1",
        "WIRED_FUNDS_TO",
        Band.D,
        (
            "Per the standing instruction referenced above, the ",
            AMOUNT,
            " obligation of ",
            SUBJ_ORG,
            " was discharged by remittance to ",
            OBJ_ORG,
            ".",
        ),
    ),
    Template(
        "wired.d2",
        "WIRED_FUNDS_TO",
        Band.D,
        (
            OBJ_ORG,
            ", acting as paying agent, took receipt of ",
            AMOUNT,
            " for the account of ",
            SUBJ_ORG,
            " pursuant to the ",
            DATE,
            " memorandum.",
        ),
    ),
)

# ── OWNED_BY ─────────────────────────────────────────────────────────────────

OWNED_BY: tuple[Template, ...] = (
    Template(
        "owned.a1",
        "OWNED_BY",
        Band.A,
        (SUBJ_ORG, " is a wholly owned subsidiary of ", OBJ_ORG, "."),
    ),
    Template("owned.a2", "OWNED_BY", Band.A, (OBJ_ORG, " owns ", SUBJ_ORG, " outright.")),
    Template(
        "owned.b1",
        "OWNED_BY",
        Band.B,
        (SUBJ_ORG, " is held, directly and indirectly, by ", OBJ_ORG, " as of ", DATE, "."),
    ),
    Template(
        "owned.c1",
        "OWNED_BY",
        Band.C,
        (
            "Ownership of ",
            SUBJ_ORG,
            " vests in ",
            OBJ_ORG,
            " through an intermediate holding structure.",
        ),
    ),
    Template(
        "owned.d1",
        "OWNED_BY",
        Band.D,
        (
            "The beneficial interest in ",
            SUBJ_ORG,
            " is attributed to ",
            OBJ_ORG,
            " under the look-through provisions cited in the ",
            DATE,
            " filing.",
        ),
    ),
)

# ── INVOICED ─────────────────────────────────────────────────────────────────

INVOICED: tuple[Template, ...] = (
    Template(
        "invoiced.a1",
        "INVOICED",
        Band.A,
        (SUBJ_ORG, " invoiced ", OBJ_ORG, " ", AMOUNT, " on ", DATE, "."),
    ),
    Template(
        "invoiced.a2",
        "INVOICED",
        Band.A,
        ("Invoice ", ACCOUNT, " from ", SUBJ_ORG, " to ", OBJ_ORG, " totals ", AMOUNT, "."),
    ),
    Template(
        "invoiced.b1",
        "INVOICED",
        Band.B,
        (OBJ_ORG, " was invoiced ", AMOUNT, " by ", SUBJ_ORG, " under reference ", ACCOUNT, "."),
    ),
    Template(
        "invoiced.c1",
        "INVOICED",
        Band.C,
        (
            "Charges of ",
            AMOUNT,
            " raised by ",
            SUBJ_ORG,
            " against ",
            OBJ_ORG,
            " appear under reference ",
            ACCOUNT,
            ".",
        ),
    ),
    Template(
        "invoiced.d1",
        "INVOICED",
        Band.D,
        (
            AMOUNT,
            " in receivables recognized by ",
            SUBJ_ORG,
            " is attributable to services rendered for ",
            OBJ_ORG,
            ".",
        ),
    ),
    # The planted failure case (plan §0). The timing anomaly lives in the
    # citation sentence; the benign contractual explanation lives in the
    # *following* sentence, which the cascade never sees because it passes a
    # single citation sentence. An honest, mechanistically explicable failure
    # of our own architecture, present by construction rather than by luck.
    Template(
        "invoiced.timing",
        "INVOICED",
        Band.B,
        (
            "Invoice ",
            ACCOUNT,
            " from ",
            SUBJ_ORG,
            " to ",
            OBJ_ORG,
            " was submitted ",
            DAYS,
            " days ahead of the contracted schedule.",
        ),
    ),
)

# ── SHARES_ADDRESS_WITH ──────────────────────────────────────────────────────

SHARES_ADDRESS_WITH: tuple[Template, ...] = (
    Template(
        "address.a1",
        "SHARES_ADDRESS_WITH",
        Band.A,
        (SUBJ_ORG, " and ", OBJ_ORG, " are both registered at ", ADDRESS, "."),
    ),
    Template(
        "address.b1",
        "SHARES_ADDRESS_WITH",
        Band.B,
        (
            "The registered office of ",
            SUBJ_ORG,
            " at ",
            ADDRESS,
            " is also the registered office of ",
            OBJ_ORG,
            ".",
        ),
    ),
    Template(
        "address.c1",
        "SHARES_ADDRESS_WITH",
        Band.C,
        (
            "Correspondence for ",
            SUBJ_ORG,
            " is directed to ",
            ADDRESS,
            ", which also appears on filings for ",
            OBJ_ORG,
            ".",
        ),
    ),
    Template(
        "address.d1",
        "SHARES_ADDRESS_WITH",
        Band.D,
        (
            ADDRESS,
            " is given as the service address on filings for ",
            SUBJ_ORG,
            " and, separately and on a later date, for ",
            OBJ_ORG,
            ".",
        ),
    ),
)

# ── SIGNATORY_OF ─────────────────────────────────────────────────────────────

SIGNATORY_OF: tuple[Template, ...] = (
    Template(
        "signatory.a1",
        "SIGNATORY_OF",
        Band.A,
        (SUBJ_PERSON, " is an authorized signatory of ", OBJ_ORG, "."),
    ),
    Template(
        "signatory.a2",
        "SIGNATORY_OF",
        Band.A,
        (SUBJ_PERSON, " signed on behalf of ", OBJ_ORG, " on ", DATE, "."),
    ),
    Template(
        "signatory.b1",
        "SIGNATORY_OF",
        Band.B,
        ("Signing authority for ", OBJ_ORG, " rests with ", SUBJ_PERSON, "."),
    ),
    Template(
        "signatory.c1",
        "SIGNATORY_OF",
        Band.C,
        (
            "Payment instructions for ",
            OBJ_ORG,
            " bear the signature of ",
            SUBJ_PERSON,
            " in every instance reviewed.",
        ),
    ),
    Template(
        "signatory.d1",
        "SIGNATORY_OF",
        Band.D,
        (
            SUBJ_PERSON,
            ", whose authority over ",
            OBJ_ORG,
            " derives from the ",
            DATE,
            " board resolution, executed the instruction.",
        ),
    ),
)

# ── NO_RELATION filler ───────────────────────────────────────────────────────
# Fillers matter for two reasons: they give the relation extractor negatives to
# learn from, and they give the boilerplate perturbation family something
# realistic to inject. Several carry taggable mentions so that a sentence
# having entities does not by itself imply a relation.

FILLER: tuple[Template, ...] = (
    Template(
        "filler.confidential",
        "NO_RELATION",
        Band.A,
        ("This message and any attachments are confidential and intended for the addressee only.",),
    ),
    Template(
        "filler.terms",
        "NO_RELATION",
        Band.A,
        ("Payment terms are net ", DAYS, " days from the invoice date."),
    ),
    Template(
        "filler.reference",
        "NO_RELATION",
        Band.A,
        ("Please quote reference ", ACCOUNT, " on all correspondence."),
    ),
    Template(
        "filler.ap",
        "NO_RELATION",
        Band.A,
        ("Questions about this document should be directed to accounts payable.",),
    ),
    Template(
        "filler.txtype",
        "NO_RELATION",
        Band.B,
        ("Settlement method for this period is ", TXTYPE, ", effective ", DATE, "."),
    ),
    Template(
        "filler.audit",
        "NO_RELATION",
        Band.B,
        ("Records for this period were reviewed on ", DATE, " and retained for audit."),
    ),
    Template(
        "filler.vat",
        "NO_RELATION",
        Band.B,
        ("Amounts shown exclude tax unless stated otherwise.",),
    ),
)

# The exculpatory sentence for the planted failure. Not a template with slots —
# it is one fixed sentence, placed immediately after `invoiced.timing`, and its
# whole job is to be invisible to a cascade that reads one sentence.
EXCULPATORY_SENTENCE = (
    "The contract permits early submission when the delivery milestone completes "
    "ahead of schedule, and the milestone record for this period confirms that it did."
)

ALL_TEMPLATES: tuple[Template, ...] = (
    *WIRED_FUNDS_TO,
    *OWNED_BY,
    *INVOICED,
    *SHARES_ADDRESS_WITH,
    *SIGNATORY_OF,
    *FILLER,
)

BY_ID: dict[str, Template] = {t.id: t for t in ALL_TEMPLATES}

RELATION_TEMPLATES: dict[str, tuple[Template, ...]] = {
    "WIRED_FUNDS_TO": WIRED_FUNDS_TO,
    "OWNED_BY": OWNED_BY,
    "INVOICED": INVOICED,
    "SHARES_ADDRESS_WITH": SHARES_ADDRESS_WITH,
    "SIGNATORY_OF": SIGNATORY_OF,
    "NO_RELATION": FILLER,
}


def templates_for(relation: str, band: Band, *, split: str) -> tuple[Template, ...]:
    """Candidate templates for a relation and band.

    Band D is refused for the training split at the point of use, so no caller
    has to remember the rule. A training corpus containing held-out syntax
    would quietly destroy the fragility eval.
    """
    if split == "train" and band not in TRAINABLE_BANDS:
        raise ValueError(
            f"band {band} is held out of training; requesting it for the train split "
            "would contaminate the fragility and routing evals"
        )
    candidates = tuple(t for t in RELATION_TEMPLATES[relation] if t.band is band)
    if not candidates:
        raise LookupError(f"no {relation} template in band {band}")
    return candidates


def assert_template_coverage() -> None:
    """Every relation must have at least one template in every band.

    Asserted in tests/test_synth.py: a missing band would silently skew the
    band mix, and the band mix is what produces vacuity variance.
    """
    for relation, templates in RELATION_TEMPLATES.items():
        if relation == "NO_RELATION":
            continue
        bands = {t.band for t in templates}
        missing = set(Band) - bands
        if missing:
            raise AssertionError(f"{relation} has no template in band(s) {sorted(missing)}")
