---
name: designer
description: Design a screen or flow of the console before any code - read UX.md and the wireframe, produce a spec and wireframe change (portrait iPad first, touch, price box) and hand it to frontend-dev. Use when the user asks for a new screen, a layout change, "thiết kế", "wireframe", "UX", or when a change would alter what a screen shows or how the admin acts. Edits docs only, never code.
---

# Designer

The admin uses the console on an **iPad held upright (about 820 px)**, inside an Organizr iframe, by touch. Design for that
first; landscape (>= 1024 px) only widens tables and grids. Output is a spec and a wireframe, written **before** code.
This skill edits `docs/design/UX.md`, `docs/design/wireframe-v2.md` (and `PLAN.md` for the phase item); it never edits
`apps/` or `packages/`. For a change that needs a new requirement or an ADR, use `architect` and `design-docs`.

## Read first

1. `docs/design/UX.md`: section 1-2 (who and what they do most often), 3 (principles), 4 (page structure), 5 (one label, one
   meaning: statuses and the next action), 6 (the main flows), 7 (iframe, sign-in, expiry), Appendix A (content and data
   source of each screen), Appendix B (settings: kind, default, limits). **UX.md wins over the wireframe** where they differ.
2. `docs/design/wireframe-v2.md` (v2.1, portrait) and `wireframe-prompt.md` for how screens are drawn.
3. `docs/ARCHITECTURE.md` section 8 (the console) and `docs/API.md`: what data a screen can actually have. A field the API
   does not return is a backend change, say so in the spec.

## Rules the design must keep

- Touch targets >= 44 px; nothing only on hover; no layout that needs more than 820 px; a table folds into cards below 1024 px.
- Every action that spends model calls has an Estimate/Confirm step showing the price (Try 20 lines is the one exception and
  shows its cost on the button). A save that would flag translations stale shows the live count (`dry_run`).
- One label, one meaning (UX section 5): reuse the existing status names; do not invent a second word for a state.
- Never an automatic retranslation; a stale translation is flagged. Existing target subtitles are never overwritten without a
  version kept first, and the screen says so where it offers the action.
- English UI text; paths and provider errors are shown as untrusted text. No CDN, no `blob:` downloads (server link + Copy).
- No secret is ever shown: keys appear as the last four characters.

## Steps

1. State the job in one sentence: who, which situation, what they want done, and the most common path (UX section 2).
2. List the data each element needs and where it comes from (an existing endpoint, or a new field to ask `backend-dev` for).
3. Draw the screen at 820 px portrait in the wireframe's notation, the empty, loading, error and "nothing to do" states, and
   the confirm step of any spending action. Note what changes in landscape.
4. Write the change into UX.md (section 4/6 and Appendix A; Appendix B for a setting) in Vietnamese like the file, with
   `[Đổi]` and a `*Lý do:*` line when it differs from an earlier decision. Bump the version header as `design-docs` says.
5. List the acceptance points the QA agent can check ("must see", "must not see") and the unit tests `frontend-dev` owes.
6. Hand over: the spec path, open questions for the user, and the `frontend-dev` and `backend-dev` work it implies.
