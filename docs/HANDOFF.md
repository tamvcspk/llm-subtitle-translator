# Handoff

Ghi đè toàn bộ file mỗi lần chuyển tool. Tối đa 30 dòng.

- Thời điểm: 2026-11-28
- Từ: Claude Code (làm bước 1: ghi D-08 và sửa SPEC) → Sang: phiên kế tiếp (bước 2: `subcore`, rồi server, web cho `R19-1..R19-10`)
- Nhánh / worktree: `main` (HEAD `a8b5cfa`); các sửa docs của phiên này **chưa commit**
- Kiểm tra gần nhất: `bash scripts/check` → `check: OK` (10/10 bước, gồm doccheck và web build) sau khi sửa docs
- Mục tiêu kế tiếp: làm theo `PLAN.md` → `### Code review P19` → **"Phản biện và hướng chốt"** (mục đó thắng lời review gốc khi hai bên khác nhau).

## Đã xong trong phiên này (chỉ docs, không đổi code)
- `docs/DECISIONS.md` **D-08**: R19-1 phương án B (cast lười, chỉ khi memory bật), giữ ngưỡng nén `len(older) > keep`, 25d bản hẹp, bỏ 25h cũ.
- `docs/design/SPEC.md` **v0.26**: FR-5.8, FR-5.10(a), FR-5.11, tiêu chí 25d/25g/25h, dòng lịch sử v0.26, quyết định #33.
- `docs/design/ARCHITECTURE.md` **v0.26**: sửa câu "đổi `PROMPT_VERSION` → Stale" (nay là không Stale, nằm trong `context_hash`), thêm đoạn compressor thăm dò và cast lười. `PLAN.md` tick bước 1.
- Chưa sửa `CONFIGURATION.md`/`API.md` (không có setting hay route mới).

## Việc tiếp theo (theo thứ tự bắt buộc)
1. `subcore`: R19-6 (`Names` nhận `cast`, chống nhập nhằng) → R19-5 → 25d bản hẹp (gộp chính tả về tên TMDB trong `subcore.memory`) → compressor thăm dò cho `summarize_needed` (R19-3 2b).
2. Server: R19-3 ý 1 (đổi 3 chỗ sang `*self._memory_calls(...)`, xoá `_extract_calls`) + 2c (`_stored(units)` một lần) → R19-2 → R19-1 B + R19-10 → R19-9 ý 1.
3. Web: R19-2 `draftProblem` → R19-7 → R19-8 → R19-9 ý 2. Unit test đi cùng từng mục.
4. `CHANGELOG.md`, tick `R19-*`, `scripts/check` đủ 10 bước, push `main`, CI xanh.
5. Đo lại P19-0 kèm cast TMDB thật (hỏi người dùng trước khi đọc khoá trên homeserver), rồi đóng `## P19`; sau đó chốt W1–W3 (`docs/design/REVIEW-P19-P20.md`) cho P20.

## Lưu ý cho người nhận
- Trên Windows sandbox, `bash scripts/check` có thể gặp `E_ACCESSDENIED`; lần này chạy được. Nếu lỗi, chạy trực tiếp 10 lệnh trong PowerShell với ổ `C:` viết hoa cho Vitest và `PYTHONUTF8=1`.
- Homeserver `192.168.1.60` chỉ SSH read-only khi cần và khi được cho phép; không chép khoá TMDB/Gemini vào file commit.
