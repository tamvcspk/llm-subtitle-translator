---
name: design-docs
description: Update the Vietnamese design docs docs/design/SPEC.md and docs/design/ARCHITECTURE.md — record answers to open questions, add or change FR/NFR/ADR, adjust the roadmap. Use when the user answers a design question ("câu hỏi mở", "chốt"), changes a requirement, or asks to "update spec/architecture".
---

# Design docs

`docs/design/SPEC.md` (what) and `docs/design/ARCHITECTURE.md` (how) are the contract for the redesign. Keep them consistent with each other; they are written in Vietnamese — keep that language and the existing terminology (unit, story order, facts, story state @N, glossary, context bundle, stale…, SPEC §2).

## Rules

1. **Version bump.** Bump `Draft vX.Y` in the header of every file you change and add one line `> vX.Y: <what changed>` under the existing history lines in SPEC.md. Same version number in both files when both change.
2. **Closing an open question** (SPEC §8): move it to the decisions table in SPEC §7 (next `#`, question, decision), remove it from §8 (write `Chưa có.` if empty), then apply the decision everywhere it lands — Non-goals, the relevant FR/NFR, ARCHITECTURE sections (§1 ADRs, §5 API, §6 config, §7 roadmap, §8 risks). Grep both files for the topic keyword to find every spot.
3. **Deviations** from the original SRS get **[Đổi]** plus a `*Lý do:*` line. Do not remove existing [Đổi] markers.
4. **Deferred work** goes into Non-goals (v1) with the target version (e.g. "để v1.1"), not silently dropped.
5. **Roadmap**: each phase row needs a concrete, testable "Kết quả kiểm chứng". New work that must happen early gets its own row (e.g. `P0b`) rather than being squeezed into an unrelated phase.
6. **IDs are stable**: never renumber FR/NFR/ADR or acceptance criteria; append new ones.
7. Settings/env names introduced here must later appear in `docs/CONFIGURATION.md` when implemented — note that in the report, do not edit CONFIGURATION.md for unimplemented features.

## Report

List each change as `file §section: before → after` in one line, and call out any decision you had to make yourself that the user did not state.
