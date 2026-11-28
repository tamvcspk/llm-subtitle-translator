# Handoff

Ghi đè toàn bộ file mỗi lần chuyển tool. Tối đa 30 dòng.

- Thời điểm: 2026-11-28
- Từ: Claude Code (đóng P19: đo P19-0 kèm cast, sửa CLI, dựng lại bundle v2) → Sang: phiên kế tiếp
- Nhánh / worktree: `main`. Đã push tới `9ad076d` (CI và Docker xanh). **Chưa push** `cfeed95` (sửa CLI + P19-0) và commit đóng P19 (sau `cfeed95`).
- Kiểm tra gần nhất: `bash scripts/check` → `check: OK` (10/10) trên `cfeed95`; commit đóng P19 chỉ đổi PLAN.md và HANDOFF.md.

## Đã xong
- `## P19` đóng trong PLAN.md. P19-0 đo lại kèm cast TMDB: bundle v2 (sections dựng lại bằng code hiện tại, cast TMDB) cho Forms of address S02E04 **66** (33 cặp), S01E03 **66**, S01E05 **60**; `Mom`/`Dad` là nhân vật **0**; cặp trùng Glossary **0**. Số chi tiết: `local-state/p19/v2-measure.json`.
- Sửa `subcore` CLI: `render-prompt --bundle` và `translate --bundle` bỏ `cast`. Đã sửa `cli._inputs`, có test `test_the_cli_keeps_the_episode_cast_of_a_bundle`.

## Việc tiếp theo (theo thứ tự)
1. Push `main` khi user cho phép, rồi `gh run list -R tamvcspk/llm-subtitle-translator` đến khi CI xanh.
2. P20: chốt W1–W3 (`docs/design/REVIEW-P19-P20.md`: route lưu hàng loạt, `base_version`/409, pin theo số cue), rồi ghi FR. Chỉ làm P20a sau khi chốt.
3. A/B xưng hô trên bundle v2 (cần gọi model: hỏi user trước, có Estimate). Không bắt buộc để đóng P19.
4. Cân nhắc `test-plan` (browser) cho: xoá nhân vật chỉ có Voice, nhãn alias ở Glossary, sửa đề xuất `Mom` ở Review, hộp Bootstrap hai ngôn ngữ.

## Lưu ý cho người nhận
- Container `llm-translator` trên homeserver (`192.168.1.60`) chạy image cũ, không có `build_tmdb_cast` (P19b). Cast TMDB chưa chạy ở production. Ghi chú memory nói container đã gỡ là sai.
- Khoá TMDB nằm ở bảng `api_keys` (provider `tmdb`) trong `/state/app.db` của container. Chỉ đọc bằng `sqlite3` `mode=ro`, không in giá trị.
- `rtk` rút gọn đầu ra cả khi ghi vào file: dùng `rtk proxy <cmd>` khi cần đủ nội dung (render prompt, measure).
- Hook `rtk` đôi khi chặn Bash ("a hook changed this call's input"): chạy lại đúng input một lần, hoặc dùng Read/Grep/Edit.
- Vitest cần shell có ổ `C:` viết hoa; `bash scripts/check` chạy được trên máy này.
