# Fonts

Self-hosted, subsetted `woff2` only. Do not add a Google Fonts `<link>` —
it blocks render and the hero sequence has to paint immediately.

Drop these three files here:

- `InstrumentSans-Variable.woff2` — UI and display. Not Inter; Inter is the
  default tell and half the projects at this hackathon will use it.
- `Literata-Variable.woff2` — the paper reader body only. Its presence is what
  makes the document surface feel like a document rather than a div.
- `JetBrainsMono-Regular.woff2` — machine identifiers only: byte offsets,
  UUIDs, request IDs, SHA prefixes, temperature values. Never labels or
  captions or "technical flavour."

Subset with:

    npx glyphhanger --subset=*.woff2 --formats=woff2 --US_ASCII
