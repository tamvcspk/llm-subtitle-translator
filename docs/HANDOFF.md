# Handoff

Ghi đè toàn bộ file mỗi lần chuyển tool. Tối đa 30 dòng.

- Thời điểm: 2026-11-27
- Từ: Claude Code → Sang: phiên kế tiếp (Claude Code hoặc Antigravity)
- Nhánh / worktree: main; chưa commit gì mới (code P19b/P19c và docs phản biện v0.25 đều ở working tree)
- Commit trước handoff: fb75de2
- `scripts/check`: check: OK (exit 0, 10/10 bước)
- Mục tiêu: đóng P19 (P19a, UI P19b, P19d), sau đó mới tới P20.

## Đã xong
- Phản biện chéo thiết kế P19a/b/d và P20, có đối chiếu code: `docs/design/REVIEW-P19-P20.md` (D1–D9 cho P19d, A1–A2, B1–B2, W1–W8 cho P20).
- Sửa P19d theo phản biện: SPEC v0.25 (FR-15.12, tiêu chí 27a/c/d), ARCHITECTURE v0.25 (điểm 7 "Quyết định P19"), UX.md v0.25 (Hold again `relockable`, `started: false`, impact của Characters là hợp tập stale), `docs/DECISIONS.md` D-07, PLAN P19d/P20.
- P20 §6 ghi kết quả phản biện; ba điểm chặn W1 (route lưu hàng loạt), W2 (`base_version`/409), W3 (pin Timeline theo số cue, không đọc SRT).

## Đang dở
- Code P19b/P19c chưa commit, chờ người dùng xem diff.
- D-07 và lựa chọn W1/W2 chờ người dùng duyệt.

## Việc tiếp theo (theo thứ tự bắt buộc)
1. Người dùng duyệt D-07 + sửa v0.25, và cho phép commit (code P19b/P19c + docs phản biện).
2. Code phần còn lại của P19 kèm unit test: P19a (lưu ý nháp `sessionStorage` cũ của nhóm 5, A2), UI/sync alias P19b + `dry_run` cho `PUT …/characters` (B1), P19d theo D-07 (test "rút rồi mở lại thì policy xin lại đúng các tập").
3. Đo "sau" P19-0 trên 3 bundle thật (hỏi phép trước khi đọc homeserver / gọi model).
4. Đóng P19, chốt W1–W3, ghi FR P20 rồi mới làm P20a–P20c.

## Lưu ý cho người nhận
- Luật D4 (`jobs/enqueue.py:80`): policy không xin lại tập có job `failed`/`cancelled`. Mọi thứ huỷ job policy phải tính tới luật này.
- "Mom/Dad": chặn ở form, không ném `GlossaryError` trong `from_doc`.
- Production `192.168.1.60` chỉ đọc; không ghi `.env`/secret; Windows cần ổ `C:` hoa cho Vitest và `PYTHONUTF8=1`.
