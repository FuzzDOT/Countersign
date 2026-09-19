"""Deterministic document generator.

Turns a `ScenarioSpec` into documents plus a ground-truth manifest. Two
properties matter more than anything else here.

**Offsets are constructed, never searched.** `_DocBuilder` maintains a running
character cursor as it appends text, so every mention's `(char_start,
char_end)` is exact by construction. A generator that located entities with
`str.find()` would be wrong the first time a company name appeared twice in one
document — and wrong quietly, which is worse. Every document is verified
against its own text before it is returned.

**Everything is a pure function of the seed.** Same seed and scenario gives the
same bytes and the same UUIDs on any machine. `random.Random` seeded with a
string uses SHA-512 rather than `hash()`, so this does not depend on
PYTHONHASHSEED. That is what lets the demo be rehearsed on one laptop and run
on another, and what keeps the prerecorded fallback transcript's `insight_id`
values valid after `make nuke`.

Run it:
    python -m data.synth.generate --scenario meridian_shell_ring --report
    python -m data.synth.generate --scenario meridian_shell_ring --sample 1
"""

from __future__ import annotations

import argparse
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from core import ids
from data.synth import names
from data.synth.labels import GoldDocument, GoldMention, GoldRelation, Manifest
from data.synth.scenarios import (
    GeneratedRelations,
    RelationPlan,
    ScenarioSpec,
    get_scenario,
)
from data.synth.templates import (
    BY_ID,
    EXCULPATORY_SENTENCE,
    FILLER,
    Band,
    Binding,
    Template,
    render,
    templates_for,
)

# Documents are dated backwards from a fixed epoch so `received_at DESC`
# ordering does not depend on when the generator ran.
CORPUS_EPOCH = datetime(2026, 9, 14, 8, 31, tzinfo=UTC)

# Matches PIPELINE_SEED in .env.example. Duplicated as a literal on purpose:
# corpus generation is a data-authoring task and should not require the
# application's settings (and therefore a database URL and a JWT secret) to be
# loadable. `_default_seed()` prefers the configured value when it is available.
FALLBACK_SEED = 20260919

# Relations whose subject is a person rather than an organization.
PERSON_SUBJECT_RELATIONS = frozenset({"SIGNATORY_OF"})


def _default_seed() -> int:
    """PIPELINE_SEED when the app config loads, the literal otherwise.

    Lazy so `python -m data.synth.generate` works in a bare checkout without
    a .env — useful when inspecting the corpus, and it keeps the generator
    unit-testable without standing up configuration.
    """
    try:
        from core.config import get_settings

        return get_settings().pipeline_seed
    except Exception:  # noqa: BLE001 - config is optional here by design
        return FALLBACK_SEED

MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


# ── document builder ─────────────────────────────────────────────────────────


@dataclass(slots=True)
class _DocBuilder:
    """Accumulates document text while tracking exact offsets."""

    parts: list[str]
    cursor: int
    mentions: list[GoldMention]
    relations: list[GoldRelation]

    @classmethod
    def new(cls) -> _DocBuilder:
        return cls(parts=[], cursor=0, mentions=[], relations=[])

    def literal(self, text: str) -> None:
        self.parts.append(text)
        self.cursor += len(text)

    def mention(self, surface: str, entity_type: str, canonical: str) -> None:
        """Append text and record it as a gold mention.

        Used for entities in document headers — an invoice's vendor line, an
        email's recipient. Those are real mentions the tagger should learn and
        the citation reader should highlight, so they carry gold labels exactly
        like entities inside a sentence.
        """
        start = self.cursor
        self.literal(surface)
        self.mentions.append(
            GoldMention(
                surface=surface,
                entity_type=entity_type,
                char_start=start,
                char_end=self.cursor,
                canonical=canonical,
            )
        )

    def sentence(
        self,
        template: Template,
        bindings: dict[str, Binding],
        *,
        routing: str | None = None,
        failure_note: str | None = None,
        with_adjacent_context: bool = False,
    ) -> None:
        start = self.cursor
        rendered = render(template, bindings, offset=start)
        self.parts.append(rendered.text)
        self.cursor += len(rendered.text)

        for mention in rendered.mentions:
            self.mentions.append(
                GoldMention(
                    surface=mention.surface,
                    entity_type=mention.entity_type,
                    char_start=mention.char_start,
                    char_end=mention.char_end,
                    canonical=mention.canonical,
                )
            )

        if rendered.relation != "NO_RELATION" and routing is not None:
            subject = next((m for m in rendered.mentions if m.role == "subject"), None)
            obj = next((m for m in rendered.mentions if m.role == "object"), None)
            if subject is None or obj is None:  # pragma: no cover - template bug
                raise AssertionError(f"template {template.id} produced no subject/object mention")
            self.relations.append(
                GoldRelation(
                    relation=rendered.relation,
                    subject_canonical=subject.canonical,
                    object_canonical=obj.canonical,
                    char_start=start,
                    char_end=self.cursor,
                    sentence_text=rendered.text,
                    band=rendered.band,
                    routing=routing,
                    template_id=template.id,
                    failure_note=failure_note,
                    has_adjacent_context=with_adjacent_context,
                )
            )

        if with_adjacent_context:
            # The exculpatory sentence sits immediately AFTER the citation span
            # and is deliberately not part of it. That gap is the mechanism the
            # documented failure in GET /evals/routing describes.
            self.literal(" " + EXCULPATORY_SENTENCE)

    def text(self) -> str:
        return "".join(self.parts)


