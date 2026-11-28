# Handoff

Ghi đè toàn bộ file mỗi lần chuyển tool. Tối đa 30 dòng.

- Thời điểm: 2026-11-28
- Từ: Antigravity → Sang: phiên kế tiếp (chốt W1–W3 và bắt đầu P20)
- Nhánh / worktree: main (đã push lên `origin/main`)
- `scripts/check`: OK (10/10 bước ở `a8b5cfa`; `test_answer_contract` 13/13 OK + `doccheck` OK sau khi sửa `preview_seed.py`)
- Mục tiêu: đóng `## P19` trong `PLAN.md`, chốt W1–W3 trong `docs/design/REVIEW-P19-P20.md`, ghi FR rồi mới làm P20a–P20c.

## Đã xong
- **Mục 1 (P19-0 sau P19b/P19c):** đã dựng lại 3 bundle (`S02E04`, `S01E03`, `S01E05`) từ export read-only `Malcolm` (`/state/app.db`; không có `tmdb_api_key` trên homeserver): `generic_names_as_characters = []`, `pairs_in_both_raw = 0/0/0`, `S02E04 Story so far` `18 → 3` tập. Đối chiếu 66 câu golden (`local-state/p19/ab/after-comparison.md`): sửa được `#11`, `#75`, `#76`; các dao động còn lại (`#54`, `#68`, `#72`, `#18`) do SRT thiếu nhãn người nói.
- **Mục 2 (`/web-test`):** đã thêm `Bootstrap Demo` vào `preview_seed.py` (`bootstrap_demo=True` ở `main()`) & `fake_bazarr.py`; chạy Edge `820×1180` (`local-state/handoff/TEST-PLAN.md`): **5/5 PASS** (S1 Settings nhóm 2, S2 Aliases/chặn `Mom`/`dry_run`, S3 Bootstrap hộp vàng + Estimate, S4 `Hold again`, S5 sweep 820 px + `smoke.mjs` 11/11), 0 lỗi console/API.
- **Mục 3 (Push & CI):** đã commit + push `main` và kiểm tra CI GitHub (`tamvcspk/llm-subtitle-translator`).

## Việc tiếp theo (theo thứ tự bắt buộc)
1. Đóng P19 (`## P19` trong `PLAN.md` → `[x]` khi CI GitHub xanh), chốt W1–W3 của P20 (`docs/design/REVIEW-P19-P20.md`: route lưu hàng loạt, `base_version`/409, pin theo số cue), ghi FR vào `SPEC.md` rồi mới làm P20a–P20c.
2. Triển khai homeserver chỉ khi người dùng yêu cầu.

## Lưu ý cho người nhận
- `bash scripts/check` (WSL/Git Bash) bị chặn trong sandbox Windows (`E_ACCESSDENIED` / `0xC0000022`); chạy trực tiếp 10 lệnh trong PowerShell với `C:` viết hoa cho Vitest và `PYTHONUTF8=1`.
- Production `192.168.1.60` SSH qua `admin@192.168.1.60`, chỉ đọc; không ghi `.env`/secret.
