# Frontend dev 2: handoff notes

Scope built: feed and filters, detail sheet, attention arcs and ablation panel, graph canvas and table view,
entity panel, paper reader, evidence dashboards (fragility, routing, calibration and recalibration), voice console.

## Not yet machine-verified
Written without network access, so `npm ci`, `tsc`, `eslint`, `vitest` and `vite build` have NOT been run against this tree.
First thing to run: `npm ci && npm run typecheck && npm run lint && npm test && npm run build`.
Expect a few small type or lint fixes. `src/api/types.ts` is hand-mirrored from `backend/api/v1/schemas.py`;
run `npm run gen:types` against the live API and diff it.

## Edits to Frontend dev 1's files (small, easy to re-apply)
- `App.tsx`, `main.tsx`: routes now follow the brief's map (`/app/*`), lazy-load dev 2 screens, old `/feed` style paths redirect.
- `components/shell/RailNav.tsx`: links use `/app/*`. `TopBar` search goes to `/app/feed?q=`.
- `components/shell/AppShell.tsx`: `<main>` is ink (it was paper; only the reader flips to paper), screens scroll themselves,
  and the fallback briefing is preloaded on mount.
- `components/shell/RouteTransition.tsx`: wrapper is `h-full`.
- `hooks/useFeedStats.ts`: reads the shared authenticated query instead of a raw fetch.
- `lib/cookies.ts`: fixed a strict-mode error (`match[1]` may be undefined).
- `tailwind.config.ts`: colours are functions so `border-ink-500/40` style opacity utilities exist (Tailwind 3 drops them for bare `var()` colours).
- `routes/dev1-placeholders/DevLogin.tsx` is a bare stand-in for the real login and register screens.

## Shared layer I added (dev 1 may extend)
`src/api/*` (fetch client with refresh handling, typed endpoints, query hooks), `src/auth/*`, `src/components/primitives/*`,
`src/lib/*`, `src/hooks/*`. Ingest and Settings pages are still dev 1's stubs.
