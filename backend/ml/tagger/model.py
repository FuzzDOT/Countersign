"""BiLSTM-CRF entity tagger. Brief §15.

Character CNN + learned 100d word embeddings -> 2x256 BiLSTM -> linear
emissions -> CRF. Dropout 0.5, per the brief.

Two deviations from the brief's ML spec, both deliberate and both in
docs/03-BACKEND-PLAN.md:

- **No GloVe init** (§1.4). The corpus is synthetic with a closed vocabulary;
  pretrained vectors buy generalization to real-world words we do not have,
  at the cost of a 350 MB artifact inside the image. The character CNN already
  covers the morphology of the held-out names, which are the tokens that
  actually matter.
- The CRF is hand-written rather than `torchcrf`. It is 120 lines, it removes
  a dependency, and — the real reason — we need **per-token marginals**, not
  just the Viterbi path. `mentions.tagger_conf` is a number the UI shows a
  judge, so it has to be a calibrated posterior from forward-backward rather
  than a softmax over emissions that ignores the transition structure.

The BiLSTM hidden states are returned alongside the emissions because Stage 3
builds its sentence-graph node features from them (`[tagger hidden ||
entity-type one-hot || POS one-hot || relative position]`). Recomputing them
there would double the forward passes per document.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import torch
from torch import Tensor, nn

from ml.tagger.labels import N_TAGS

# Large negative, not -inf: -inf produces NaN the moment it meets a 0 weight
# in a gradient, and the difference is invisible until a loss goes to nan at
# epoch 6.
NEG_INF = -1.0e4


@dataclass(frozen=True, slots=True)
class TaggerConfig:
    n_words: int
    n_chars: int
    n_tags: int = N_TAGS
    word_dim: int = 100
    char_dim: int = 32
    char_channels: int = 64
    char_kernel: int = 3
    max_word_len: int = 20
    lstm_hidden: int = 256
    lstm_layers: int = 1
    dropout: float = 0.5

    @property
    def encoder_dim(self) -> int:
        return self.lstm_hidden * 2

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TaggerConfig:
        fields = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in payload.items() if k in fields})


class CharCNN(nn.Module):
    """Character-level features per token, max-pooled over the window.

    This is what carries a held-out company name. `Ozimandias Crate Works LLC`
    is `<unk> <unk> <unk> LLC` at the word level; the CNN still sees the
    capitalization pattern and the legal suffix.
    """

    def __init__(self, config: TaggerConfig) -> None:
        super().__init__()
        self.embedding = nn.Embedding(config.n_chars, config.char_dim, padding_idx=0)
        self.conv = nn.Conv1d(
            config.char_dim,
            config.char_channels,
            kernel_size=config.char_kernel,
            padding=config.char_kernel // 2,
        )
        self.dropout = nn.Dropout(config.dropout)

    def forward(self, char_ids: Tensor) -> Tensor:
        """char_ids: [B, T, L] -> [B, T, char_channels]"""
        batch, length, word_len = char_ids.shape
        flat = char_ids.reshape(batch * length, word_len)
        embedded = self.dropout(self.embedding(flat)).transpose(1, 2)
        pooled = torch.relu(self.conv(embedded)).max(dim=2).values
        return pooled.reshape(batch, length, -1)


class CRF(nn.Module):
    """Linear-chain CRF over BIO tags.

    `transitions[i, j]` scores a move from tag i to tag j. Start and end
    scores are separate vectors rather than two extra rows in the matrix,
    which keeps the tag indices identical to `labels.TAGS` — a checkpoint and
    a decode cannot disagree about what index 7 means.
    """

    def __init__(self, n_tags: int) -> None:
        super().__init__()
        self.n_tags = n_tags
        self.transitions = nn.Parameter(torch.empty(n_tags, n_tags))
        self.start_transitions = nn.Parameter(torch.empty(n_tags))
        self.end_transitions = nn.Parameter(torch.empty(n_tags))
        nn.init.uniform_(self.transitions, -0.1, 0.1)
        nn.init.uniform_(self.start_transitions, -0.1, 0.1)
        nn.init.uniform_(self.end_transitions, -0.1, 0.1)

    # ── partition function ───────────────────────────────────────────────────

    def _forward_alphas(self, emissions: Tensor, mask: Tensor) -> Tensor:
        """Per-position forward scores. [B, T, K]

        Padded positions carry the last real alpha forward unchanged, so the
        final logsumexp reads the right vector for every sequence regardless
        of length.
        """
        batch, length, _ = emissions.shape
        alphas = emissions.new_empty(batch, length, self.n_tags)
        alpha = self.start_transitions.unsqueeze(0) + emissions[:, 0]
        alphas[:, 0] = alpha

        for step in range(1, length):
            candidate = (
                alpha.unsqueeze(2) + self.transitions.unsqueeze(0) + emissions[:, step].unsqueeze(1)
            )
            advanced = torch.logsumexp(candidate, dim=1)
            keep = mask[:, step].unsqueeze(1)
            alpha = torch.where(keep, advanced, alpha)
            alphas[:, step] = alpha

        return alphas

    def _backward_betas(self, emissions: Tensor, mask: Tensor) -> Tensor:
        """Per-position backward scores, end transition included. [B, T, K]"""
        batch, length, _ = emissions.shape
        betas = emissions.new_empty(batch, length, self.n_tags)
        beta = self.end_transitions.unsqueeze(0).expand(batch, self.n_tags).clone()
        betas[:, length - 1] = beta

        for step in range(length - 2, -1, -1):
            candidate = (
                self.transitions.unsqueeze(0)
                + emissions[:, step + 1].unsqueeze(1)
                + beta.unsqueeze(1)
            )
            retreated = torch.logsumexp(candidate, dim=2)
            # `mask[:, step + 1]` false means step is the last real token, so
            # beta stays at the end transition — which is exactly right.
            keep = mask[:, step + 1].unsqueeze(1)
            beta = torch.where(keep, retreated, beta)
            betas[:, step] = beta

        return betas

    def log_partition(self, emissions: Tensor, mask: Tensor) -> Tensor:
        alphas = self._forward_alphas(emissions, mask)
        lengths = mask.sum(dim=1) - 1
        final = alphas[torch.arange(alphas.size(0), device=alphas.device), lengths]
        return torch.logsumexp(final + self.end_transitions.unsqueeze(0), dim=1)

    def score(self, emissions: Tensor, tags: Tensor, mask: Tensor) -> Tensor:
        """Unnormalized score of a given tag sequence. [B]"""
        batch, length, _ = emissions.shape
        floats = mask.to(emissions.dtype)

        total = self.start_transitions[tags[:, 0]] + emissions[:, 0].gather(
            1, tags[:, 0].unsqueeze(1)
        ).squeeze(1)

        for step in range(1, length):
            transition = self.transitions[tags[:, step - 1], tags[:, step]]
            emission = emissions[:, step].gather(1, tags[:, step].unsqueeze(1)).squeeze(1)
            total = total + (transition + emission) * floats[:, step]

        lengths = mask.sum(dim=1) - 1
        last_tags = tags[torch.arange(batch, device=tags.device), lengths]
        return total + self.end_transitions[last_tags]

    def nll(self, emissions: Tensor, tags: Tensor, mask: Tensor) -> Tensor:
        """Mean negative log-likelihood per sequence."""
        return (self.log_partition(emissions, mask) - self.score(emissions, tags, mask)).mean()

    # ── decoding ─────────────────────────────────────────────────────────────

    def marginals(self, emissions: Tensor, mask: Tensor) -> Tensor:
        """Per-token posterior over tags. [B, T, K]

        This is what `mentions.tagger_conf` is computed from. A softmax over
        raw emissions would ignore the transition structure and report high
        confidence on sequences the CRF would never decode.
        """
        alphas = self._forward_alphas(emissions, mask)
        betas = self._backward_betas(emissions, mask)
        return torch.softmax(alphas + betas, dim=2)

    def viterbi(self, emissions: Tensor, mask: Tensor) -> list[list[int]]:
        batch, length, _ = emissions.shape
        score = self.start_transitions.unsqueeze(0) + emissions[:, 0]
        history: list[Tensor] = []

        for step in range(1, length):
            candidate = score.unsqueeze(2) + self.transitions.unsqueeze(0)
            best, best_index = candidate.max(dim=1)
            advanced = best + emissions[:, step]
            keep = mask[:, step].unsqueeze(1)
            score = torch.where(keep, advanced, score)
            history.append(best_index)

        score = score + self.end_transitions.unsqueeze(0)
        lengths = mask.sum(dim=1).tolist()
        best_last = score.argmax(dim=1).tolist()

        paths: list[list[int]] = []
        for index in range(batch):
            seq_len = int(lengths[index])
            tag = best_last[index]
            path = [tag]
            # history[t] holds the best predecessor for position t+1.
            for step in range(seq_len - 2, -1, -1):
                tag = int(history[step][index][tag])
                path.append(tag)
            paths.append(list(reversed(path)))
        return paths


class BiLSTMCRFTagger(nn.Module):
    """The whole tagger. Emissions and encoder states in one forward pass."""

    def __init__(self, config: TaggerConfig) -> None:
        super().__init__()
        self.config = config
        self.word_embedding = nn.Embedding(config.n_words, config.word_dim, padding_idx=0)
        self.char_cnn = CharCNN(config)
        self.dropout = nn.Dropout(config.dropout)
        self.lstm = nn.LSTM(
            config.word_dim + config.char_channels,
            config.lstm_hidden,
            num_layers=config.lstm_layers,
            bidirectional=True,
            batch_first=True,
            dropout=config.dropout if config.lstm_layers > 1 else 0.0,
        )
        self.emission = nn.Linear(config.encoder_dim, config.n_tags)
        self.crf = CRF(config.n_tags)

        nn.init.uniform_(self.word_embedding.weight, -0.1, 0.1)
        with torch.no_grad():
            self.word_embedding.weight[0].fill_(0.0)

    def encode(self, word_ids: Tensor, char_ids: Tensor, mask: Tensor) -> Tensor:
        """BiLSTM hidden states. [B, T, 2*hidden]

        Public because Stage 3's graph builder consumes these as node
        features. Packed so padding cannot leak into the recurrence — with a
        plain forward pass the backward direction starts on pad tokens and the
        first real token's representation depends on how long the batch's
        longest sentence happened to be.
        """
        features = torch.cat(
            [self.word_embedding(word_ids), self.char_cnn(char_ids)],
            dim=2,
        )
        features = self.dropout(features)

        lengths = mask.sum(dim=1).clamp(min=1).cpu()
        packed = nn.utils.rnn.pack_padded_sequence(
            features, lengths, batch_first=True, enforce_sorted=False
        )
        output, _ = self.lstm(packed)
        hidden, _ = nn.utils.rnn.pad_packed_sequence(
            output, batch_first=True, total_length=word_ids.size(1)
        )
        return self.dropout(hidden)

    def forward(self, word_ids: Tensor, char_ids: Tensor, mask: Tensor) -> tuple[Tensor, Tensor]:
        hidden = self.encode(word_ids, char_ids, mask)
        emissions = self.emission(hidden)
        # Padded positions must not win the argmax in Viterbi's final step.
        emissions = emissions.masked_fill(~mask.unsqueeze(2), NEG_INF)
        return emissions, hidden

    def loss(self, word_ids: Tensor, char_ids: Tensor, mask: Tensor, tags: Tensor) -> Tensor:
        emissions, _ = self.forward(word_ids, char_ids, mask)
        return self.crf.nll(emissions, tags, mask)

    @torch.no_grad()
    def decode(
        self, word_ids: Tensor, char_ids: Tensor, mask: Tensor
    ) -> tuple[list[list[int]], Tensor, Tensor]:
        """Viterbi paths, per-token marginals, and the encoder states."""
        self.eval()
        emissions, hidden = self.forward(word_ids, char_ids, mask)
        return self.crf.viterbi(emissions, mask), self.crf.marginals(emissions, mask), hidden


# ── checkpoint I/O ───────────────────────────────────────────────────────────


def save_checkpoint(
    path: Path,
    model: BiLSTMCRFTagger,
    vocab_payload: dict[str, Any],
    metrics: dict[str, Any],
) -> None:
    """Weights, config, vocabulary and the dev metrics, in one file.

    The vocabulary travels with the weights on purpose: a checkpoint loaded
    against a vocabulary rebuilt from a slightly different corpus would map
    every word id to the wrong embedding and degrade silently rather than
    fail. `metrics` rides along so `/health`-style introspection can report
    the F1 the running model was actually trained to.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "format": 1,
            "config": model.config.to_dict(),
            "state_dict": model.state_dict(),
            "vocab": vocab_payload,
            "metrics": metrics,
        },
        path,
    )


def load_checkpoint(path: Path) -> tuple[BiLSTMCRFTagger, dict[str, Any], dict[str, Any]]:
    payload = torch.load(path, map_location="cpu", weights_only=False)
    config = TaggerConfig.from_dict(payload["config"])
    model = BiLSTMCRFTagger(config)
    model.load_state_dict(payload["state_dict"])
    model.eval()
    return model, payload["vocab"], payload.get("metrics", {})
