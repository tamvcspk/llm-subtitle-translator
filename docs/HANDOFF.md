# Handoff

Ghi đè toàn bộ file mỗi lần chuyển tool. Tối đa 30 dòng.

- Thời điểm: 2026-11-28
- Từ: Claude Code → Sang: phiên kế tiếp (Claude Code hoặc Antigravity)
- Nhánh / worktree: main
- Commit trước handoff: f121e5b (code P19a/P19b-UI/P19d nằm ở commit kế tiếp, xem `git log`)
- `scripts/check`: check: OK (exit 0, 10/10 bước)
- Mục tiêu: đóng P19, sau đó mới tới P20.

## Đã xong
- Người dùng đã duyệt D-07 (2026-11-28). Code phần còn lại của P19, kèm unit test (server và Vitest):
  - P19a: `domain.config.task_models` + `task_models` ở `GET /api/settings`, `Estimate.model`, `extract_model`; Settings nhóm 2 gom đủ ô model, placeholder động, bảng `Active models by task`, cảnh báo; nháp cũ nhóm 5 bỏ `memory_model` (A2).
  - P19b UI/sync: một ô `Aliases` ở Name, danh xưng chung bị chặn trên form, alias quy về tên chuẩn ở Forms of address (`Written via alias`, `Conflicting stages`, Dọn trùng), `_sync_aliases` khi accept đề xuất, `PUT …/characters` có `dry_run` (B1).
  - P19d theo D-07: `PUT …/bootstrap` (`estimate_only`, `started`, `cancel_queued_policy`/`cancelled`), `AutoRequest.start` chạy nền một series, `JobStore.withdraw_policy` (result `bootstrap_hold`), `Store.policy_blocked`, `relockable`, `held_by_lang`; `BootstrapNotice` ở web.
  - Docs: API, ARCHITECTURE, CONFIGURATION, CONTEXT, CHANGELOG, PLAN, openapi.

## Việc tiếp theo (theo thứ tự bắt buộc)
1. Đo "sau" P19-0 trên 3 bundle thật (S02E04, S01E03, S01E05): hỏi phép trước khi đọc homeserver / gọi model (PLAN P19 "Chạy lại P19-0").
2. Thử trên trình duyệt (`test-plan`: preview `-Bazarr` cho hộp vàng, Hold again, Settings nhóm 2); chưa ai bấm thử các màn mới.
3. CI GitHub xanh; triển khai homeserver chỉ khi được yêu cầu.
4. Đóng P19, chốt W1–W3 của P20 (route lưu hàng loạt, `base_version`/409, pin theo số cue), ghi FR rồi mới làm P20a–P20c.

## Lưu ý cho người nhận
- Luật D4 (`jobs/enqueue.py`): policy không xin lại tập `failed`/`cancelled`; riêng `cancelled` + result `bootstrap_hold` (Hold again) được bỏ qua, cả ở `last_ended_state` lẫn `policy_jobs_since`.
- Bootstrap chỉ chọn tập có phụ đề nguồn và policy xin được; hộp vàng đếm theo ngôn ngữ (`held_by_lang`), không còn con số tổng 121.
- "Mom/Dad": chặn ở form (`lib/address.ts` khớp `subcore.memory.address`), không ném `GlossaryError` trong `from_doc`.
- Production `192.168.1.60` chỉ đọc; không ghi `.env`/secret; Windows cần ổ `C:` hoa cho Vitest và `PYTHONUTF8=1`.
