# Handoff

Ghi đè toàn bộ file mỗi lần chuyển tool. Tối đa 30 dòng.

- Thời điểm: 2026-11-28
- Từ: Claude Code (làm bước `subcore` của R19) → Sang: phiên kế tiếp (bước server, rồi web)
- Nhánh / worktree: `main` (HEAD `1e82271` = docs D-08/SPEC v0.26); phần `subcore` của phiên này **chưa commit**
- Kiểm tra gần nhất: `bash scripts/check` → `check: OK` (10/10 bước; subcore 480 test OK, doccheck OK)
- Mục tiêu kế tiếp: làm theo `PLAN.md` → `### Code review P19` → **"Phản biện và hướng chốt"** (mục đó thắng lời review gốc khi hai bên khác nhau).

## Đã xong trong phiên này (chỉ `subcore` + docs; server chưa đổi)
- R19-6: `translate/scope.py` `Names(glossary, cast)`: hai mục glossary riêng không bao giờ `same`; khớp theo từ chỉ khi đúng một người mang tên đó trong glossary ∪ cast. `scoped_rules`, `ruled_pairs(rules, glossary, cast)`, `without_ruled_addresses(..., cast)` nhận `cast`.
- R19-5: `prompt.py` tính `in_scope` một lần, dùng cho Glossary, Character voices và Relationship state; `ruled_pairs` chỉ tính rule có `from_cue is None`.
- 25d hẹp: module mới `memory/spelling.py` (`spelling_map`, `rename_facts`); `build_context(..., cast=, keep=)` và `episode_cast(..., keep=)` gộp chính tả về tên cast. Fingerprint không đổi.
- R19-3 2b (phần subcore): `fold.condensing_calls(prior, budget_tokens, recent, cached)` chạy `build_context` với compressor thăm dò.
- Test: `test_prompt_scope.py` (mở rộng), `test_spelling.py` (mới), `test_memory.py::CondensingCallsTests`. `docs/ARCHITECTURE.md` và `PLAN.md` đã cập nhật. Chưa ghi `CHANGELOG.md` (làm ở bước cuối).

## Việc tiếp theo (theo thứ tự bắt buộc)
1. Server: R19-3 ý 1 (đổi 3 chỗ trong `unit_requests.py:142,191,279` sang `*self._memory_calls(...)`, xoá `_extract_calls` ở `unit_requests.py:47` và `job_requests.py:324`) + 2c (`summarize_needed` dùng `condensing_calls` với `cached=lambda e: facts.get_summary(entries_hash(e)) is not None`, gọi `_stored(units)` một lần; facts chưa lưu vẫn tính cận trên) → R19-2 → R19-1 B + R19-10 → R19-9 ý 1.
2. Server cũng phải truyền `cast=` (tên TMDB main+guests đã `clean_cast_name`) và `keep=` (tên và alias glossary) vào `build_context` ở `memory.py:_context`, và `keep=` vào `episode_cast`, để 25d có tác dụng; thêm test server (Carolyn → Caroline Miller qua `memory.plan`).
3. Web: R19-2 `draftProblem` → R19-7 → R19-8 → R19-9 ý 2. Unit test đi cùng từng mục.
4. `CHANGELOG.md`, tick `R19-*`, `scripts/check` đủ 10 bước, push `main`, CI xanh.
5. Đo lại P19-0 kèm cast TMDB thật (hỏi người dùng trước khi đọc khoá trên homeserver), rồi đóng `## P19`; sau đó chốt W1–W3 (`docs/design/REVIEW-P19-P20.md`) cho P20.

## Lưu ý cho người nhận
- Trên Windows sandbox, `bash scripts/check` có thể gặp `E_ACCESSDENIED`; lần này chạy được. Nếu lỗi, chạy trực tiếp 10 lệnh trong PowerShell với ổ `C:` viết hoa cho Vitest và `PYTHONUTF8=1`.
- Homeserver `192.168.1.60` chỉ SSH read-only khi cần và khi được cho phép; không chép khoá TMDB/Gemini vào file commit.
