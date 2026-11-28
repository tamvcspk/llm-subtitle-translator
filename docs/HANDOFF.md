# Handoff

Ghi đè toàn bộ file mỗi lần chuyển tool. Tối đa 30 dòng.

- Thời điểm: 2026-11-28
- Từ: Antigravity (Code review P19) → Sang: phiên kế tiếp (sửa review P19 `R19-1..R19-9`, đóng P19 rồi sang P20)
- Nhánh / worktree: `main` (`origin/main` ở `1b433f0`; commit mới nhất trên local ghi kết quả review P19)
- Kiểm tra gần nhất: `doccheck.py` OK, `test_core_boundary` 18/18 OK; code chưa sửa gì sau `1b433f0`
- Mục tiêu kế tiếp: xử lý 9 phát hiện code review P19 (`PLAN.md` → `### Code review P19`), chạy full check, đóng `## P19`, rồi mới chốt W1–W3 cho P20.

## Đã xong trong phiên này
- Code review toàn diện dải commit P19 (`9999037..1b433f0`), đối chiếu từng tiêu chí SPEC §6 (25a–i, 26a–f, 27a–d), invariants và ranh giới kiến trúc.
- Ghi đầy đủ 9 phát hiện (`R19-1..R19-9`: 4 Cao, 4 Trung bình, 1 Thấp) vào `PLAN.md` (`### Code review P19`), chưa sửa code theo yêu cầu.

## Việc tiếp theo (theo thứ tự bắt buộc)
1. Chốt hướng cho **R19-4** (tiêu chí **25d** gộp chính tả qua TMDB cast và **25h** đổi `PROMPT_VERSION` đánh dấu `Stale`: cài code + test hay ghi `DECISIONS.md` + cập nhật `SPEC.md`) và ngưỡng nén của **R19-3** (`len(older) > keep` vs `> 0`).
2. Sửa **R19-1..R19-9** kèm unit test (ưu tiên cụm Cao: `R19-1` giữ `cast` khi `memory_enabled=False`; `R19-2` lọc danh xưng chung ở `_sync_aliases`/backend/`draftProblem`; `R19-3` nối `_memory_calls` cho `retranslate`/`variant`/`estimate_retry`), cập nhật `CHANGELOG.md`, tick `[x]` từng `R19-*`.
3. Chạy đủ 10 bước kiểm tra (`scripts/check`), push lên `main`, đợi CI GitHub xanh rồi đánh dấu `[x]` đóng `## P19` trong `PLAN.md`.
4. Chốt W1–W3 trong `docs/design/REVIEW-P19-P20.md`, ghi FR vào `SPEC.md` rồi mới bắt đầu P20a–P20c.

## Lưu ý cho người nhận
- Trên Windows sandbox, `bash scripts/check` có thể gặp `E_ACCESSDENIED`; chạy trực tiếp 10 lệnh trong PowerShell với ổ `C:` viết hoa cho Vitest và `PYTHONUTF8=1`.
- Homeserver `192.168.1.60` chỉ SSH read-only khi cần; triển khai container chỉ khi người dùng yêu cầu rõ.
