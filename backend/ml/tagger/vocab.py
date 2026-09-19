"""Word and character vocabularies, built once from the training split.

Two properties are load-bearing.

**It is built from `train_corpus` only.** Every held-out name (plan §0 band D)
therefore hits `<unk>` at the word level and is carried entirely by the
character CNN. That is not a limitation to work around — it is the mechanism
that gives the evidential head something to be uncertain *about*. A vocabulary
built over the demo scenario would leak held-out names into the model and
flatten vacuity, which is the one failure the plan says has no fix at hour 13.

**It is ordered deterministically.** Frequency descending, then alphabetically,
so two machines building from the same corpus produce the same integer ids and
a checkpoint from one loads correctly against the other.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PAD = "<pad>"
UNK = "<unk>"

# Below this count a word is more useful as an <unk> training signal than as
# its own embedding: the model needs to see <unk> often enough to learn that
# the character CNN carries the load when the word id is uninformative.
MIN_WORD_COUNT = 2

# Shape features, appended to the character alphabet so casing and digit
# patterns survive the lowercase word lookup.
DIGIT = "<digit>"


@dataclass(frozen=True, slots=True)
class Vocab:
    words: tuple[str, ...]
    chars: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.words[:2] != (PAD, UNK) or self.chars[:2] != (PAD, UNK):
            raise AssertionError("vocabularies must start with <pad>, <unk>")

    @property
    def word_index(self) -> dict[str, int]:
        return {word: index for index, word in enumerate(self.words)}

    @property
    def char_index(self) -> dict[str, int]:
        return {char: index for index, char in enumerate(self.chars)}

    @property
    def n_words(self) -> int:
        return len(self.words)

    @property
    def n_chars(self) -> int:
        return len(self.chars)

    def to_dict(self) -> dict[str, Any]:
        return {"words": list(self.words), "chars": list(self.chars)}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Vocab:
        return cls(words=tuple(payload["words"]), chars=tuple(payload["chars"]))

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")

    @classmethod
    def read(cls, path: Path) -> Vocab:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


def normalize_word(surface: str) -> str:
    """Lowercase, with digits collapsed to a single placeholder character.

    `$48,200.00` and `$7,015.33` become the same word type, which is correct:
    the *identity* of an amount carries no signal about whether the token is
    MONEY, and keeping them distinct would fill the vocabulary with hapaxes.
    The character CNN still sees the real digits.
    """
    return "".join("0" if char.isdigit() else char for char in surface.casefold())


def build_vocab(
    sentences: list[list[str]],
    *,
    min_word_count: int = MIN_WORD_COUNT,
    max_words: int = 20_000,
) -> Vocab:
    """Build from tokenized sentences of the training split.

    `max_words` caps the embedding table, which is the bulk of the checkpoint
    (plan §1.7 budgets the tagger at 25 MB). The synthetic corpus does not come
    close to the cap; it is there so a larger corpus cannot silently produce a
    checkpoint too big to commit.
    """
    word_counts: Counter[str] = Counter()
    char_counts: Counter[str] = Counter()

    for sentence in sentences:
        for surface in sentence:
            word_counts[normalize_word(surface)] += 1
            for char in surface:
                char_counts[DIGIT if char.isdigit() else char] += 1

    def ordered(counts: Counter[str], minimum: int, limit: int) -> tuple[str, ...]:
        kept = [(item, n) for item, n in counts.items() if n >= minimum]
        kept.sort(key=lambda pair: (-pair[1], pair[0]))
        return tuple(item for item, _ in kept[:limit])

    return Vocab(
        words=(PAD, UNK, *ordered(word_counts, min_word_count, max_words)),
        chars=(PAD, UNK, *ordered(char_counts, 1, 512)),
    )


def encode_words(surfaces: list[str], word_index: dict[str, int]) -> list[int]:
    unk = word_index[UNK]
    return [word_index.get(normalize_word(s), unk) for s in surfaces]


def encode_chars(
    surfaces: list[str], char_index: dict[str, int], *, max_len: int
) -> list[list[int]]:
    """Fixed-width character ids per token, right-padded.

    Truncation is centered rather than tail-clipped: prefixes and suffixes are
    where morphology lives (`Ltd`, `$`, `-X`), and a 20-character window that
    keeps both ends beats one that keeps only the start.
    """
    unk = char_index[UNK]
    encoded: list[list[int]] = []
    for surface in surfaces:
        chars = [DIGIT if c.isdigit() else c for c in surface]
        if len(chars) > max_len:
            head = max_len // 2
            tail = max_len - head
            chars = chars[:head] + chars[-tail:]
        ids = [char_index.get(c, unk) for c in chars]
        ids.extend([0] * (max_len - len(ids)))
        encoded.append(ids)
    return encoded
