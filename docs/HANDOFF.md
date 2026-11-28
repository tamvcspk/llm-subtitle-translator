# Handoff

Ghi đè toàn bộ file mỗi lần chuyển tool. Tối đa 30 dòng.

- Thời điểm: 2026-11-28
- Từ: Claude Code (làm xong bước server của R19) → Sang: phiên kế tiếp (bước web, rồi đóng P19)
- Nhánh / worktree: `main` (HEAD `4e2acd6` = subcore); phần server của phiên này **chưa commit**
- Kiểm tra gần nhất: `bash scripts/check` → `check: OK` (10/10 bước) sau khi sửa server
- Mục tiêu kế tiếp: làm theo `PLAN.md` → `### Code review P19` → **"Phản biện và hướng chốt"** (mục đó thắng lời review gốc khi hai bên khác nhau).

## Đã xong trong phiên này (server, test đi cùng)
- R19-3: bỏ `_extract_calls` (2 chỗ); retranslate/variant/retry dùng `*self._memory_calls(...)`; `StoryMemory.summarize_needed` gọi `subcore.memory.condensing_calls` và `_stored` một lần.
- R19-1 B: `worker.process_job` truyền `description` và `cast` dạng callable; `TranslationAttempt.run` chỉ gọi cast trong nhánh `memory.plan` (memory bật), description sau hai lần kiểm skip.
- R19-10: `context_hash(..., cast)` thêm hash của `sorted(cast)` khi có cast. 25d: `plan(keep=)` nhận tên và alias glossary, `build_context(cast=, keep=)`, `episode_cast(keep=)`.
- R19-2 server: `_sync_aliases` và `normalise` lọc danh xưng chung; `save_owner_character`/`save_owner_card` chỉ chặn alias **mới thêm** (`ValueError` → 400). `Owner.source_lang` mới.
- R19-9 ý 1: `TmdbMetadata` nhớ lần gọi Bazarr theo `job_id` (`_OncePerJob`, khoá không chứa header).
- Test: `test_story_memory.py` (CondensingTests, LazyLookupsTests, ContextHashCastTests, 25d), `test_suggestions.py`, `test_character_write.py`, `test_tmdb_cast.py`. `docs/ARCHITECTURE.md`, `PLAN.md` đã cập nhật; `CHANGELOG.md` chưa.

## Việc tiếp theo (theo thứ tự bắt buộc)
1. Web (`apps/web`, skill `frontend-dev`): R19-2 `draftProblem` dùng lại `nameProblems` của form Characters (chặn alias danh xưng chung mới thêm trong `suggestions.ts`) → R19-7 (`dedupeAddressRules`, `DeleteSheet` + `useCharacterImpact` kèm `delete_card`) → R19-8 (hẹn `refresh()` lần hai ~2 s ở `useReleaseBootstrap`; nhãn `Translations to release`) → R19-9 ý 2 (`activeModels` dùng model mặc định server trả). Vitest đi cùng; chạy `npm test` từ shell có ổ `C:` viết hoa.
2. `CHANGELOG.md` (R19-1..R19-10, 25d, D-08), tick `R19-2`, `R19-7`, `R19-8`, `R19-9`, `scripts/check` đủ 10 bước, push `main`, CI xanh.
3. Đo lại P19-0 kèm cast TMDB thật (hỏi người dùng trước khi đọc khoá trên homeserver), rồi đóng `## P19`; sau đó chốt W1–W3 (`docs/design/REVIEW-P19-P20.md`) cho P20.

## Lưu ý cho người nhận
- Trên Windows sandbox, `bash scripts/check` có thể gặp `E_ACCESSDENIED`; lần này chạy được. Nếu lỗi, chạy trực tiếp 10 lệnh trong PowerShell với ổ `C:` viết hoa cho Vitest và `PYTHONUTF8=1`.
- Homeserver `192.168.1.60` chỉ SSH read-only khi cần và khi được cho phép; không chép khoá TMDB/Gemini vào file commit.