# ── slot values ──────────────────────────────────────────────────────────────


def _money(rng: random.Random) -> str:
    """Mixed magnitudes, mixed roundness.

    An all-round-thousands corpus would make the MONEY regex in the tagger's
    weak supervision trivially perfect, which inflates the reported F1 in a way
    that does not survive a judge asking how the labels were produced.
    """
    magnitude = rng.choice((3, 3, 4, 4, 4, 5))
    value = rng.randint(10 ** (magnitude - 1), 10**magnitude - 1)
    if rng.random() < 0.35:
        return f"${value - (value % 100):,}"
    return f"${value:,}.{rng.randint(0, 99):02d}"


def _date(rng: random.Random, received: datetime) -> str:
    """Three formats, for the same reason `_money` mixes roundness."""
    moment = received - timedelta(days=rng.randint(1, 45))
    style = rng.randint(0, 2)
    if style == 0:
        return f"{moment.day} {MONTHS[moment.month - 1]} {moment.year}"
    if style == 1:
        return moment.strftime("%Y-%m-%d")
    return f"{MONTHS[moment.month - 1][:3]} {moment.day}, {moment.year}"


def _account(rng: random.Random) -> str:
    return rng.choice(names.ACCOUNT_FORMATS).format(n=rng.randint(1, 99999))


def _surface_for(canonical: str, band: Band, rng: random.Random) -> str:
    """Pick a surface form, using aliases more often in the harder bands.

    Band A always uses the canonical name. From band B onward aliases appear,
    which is what gives coreference something real to resolve and stops the
    tagger from seeing exactly one fixed string per entity.
    """
    variants = names.aliases_for(canonical)
    if band is Band.A or len(variants) == 1:
        return canonical
    weight = {Band.B: 0.35, Band.C: 0.50, Band.D: 0.60}.get(band, 0.0)
    return rng.choice(variants[1:]) if rng.random() < weight else canonical


def _value_binding(surface: str, entity_type: str | None) -> Binding:
    """MONEY, DATE, ACCOUNT_REF and TRANSACTION_TYPE canonicalize to themselves.

    They are values, not entities to be resolved across documents — merging two
    occurrences of "$4,000" into one entity would be meaningless.
    """
    return Binding(surface=surface, canonical=surface, entity_type=entity_type)


def _relation_bindings(
    plan: RelationPlan,
    rng: random.Random,
    *,
    received: datetime,
    doc_account: str,
    address: str,
) -> dict[str, Binding]:
    subject_type = "PERSON" if plan.relation in PERSON_SUBJECT_RELATIONS else "ORG"

    def surface(canonical: str) -> str:
        if plan.canonical_surfaces:
            return canonical
        return _surface_for(canonical, plan.band, rng)

    bindings: dict[str, Binding] = {
        "subject": Binding(
            surface=surface(plan.subject),
            canonical=plan.subject,
            entity_type=subject_type,
        ),
        "object": Binding(
            surface=surface(plan.object),
            canonical=plan.object,
            entity_type="ORG",
        ),
        "amount": _value_binding(_money(rng), "MONEY"),
        "date": _value_binding(_date(rng, received), "DATE"),
        "account": _value_binding(doc_account, "ACCOUNT_REF"),
        "txtype": _value_binding(rng.choice(names.TRANSACTION_TYPES), "TRANSACTION_TYPE"),
        # Addresses and day counts are substituted but not tagged: brief §15's
        # tag set has no ADDRESS label.
        "address": _value_binding(address, None),
        "days": _value_binding(str(rng.randint(3, 21)), None),
    }
    for key, value in plan.fixed:
        existing = bindings.get(key)
        if key in ("subject", "object") and existing is not None:
            # Pinning an argument's surface must not sever its link to the
            # canonical entity, or coreference and the graph would treat it as
            # a new company.
            bindings[key] = Binding(value, existing.canonical, existing.entity_type)
        else:
            bindings[key] = _value_binding(
                value, existing.entity_type if existing is not None else None
            )
    return bindings


