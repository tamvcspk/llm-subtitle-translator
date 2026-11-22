---
name: frontend-dev
description: Rules for changing the React console in apps/web - generated API types, typed query strings, settings.ts, draft.ts, the Estimate/Confirm dialog, touch and portrait rules, Vitest on Windows. Use when adding or changing a screen, component, query or setting form in apps/web, or when the user says "frontend", "giao diện", "màn hình", "console".
---

# Frontend change (`apps/web`)

The console is TypeScript (strict), Vite, Mantine, React Router in hash mode, TanStack Query, Vitest (ADR-17). The layout of
screens and data sources is `docs/design/UX.md` (read the screen's section first; the wireframe is `wireframe-v2.md`, and
UX.md wins where they differ). The build, delivery and sign-in page are in `docs/ARCHITECTURE.md` section 8.

## Rules

- The UI calls only `/api/*` (`docs/API.md`), routes with `#hash` (it runs in an iframe of Organizr on an iPad), is
  English-only, has no CDN dependency (fonts and icons are bundled).
- **Touch first, portrait first.** Targets >= 44 px, no hover-only control, no behaviour that needs a wide screen. Design for
  about 820 px (iPad upright); landscape >= 1024 px only widens tables and grids, which fold into cards below 1024 px.
- **Money.** Every button that spends model calls goes through an Estimate/Confirm dialog (`components/EstimateSheet.tsx`,
  `ConfirmSheet.tsx`); "Try 20 lines" is the one exception and shows its cost on the button. The number of translations a
  Style or Glossary save would flag stale is shown live from `dry_run`, not behind a Check button.
- **Downloads** use server-served links plus a Copy button, never `blob:`.
- A page never builds HTML from text (React escapes it); paths and provider errors are untrusted.
- Login stays a standalone static page (`public/login.html`); do not move it into the bundle.

## Types come from the server

- `src/api/schema.gen.ts` is generated from `docs/openapi.json` by `npm run gen:api`; neither is edited by hand.
- A field a screen needs is added to the pydantic model (`apps/server/subserver/api/answers/<page>.py`), then run
  `uv run --locked --directory apps/server python -m subserver.api.openapi` and `npm run gen:api` and commit both. The CI
  `web` job runs `gen:api` and asks for no diff.
- `src/api/types.ts` only gives each generated type the name the screens use, and keeps the shapes only the UI has. Type a
  query from the route: `AnswerOf<"/api/units/{p1}/cues", "get">`, and build its URL with the typed route helper in
  `src/api/route.ts` (path parameters and query names are checked against the document); do not hand-write
  `?lang=${...}` strings in a new call.
- A setting a screen edits goes in `src/lib/settings.ts` (name, kind, limits as in UX Appendix B). A form that keeps a draft
  uses `src/lib/draft.ts` (never for a secret).
- Pure rules go in `src/lib/` and are tested without a DOM; a component stays thin.

## Commands (from `apps/web`, Node 24)

```bash
npm ci
npm run typecheck && npm run lint && npm test && npm run build   # what the CI `web` job runs
npm run dev           # Vite on :5173, proxies /api and /login to the worker
npm run gen:api
```

- **Windows:** run `npm test` from a shell whose path has an upper-case drive letter (`C:/Src/...`). With `c:\...` Vitest
  loads two copies of itself and every suite fails with "failed to find the current suite".
- Add dependencies with `npm install --save-exact`; `.npmrc` keeps install scripts off. `apps/web` is not a uv workspace
  member and the final image never holds Node or `node_modules`.
- Try it with real rows and no key: `scripts/preview.ps1`, then `node scripts/smoke/smoke.mjs --routes ...`. For a person-style
  check hand over to `test-plan`.

## Ships with the change

A `vitest` test next to the lib or screen (`*.test.ts(x)`), the four commands above green, `docs/design/UX.md` updated when a
screen's behaviour changed, `CHANGELOG.md` under Unreleased. Offer `test-plan` when a screen changed.
