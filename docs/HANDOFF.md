# Handoff

Ghi đè toàn bộ file mỗi lần chuyển tool. Tối đa 30 dòng.

- Thời điểm: 2026-11-28
- Từ: Claude Code (CI xanh, đo P19-0 kèm cast TMDB, sửa CLI) → Sang: phiên kế tiếp
- Nhánh / worktree: `main` đã push tới `9ad076d`, CI xanh (`46161133795`), Docker image xanh (`46161133785`). **Chưa commit** các thay đổi sau `9ad076d` (CLI, test, CHANGELOG, PLAN, HANDOFF).
- Kiểm tra gần nhất: `bash scripts/check` → `check: OK` (10/10) sau khi sửa CLI và docs.

## Đã xong
- CI và Docker image của `main` (`9ad076d`) đều xanh.
- P19-0 đo lại với cast TMDB (ghi chi tiết trong PLAN.md, mục P19-0): Forms of address S02E04 72 → 66 (cast TMDB), S01E03 152 → 64, S01E05 152 → 62; bundle "sau" có sẵn: 50 / 30 / 40 (cast 7–16, không TMDB).
- Lỗi subcore CLI: `render-prompt --bundle` và `translate --bundle` bỏ `cast`; đã sửa `cli._inputs`, thêm test `test_the_cli_keeps_the_episode_cast_of_a_bundle`.

## Việc tiếp theo (theo thứ tự)
1. Commit các thay đổi local sau `9ad076d` (khi user cho phép), rồi push (cần cho phép push), rồi kiểm CI.
2. P19 chưa đóng: mục P19c (test "P19-0 diff trên S02E04") còn mở. Đóng khi: (a) dựng lại `sections` bằng code mới với cast TMDB (harness `StoryMemory.plan` không còn trong repo), hoặc (b) chấp nhận số đo hiện tại. A/B xưng hô với cast TMDB cần model call (hỏi user trước, có Estimate).
3. Chốt W1–W3 (`docs/design/REVIEW-P19-P20.md`) cho P20; chỉ bắt đầu P20 sau khi đóng P19.
4. Cân nhắc `test-plan` (browser) cho: xoá nhân vật chỉ có Voice, nhãn alias ở Glossary, sửa đề xuất nhân vật tên `Mom` ở Review, hộp Bootstrap hai ngôn ngữ.

## Lưu ý cho người nhận
- Container `llm-translator` trên homeserver (`192.168.1.60`) đang chạy image cũ: không có `build_tmdb_cast` (P19b), nên cast TMDB chưa chạy ở đó. Ghi chú memory nói container đã gỡ là sai.
- DB live trên homeserver: `/state/app.db` trong container; khoá TMDB ở bảng `api_keys` (provider `tmdb`). Đọc chỉ bằng `sqlite3` `mode=ro`, không in giá trị.
- `rtk` rút gọn đầu ra cả khi ghi vào file: dùng `rtk proxy <cmd>` khi cần đầy đủ (render prompt, measure).
- Hook `rtk` đôi khi chặn Bash ("a hook changed this call's input"): chạy lại đúng input một lần, hoặc dùng Read/Grep/Edit.
- Vitest cần shell có ổ `C:` viết hoa; `bash scripts/check` chạy được trên máy này.