def _filler_bindings(
    rng: random.Random, *, received: datetime, doc_account: str
) -> dict[str, Binding]:
    return {
        "account": _value_binding(doc_account, "ACCOUNT_REF"),
        "date": _value_binding(_date(rng, received), "DATE"),
        "txtype": _value_binding(rng.choice(names.TRANSACTION_TYPES), "TRANSACTION_TYPE"),
        "days": _value_binding(str(rng.choice((14, 30, 45, 60))), None),
        "amount": _value_binding(_money(rng), "MONEY"),
    }


# ── relation planning ────────────────────────────────────────────────────────


def _expand_generated(
    spec: ScenarioSpec, block: GeneratedRelations, rng: random.Random
) -> list[RelationPlan]:
    """Sample subject/object pairs for a bulk relation block."""
    if len(spec.orgs) < 2:
        raise ValueError(f"{spec.name}: need at least two orgs to sample relations")

    plans: list[RelationPlan] = []
    for _ in range(block.count):
        if block.relation in PERSON_SUBJECT_RELATIONS:
            subject, obj = rng.choice(spec.persons), rng.choice(spec.orgs)
        else:
            subject, obj = rng.sample(spec.orgs, 2)
        plans.append(
            RelationPlan(
                relation=block.relation,
                subject=subject,
                object=obj,
                band=block.band,
                routing=block.routing,
            )
        )
    return plans


def _assign_documents(
    spec: ScenarioSpec, plans: list[RelationPlan], rng: random.Random
) -> list[list[RelationPlan]]:
    """Distribute relations across documents, honouring pinned placements.

    Least-loaded-first rather than random, so every document ends up non-empty
    and the per-document relation count stays even. `invoice_flood` has to be
    120 comparable documents to say anything about latency, not three enormous
    ones and 117 empty.
    """
    buckets: list[list[RelationPlan]] = [[] for _ in range(spec.n_documents)]
    floating: list[RelationPlan] = []

    for plan in plans:
        if plan.document_index is None:
            floating.append(plan)
            continue
        if not 0 <= plan.document_index < spec.n_documents:
            raise ValueError(
                f"{spec.name}: relation pinned to document {plan.document_index}, "
                f"outside 0..{spec.n_documents - 1}"
            )
        buckets[plan.document_index].append(plan)

    rng.shuffle(floating)
    order = sorted(range(spec.n_documents), key=lambda i: len(buckets[i]))
    for position, plan in enumerate(floating):
        buckets[order[position % spec.n_documents]].append(plan)
    return buckets


# ── document assembly ────────────────────────────────────────────────────────


def _document_sources(spec: ScenarioSpec) -> list[str]:
    sources: list[str] = []
    for source, count in spec.source_mix:
        sources.extend([source] * count)
    return sources


def _primary_org(plans: list[RelationPlan], spec: ScenarioSpec) -> str:
    for plan in plans:
        if plan.relation not in PERSON_SUBJECT_RELATIONS:
            return plan.subject
    return spec.orgs[0]


def _short_name(canonical: str) -> str:
    """Drop the legal suffix for a title: "Meridian Supply LLC" ->
    "Meridian Supply", which is how a bookkeeper would name the file."""
    for suffix in (" LLC", " Inc", " Ltd", " Co", " Corp", " Limited", " LP"):
        if canonical.endswith(suffix):
            return canonical[: -len(suffix)]
    return canonical


def _slug(canonical: str) -> str:
    base = _short_name(canonical).casefold()
    return "".join(ch if ch.isalnum() else "-" for ch in base).strip("-")


