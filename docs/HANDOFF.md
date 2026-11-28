# Handoff

Ghi đè toàn bộ file mỗi lần chuyển tool. Tối đa 30 dòng.

- Thời điểm: 2026-11-28
- Từ: Claude Code (làm mục 1 "Việc tiếp theo": push bị từ chối) → Sang: phiên kế tiếp (push khi được phép, rồi đo P19-0)
- Nhánh / worktree: `main`, **3 commit chưa push** (`4e2acd6`, `8f8c09c`, `d93b1ab`); cây làm việc sạch
- Kiểm tra gần nhất: `bash scripts/check` → `check: OK` (10/10 bước), chạy ngay trước lần push bị chặn
- Mục tiêu kế tiếp: làm theo `PLAN.md` → `### Code review P19` → **"Phản biện và hướng chốt"** (mục đó thắng lời review gốc).

## Đã xong
- Server (commit `8f8c09c`) và web (commit `d93b1ab`) của R19-1..R19-10, 25d, D-08; `PLAN.md` đã tick R19-1..R19-10 (trừ P19-0 đo lại) và bước 1-4 của "Thứ tự làm".
- Web: `draftProblem` + `EditSheet` (R19-2), `dedupeAddressRules.merging` và `DeleteSheet` kèm `delete_card` (R19-7), hẹn `refresh()` 2 s và nhãn `Translations to release` + `By language 99 vi + 21 fr` (R19-8), `fieldProblem` chặn Default model rỗng (R19-9).
- `CHANGELOG.md` (mục "Code review fixes (R19-1..R19-10, D-08)"), `docs/design/UX.md` (bảng Settings: `Choose a model`), `wireframe-v2.md` (nhãn Bootstrap).

## Việc tiếp theo (theo thứ tự bắt buộc)
1. **Bị chặn:** push `main` lên `origin`. Lần thử push bị auto-mode classifier từ chối ("Destructive or Unwanted Actions"). Chờ người dùng cho phép push (hoặc thêm quyền Bash `git push` trong settings), rồi `gh run list -R tamvcspk/llm-subtitle-translator` đến khi CI xanh.
2. Đo lại P19-0 kèm cast TMDB thật (hỏi người dùng trước khi đọc khoá trên homeserver; không chép khoá ra file commit), chạy `measure.py`, ghi số dòng Forms of address vào P19-0, rồi đóng `## P19`.
3. Chốt W1–W3 (`docs/design/REVIEW-P19-P20.md`) cho P20; chỉ bắt đầu P20 sau khi đóng P19.
4. Cân nhắc `test-plan` (browser) cho: xoá nhân vật chỉ có Voice, nhãn alias ở Glossary, sửa đề xuất nhân vật tên `Mom` ở Review, hộp Bootstrap hai ngôn ngữ.

## Lưu ý cho người nhận
- Nhãn Bootstrap dựa trên số ngôn ngữ đang giữ (không so `estimate.jobs` với `held_by_lang[lang]`, vì trần quota cũng làm `jobs` nhỏ đi); dòng chia là `99 vi + 21 fr`, không phải `N × M`.
- Hook `rtk` đôi khi chặn Bash ("a hook changed this call's input"): chạy lại đúng input một lần, hoặc dùng Read/Grep/Edit.
- Vitest cần shell có ổ `C:` viết hoa; `bash scripts/check` chạy được trên máy này.
- Homeserver `192.168.1.60` chỉ SSH read-only khi cần và khi được cho phép.
