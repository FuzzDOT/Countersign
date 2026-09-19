"""Entity embeddings: hashed character 3-gram TF-IDF, 256 dims, L2-normalized.

Plan §1.5. `entities.embedding VECTOR(256)` needs a definition and the brief
does not give one; since its stated job is coreference and dedupe by string
similarity, this is a classical vectorizer rather than a learned one.

It is honest about what it does: **it merges surface forms, it does not
understand companies.** `Meridian Supply LLC` and `Meridian Supply, LLC` come
out at cosine ~0.99 because they share almost every trigram. Two genuinely
distinct companies with similar names would merge too, and the API returns the
`aliases` list precisely so a judge can see exactly what got merged rather than
taking our word for it (brief §13 already lists this as known debt).

Three choices worth stating:

- **Hashing, not a fitted vocabulary.** No fit step, no vocabulary artifact to
  keep in sync with a checkpoint, and a name seen for the first time at hour
  22 vectorizes identically to one seen at hour 3.
- **Signed hashing.** Two trigrams colliding on the same bucket cancel on
  average instead of reinforcing, which is the standard correction for the
  bias plain hashing introduces at 256 dimensions.
- **IDF from the training name pool**, a fixed background collection. Trigrams
  like `ltd`, `llc` and ` co` are common across company names and carry almost
  no identity, so down-weighting them is what stops two unrelated limited
  companies from looking similar. The held-out pool is deliberately not in the
  background collection — nothing in the serving path should be parameterized
  by names the model is supposed to find unfamiliar.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from functools import lru_cache

import numpy as np

EMBEDDING_DIM = 256
NGRAM = 3

# Stripped before vectorizing: they are the least informative part of a
# company name and the part most likely to differ between two surface forms
# of the same entity.
LEGAL_SUFFIXES = (
    "llc",
    "l.l.c",
    "inc",
    "incorporated",
    "ltd",
    "limited",
    "corp",
    "corporation",
    "co",
    "company",
    "lp",
    "llp",
    "plc",
    "gmbh",
    "sa",
    "nv",
    "bv",
)

_PUNCTUATION = re.compile(r"[^0-9a-z& ]+")
_WHITESPACE = re.compile(r"\s+")

# Pad the name so the first and last characters get full trigram context;
# without it `Advent` and `Advent Holdings` differ more at the boundaries than
# they should.
BOUNDARY = " "


def normalize_name(surface: str) -> str:
    """Case-fold, drop legal suffixes, collapse punctuation and whitespace.

    Comparison-only, like every other normalizer in this codebase: the result
    is never used to compute a character offset (plan §3). `entities.canonical`
    stores the original surface, not this.
    """
    lowered = surface.casefold()
    cleaned = _PUNCTUATION.sub(" ", lowered)
    words = [w for w in _WHITESPACE.split(cleaned) if w]
    while words and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def trigrams(text: str) -> list[str]:
    padded = f"{BOUNDARY}{text}{BOUNDARY}"
    if len(padded) < NGRAM:
        return [padded]
    return [padded[i : i + NGRAM] for i in range(len(padded) - NGRAM + 1)]


@lru_cache(maxsize=65_536)
def _bucket_and_sign(trigram: str) -> tuple[int, float]:
    digest = hashlib.blake2b(trigram.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % EMBEDDING_DIM, 1.0 if (value >> 63) & 1 else -1.0


@lru_cache(maxsize=1)
def _idf() -> dict[str, float]:
    """Inverse document frequency over the training name pool.

    Smoothed, and capped at the unseen-trigram value so a novel trigram is
    treated as maximally informative rather than as an error.
    """
    from data.synth import names

    collection = [normalize_name(name) for name in (*names.TRAIN_ORGS, *names.TRAIN_PERSONS)]
    document_frequency: Counter[str] = Counter()
    for entry in collection:
        for trigram in set(trigrams(entry)):
            document_frequency[trigram] += 1

    total = len(collection)
    return {
        trigram: math.log((1 + total) / (1 + count)) + 1.0
        for trigram, count in document_frequency.items()
    }


def _unseen_idf() -> float:
    from data.synth import names

    total = len(names.TRAIN_ORGS) + len(names.TRAIN_PERSONS)
    return math.log((1 + total) / 1) + 1.0


def embed(surface: str) -> np.ndarray:
    """A 256-dim unit vector for one name. Deterministic across processes."""
    normalized = normalize_name(surface) or surface.casefold()
    counts: Counter[str] = Counter(trigrams(normalized))
    idf = _idf()
    unseen = _unseen_idf()

    vector = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    for trigram, count in counts.items():
        bucket, sign = _bucket_and_sign(trigram)
        # Sublinear TF: a trigram appearing three times in one name is not
        # three times as characteristic of it.
        weight = (1.0 + math.log(count)) * idf.get(trigram, unseen)
        vector[bucket] += sign * weight

    norm = float(np.linalg.norm(vector))
    if norm == 0.0:  # pragma: no cover - only for an empty name
        return vector
    return vector / norm


def cosine(left: np.ndarray, right: np.ndarray) -> float:
    """Dot product. Both arguments are already unit vectors."""
    return float(np.dot(left, right))


def embed_many(surfaces: list[str]) -> np.ndarray:
    """[N, 256] matrix, rows in the order given."""
    if not surfaces:
        return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
    return np.vstack([embed(surface) for surface in surfaces])