def _title(source: str, account: str, primary_org: str) -> str:
    short = _short_name(primary_org)
    return {
        "invoice": f"{account} {short}",
        "email": f"Re: {account} — {short}",
        "press_release": f"{short} announcement",
        "rss": f"Trade Wire: {short}",
        "gdelt": f"GDELT: {short}",
        "transaction_log": f"Ledger export {account}",
    }.get(source, f"Note on {account}")


def _write_header(
    builder: _DocBuilder,
    *,
    source: str,
    account: str,
    primary_org: str,
    counterparty: str,
    received: datetime,
    address: str,
) -> None:
    """Write a realistic header for the document type.

    Headers are part of `raw_text`, so their entity mentions carry gold labels
    and the citation reader renders them. An uploaded `.eml` keeps its headers
    for the same reason (Stage 2).
    """
    stamp = received.strftime("%Y-%m-%d")
    if source == "invoice":
        builder.literal(f"Invoice {account}\n")
        builder.mention(primary_org, "ORG", primary_org)
        builder.literal(f"\n{address}\nIssued: {stamp}\nBill to: ")
        builder.mention(counterparty, "ORG", counterparty)
        builder.literal("\n\n")
    elif source == "email":
        builder.literal(
            f"From: accounts@{_slug(primary_org)}.example\n"
            f"To: ap@{_slug(counterparty)}.example\n"
            f"Date: {stamp}\nSubject: Reference {account}\n\n"
        )
    elif source == "press_release":
        builder.literal("FOR IMMEDIATE RELEASE\n")
        builder.mention(primary_org, "ORG", primary_org)
        builder.literal(f"\n{stamp}\n\n")
    elif source in ("rss", "gdelt"):
        feed = "Trade Wire" if source == "rss" else "GDELT Event Stream"
        builder.literal(f"[{feed}] ")
        builder.mention(primary_org, "ORG", primary_org)
        builder.literal(f" — filing activity noted {stamp}\n\n")
    elif source == "transaction_log":
        builder.literal(f"LEDGER EXPORT {account}\nPeriod ending {stamp}\n\n")
    else:
        builder.literal(f"Working note — {stamp}\nRe: {account}\n\n")


def _pick_template(plan: RelationPlan, spec: ScenarioSpec, rng: random.Random) -> Template:
    if plan.template_id is not None:
        try:
            return BY_ID[plan.template_id]
        except KeyError:
            raise LookupError(f"unknown template id {plan.template_id!r}") from None
    return rng.choice(templates_for(plan.relation, plan.band, split=spec.split))


def _build_document(
    spec: ScenarioSpec,
    *,
    index: int,
    source: str,
    plans: list[RelationPlan],
    rng: random.Random,
    org_id: uuid.UUID,
    pinned_accounts: dict[int, str],
) -> GoldDocument:
    received = CORPUS_EPOCH + timedelta(hours=6 * index, minutes=rng.randint(0, 59))
    account = pinned_accounts.get(index) or _account(rng)
    primary_org = _primary_org(plans, spec)
    counterparty = next(
        (p.object for p in plans if p.object != primary_org),
        spec.orgs[-1] if spec.orgs[-1] != primary_org else spec.orgs[0],
    )
    address = rng.choice(spec.addresses)

    builder = _DocBuilder.new()
    _write_header(
        builder,
        source=source,
        account=account,
        primary_org=primary_org,
        counterparty=counterparty,
        received=received,
        address=address,
    )

    # Interleave relation sentences with filler, so a relation is never simply
    # "the only sentence in the document" — which would let the relation
    # extractor cheat on position instead of learning syntax.
    #
    # Sampled WITHOUT replacement: `choice` in a loop produces the same filler
    # sentence twice in a row often enough to be visible, and a judge reading
    # the source document in the citation reader would spot the repeat
    # immediately. Capped at the pool size.
    filler_lo, filler_hi = spec.filler_range
    n_filler = min(rng.randint(filler_lo, filler_hi), len(FILLER))
    body: list[RelationPlan | Template] = [*plans, *rng.sample(FILLER, n_filler)]
    rng.shuffle(body)

    for position, item in enumerate(body):
        if position:
            # Mixed separators: some sentences share a line, some do not. The
            # reorder perturbation and the sentence splitter both need to cope
            # with real-looking layout rather than one-sentence-per-line.
            builder.literal(" " if rng.random() < 0.55 else "\n")

        if isinstance(item, Template):
            builder.sentence(
                item, _filler_bindings(rng, received=received, doc_account=account)
            )
            continue

        template = _pick_template(item, spec, rng)
        builder.sentence(
            template,
            _relation_bindings(
                item, rng, received=received, doc_account=account, address=address
            ),
            routing=item.routing,
            failure_note=item.failure_note,
            with_adjacent_context=item.with_adjacent_context,
        )

    builder.literal("\n")

    document = GoldDocument(
        index=index,
        document_id=ids.document_id(org_id, spec.name, index),
        source=source,
        title=_title(source, account, primary_org),
        raw_text=builder.text(),
        received_at=received,
        mentions=tuple(builder.mentions),
        relations=tuple(builder.relations),
    )
    # Fail at generation time, not when a judge clicks a citation.
    document.verify_offsets()
    return document


