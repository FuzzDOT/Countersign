"""spaCy parsing, behind a content-addressed cache.

Two reasons this is its own module rather than three lines inside the
pipeline.

**Cost.** The fuzzer (Stage 5) re-parses 1,070 perturbed variants of sentences
that are 85% identical to each other. Brief §15 is right that a parse cache is
the difference between a three-minute and a forty-minute run, and it has to
exist before Stage 5 rather than be retrofitted into it.

**Offsets.** Everything that converts a spaCy `Doc` into our coordinate system
goes through `ml.text.tokenize.build_tokenized_doc`, which verifies itself. The
cache stores `TokenizedDoc.as_dict()` — token offsets, not text — and rehydrates
against the caller's string, so a cache hit cannot return offsets computed
against a different document. The key includes the spaCy model version, because
a model upgrade changes tokenization and a stale entry would then be wrong
rather than merely old.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.config import get_settings
from core.logging import get_logger
from ml.text.tokenize import TokenizedDoc, build_tokenized_doc

log = get_logger(__name__)

SPACY_MODEL = "en_core_web_sm"

# spaCy's own NER is disabled: entity spans come from our BiLSTM-CRF, and
# running a second tagger we then ignore is pure latency. The parser and the
# POS tagger stay — the sentence graph in Stage 3 is built from dependency
# arcs, so they are load-bearing rather than decorative.
DISABLED_PIPES = ("ner",)

# In-process LRU on top of the disk cache. The disk layer survives a restart;
# this one avoids a JSON decode per sentence during a fuzzer run.
MEMORY_CACHE_SIZE = 4_096


@lru_cache(maxsize=1)
def get_nlp() -> Any:
    """Load spaCy once per process.

    Cached rather than loaded at import: `python -m data.synth.generate` and
    the fixture generator both import this package transitively and neither
    needs a 40 MB model resident.
    """
    import spacy

    nlp = spacy.load(SPACY_MODEL, disable=list(DISABLED_PIPES))
    log.info("spacy_loaded", model=SPACY_MODEL, version=nlp.meta.get("version"))
    return nlp


@lru_cache(maxsize=1)
def _cache_namespace() -> str:
    """Model identity, mixed into every cache key.

    Without it, upgrading en_core_web_sm would silently serve tokenization
    from the previous model — offsets that verify against the text but
    disagree with what the tagger was trained on.
    """
    nlp = get_nlp()
    return f"{SPACY_MODEL}-{nlp.meta.get('version', '0')}-{'+'.join(DISABLED_PIPES)}"


def content_sha(text: str) -> str:
    """sha256 of the exact bytes. Also used for `documents.content_sha`."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cache_key(text: str) -> str:
    return hashlib.sha256(f"{_cache_namespace()}\0{text}".encode()).hexdigest()


class _ParseCache:
    """Two-tier cache: bounded dict in front of one JSON file per parse.

    Sharded two hex digits deep, because a single directory with 40k entries
    is measurably slower to stat on the overlay filesystem in the container.
    """

    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._memory: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def _path(self, key: str) -> Path:
        return self._dir / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            payload = self._memory.get(key)
            if payload is not None:
                self._memory.move_to_end(key)
                self.hits += 1
                return payload

        path = self._path(key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            # A truncated entry (killed mid-write) is a miss, not a crash.
            with self._lock:
                self.misses += 1
            return None

        self._remember(key, payload)
        with self._lock:
            self.hits += 1
        return payload

    def put(self, key: str, payload: dict[str, Any]) -> None:
        self._remember(key, payload)
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write-then-rename: a reader never sees a half-written file, which
            # is what makes the JSONDecodeError branch above rare rather than
            # routine.
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:  # pragma: no cover - read-only volume
            log.warning("parse_cache_write_failed", error=str(exc))

    def _remember(self, key: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._memory[key] = payload
            self._memory.move_to_end(key)
            while len(self._memory) > MEMORY_CACHE_SIZE:
                self._memory.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._memory.clear()
            self.hits = 0
            self.misses = 0


@lru_cache(maxsize=1)
def _cache() -> _ParseCache:
    return _ParseCache(get_settings().path(get_settings().parse_cache_dir))


def parse(text: str, *, use_cache: bool = True) -> TokenizedDoc:
    """Tokenize and dependency-parse one string.

    The returned `TokenizedDoc.text` is the argument, byte for byte. Callers
    pass `documents.raw_text` and nothing else.
    """
    if not use_cache:
        return build_tokenized_doc(text, get_nlp()(text))

    key = _cache_key(text)
    cached = _cache().get(key)
    if cached is not None:
        doc = TokenizedDoc.from_dict(text, cached)
        # Cheap, and it turns "the cache disagrees with the text" from a
        # mystery citation bug into an immediate loud failure.
        doc.verify()
        return doc

    doc = build_tokenized_doc(text, get_nlp()(text))
    _cache().put(key, doc.as_dict())
    return doc


def parse_many(texts: list[str], *, use_cache: bool = True) -> list[TokenizedDoc]:
    """Batch parse, using `nlp.pipe` for the entries that miss the cache.

    Order is preserved. Worth the bookkeeping: `pipe` is roughly 3x faster than
    a loop over `nlp()` on a batch of 400 training documents.
    """
    results: list[TokenizedDoc | None] = [None] * len(texts)
    pending: list[tuple[int, str, str]] = []

    for index, text in enumerate(texts):
        if use_cache:
            key = _cache_key(text)
            cached = _cache().get(key)
            if cached is not None:
                doc = TokenizedDoc.from_dict(text, cached)
                doc.verify()
                results[index] = doc
                continue
            pending.append((index, key, text))
        else:
            pending.append((index, "", text))

    if pending:
        nlp = get_nlp()
        for (index, key, text), spacy_doc in zip(
            pending, nlp.pipe([t for _, _, t in pending]), strict=True
        ):
            doc = build_tokenized_doc(text, spacy_doc)
            if use_cache:
                _cache().put(key, doc.as_dict())
            results[index] = doc

    return [doc for doc in results if doc is not None]


def cache_stats() -> dict[str, int]:
    cache = _cache()
    return {"hits": cache.hits, "misses": cache.misses}


def clear_memory_cache() -> None:
    """Used by tests. Does not touch the on-disk layer."""
    _cache().clear()
