# Handoff

Ghi đè toàn bộ file mỗi lần chuyển tool. Tối đa 30 dòng.

- Thời điểm: 2026-11-28
- Từ: Claude Code (phản biện review P19, lập kế hoạch sửa) → Sang: phiên kế tiếp (sửa `R19-1..R19-10`, đóng P19 rồi sang P20)
- Nhánh / worktree: `main` (commit trước phiên này: `fc63f33`); phiên này chỉ sửa `PLAN.md` và file này, chưa sửa code
- Kiểm tra gần nhất: phiên trước `doccheck.py` OK, `test_core_boundary` 18/18 OK; phiên này chưa chạy kiểm tra nào (chỉ sửa docs)
- Mục tiêu kế tiếp: làm theo `PLAN.md` → `### Code review P19` → **"Phản biện và hướng chốt"** (mục đó thắng lời review gốc khi hai bên khác nhau).

## Đã xong trong phiên này
- Đối chiếu từng mục review với code; thêm R19-10 (`cast` không nằm trong `context_hash`) và việc đo lại P19-0 kèm cast.
- Người dùng chốt: production **có** `tmdb_api_key`; chỉ series **Malcolm in the Middle** bật Story memory; R19-1 theo **phương án B** (memory tắt thì không lấy cast, giữ hết rule); 25d làm **bản hẹp trong P19**.
- Phản biện được giữ: ngưỡng nén `len(older) > keep` giữ nguyên, sửa câu chữ FR-5.8/25g; bỏ tiêu chí 25h; R19-5 ý 3 không phải lỗi, điều kiện ý 2 đổi thành "có giai đoạn với `from_cue is None`".
- Sửa câu sai trong P19c của `PLAN.md`: `PROMPT_VERSION` có nằm trong `context_hash`.

## Việc tiếp theo (theo thứ tự bắt buộc)
1. Ghi `docs/DECISIONS.md` **D-08** (R19-1 B, ngưỡng nén, 25d bản hẹp, bỏ 25h) và sửa `SPEC.md` FR-5.8, FR-5.10(a), FR-5.11, tiêu chí 25d/25g/25h (skill `design-docs`).
2. `subcore`: R19-6 → R19-5 → 25d bản hẹp → compressor thăm dò cho `summarize_needed`.
3. Server: R19-3 (xoá `_extract_calls`) → R19-2 → R19-1 B + R19-10 → R19-9 ý 1.
4. Web: R19-2 `draftProblem` → R19-7 → R19-8 → R19-9 ý 2. Unit test đi cùng từng mục.
5. `CHANGELOG.md`, tick `R19-*`, `scripts/check` đủ 10 bước, push `main`, CI xanh.
6. Đo lại P19-0 kèm cast TMDB thật (hỏi người dùng trước khi đọc khoá trên homeserver), rồi đóng `## P19`; sau đó chốt W1–W3 (`docs/design/REVIEW-P19-P20.md`) cho P20.

## Lưu ý cho người nhận
- Trên Windows sandbox, `bash scripts/check` có thể gặp `E_ACCESSDENIED`; chạy trực tiếp 10 lệnh trong PowerShell với ổ `C:` viết hoa cho Vitest và `PYTHONUTF8=1`.
- Homeserver `192.168.1.60` chỉ SSH read-only khi cần và khi được cho phép; không chép khoá TMDB/Gemini vào file commit.