# ── entry point ──────────────────────────────────────────────────────────────


def generate(
    scenario: str, *, seed: int | None = None, org_id: uuid.UUID | None = None
) -> Manifest:
    """Generate a scenario's documents and its ground-truth manifest."""
    names.assert_pools_disjoint()

    spec = get_scenario(scenario)
    base_seed = seed if seed is not None else _default_seed()
    # Mix the scenario name into the seed so two scenarios built from one base
    # seed are not the same draw.
    rng = random.Random(f"{base_seed}:{spec.name}")  # noqa: S311 - synthetic data
    resolved_org = org_id or ids.DEMO_ORG_ID

    plans: list[RelationPlan] = list(spec.relations)
    for block in spec.generated:
        plans.extend(_expand_generated(spec, block, rng))

    buckets = _assign_documents(spec, plans, rng)
    sources = _document_sources(spec)
    pinned = dict(spec.pinned_accounts)

    documents = tuple(
        _build_document(
            spec,
            index=index,
            source=sources[index],
            plans=buckets[index],
            rng=rng,
            org_id=resolved_org,
            pinned_accounts=pinned,
        )
        for index in range(spec.n_documents)
    )

    manifest = Manifest(
        scenario=spec.name,
        split=spec.split,
        seed=base_seed,
        org_id=resolved_org,
        documents=documents,
    )
    manifest.verify()
    return manifest


def report(manifest: Manifest) -> str:
    mix = manifest.band_mix()
    return "\n".join(
        (
            f"scenario       {manifest.scenario} ({manifest.split} split, seed {manifest.seed})",
            f"documents      {len(manifest.documents)}",
            f"mentions       {len(manifest.mentions)}",
            f"relations      {len(manifest.relations)}",
            f"entities       {len(manifest.entities)} "
            f"({len(manifest.named_entities())} org/person, rest are money/date/ref values)",
            "band mix       "
            + "  ".join(f"{b}={mix.get(b, 0.0):.0%}" for b in ("A", "B", "C", "D")),
            "routing truth  "
            + "  ".join(f"{k}={v}" for k, v in sorted(manifest.routing_counts().items())),
            f"planted fails  {len(manifest.planted_failures())}",
            f"total chars    {sum(len(d.raw_text) for d in manifest.documents):,}",
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a synthetic scenario.")
    parser.add_argument("--scenario", default="meridian_shell_ring")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--report", action="store_true", help="Print a summary.")
    parser.add_argument("--write", action="store_true", help="Write the manifest to disk.")
    parser.add_argument("--sample", type=int, default=0, help="Print N documents in full.")
    args = parser.parse_args()

    manifest = generate(args.scenario, seed=args.seed)

    if args.report:
        print(report(manifest))

    for document in manifest.documents[: args.sample]:
        print(f"\n{'=' * 74}\n{document.title}   [{document.source}]\n{'=' * 74}")
        print(document.raw_text)
        for relation in document.relations:
            marker = "  <-- PLANTED FAILURE" if relation.is_planted_failure else ""
            print(
                f"  {relation.relation:<20} {relation.subject_canonical} -> "
                f"{relation.object_canonical}  "
                f"[band {relation.band}, truth={relation.routing}] "
                f"@{relation.char_start}:{relation.char_end}{marker}"
            )

    if args.write:
        from core.config import get_settings

        path = manifest.write(get_settings().path("data/generated"))
        print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
