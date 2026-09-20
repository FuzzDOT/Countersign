"""Entity rename. Brief §15, family 2 of 5 — and the load-bearing one.

Every mention of the claim's two parties is replaced with a company name
that appears nowhere in any corpus. Nothing else changes: same syntax, same
verbs, same amounts, same document.

This is the family that separates **structure from memorization**. A model
that has learned "the thing before `wired` is a payer" is unaffected. A model
that has learned "Meridian Supply is a payer" loses the relation entirely. It
is the perturbation I expect to hurt most, and the one whose result says the
most about whether any of the rest of this is real.

The replacement pool is `names.FUZZ_*`, disjoint from both the training and
the held-out pools. Renaming into the held-out pool would sometimes pick a
company already in the scenario, which collides two entities and scores a
coreference merge as a relation loss.
"""

from __future__ import annotations

import random

from data.synth import names
from ml.entities.coref import entity_key
from ml.fuzzer.base import Edit, Variant, apply_edits, unchanged
from ml.tagger.infer import DocumentTagging
from ml.text.tokenize import TokenizedDoc

NAME = "rename"

RENAMEABLE = frozenset({"ORG", "PERSON"})


class RenameFamily:
    name = NAME

    def build(
        self,
        doc: TokenizedDoc,
        tagging: DocumentTagging,
        target: tuple[int, int],
        rng: random.Random,
    ) -> Variant:
        mentions = [m for m in tagging.mentions if m.entity_type in RENAMEABLE]
        if not mentions:
            return unchanged(doc, target, NAME)

        # One replacement per *entity*, not per mention: renaming the same
        # company differently in two sentences would fragment it under
        # coreference and score a broken entity as a broken relation.
        org_pool = list(names.FUZZ_ORGS)
        person_pool = list(names.FUZZ_PERSONS)
        rng.shuffle(org_pool)
        rng.shuffle(person_pool)

        replacement: dict[str, str] = {}
        renames: dict[str, str] = {}

        for mention in mentions:
            key = entity_key(mention.surface, mention.entity_type)
            if key in replacement:
                continue
            pool = person_pool if mention.entity_type == "PERSON" else org_pool
            if not pool:
                continue
            new_name = pool.pop()
            replacement[key] = new_name
            renames[entity_key(new_name, mention.entity_type)] = key

        if not replacement:
            return unchanged(doc, target, NAME)

        edits = [
            Edit(
                start=mention.char_start,
                end=mention.char_end,
                replacement=replacement[entity_key(mention.surface, mention.entity_type)],
            )
            for mention in mentions
            if entity_key(mention.surface, mention.entity_type) in replacement
        ]
        return apply_edits(doc.text, edits, target, family=NAME, renames=renames)
