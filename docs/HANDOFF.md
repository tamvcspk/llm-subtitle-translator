# Handoff

Ghi đè toàn bộ file mỗi lần chuyển tool. Tối đa 30 dòng.

- Thời điểm: 2026-11-27
- Từ: Antigravity → Sang: phiên kế tiếp (Claude Code hoặc Antigravity)
- Nhánh / worktree: main (docs P19/P20 đã commit; code P19b/P19c đang ở working tree chưa commit)
- Commit trước handoff: dbb2bd6 (`git log -1` xem SHA commit handoff mới nhất)
- `scripts/check`: check: OK (exit 0, 10/10 bước)

## Mục tiêu đang làm
Chốt toàn bộ thiết kế UX/Spec cho P19 (P19a, P19b, P19d) và thiết kế P20 (v1.1 Unified 4-View Glossary Workspace).

## Đã xong
- Docs P19 (`SPEC.md` v0.24, `ARCHITECTURE.md` v0.24, `UX.md` v0.24, `wireframe-v2.md` v2.4, `DECISIONS.md` D-04..D-05): P19a (gom model về nhóm 2, bảng `Active models by task`, model trên từng nhóm Estimate), P19b UI (1 ô `Aliases` ở `Name`, chặn danh xưng chung lúc sửa nhưng giữ `from_doc` tương thích DB cũ, dọn trùng qua alias), P19d (sửa lệch `121 wait` vs `99 missing` do `has_source`, 4 trạng thái thật, nút mở khoá qua Estimate chạy `AutoRequest.run()` ngay, `Hold again` huỷ job `policy` chờ).
- Docs P20 (`docs/design/P20-GLOSSARY-WORKSPACE.md` v0.1, `DECISIONS.md` D-06, `PLAN.md` P20): gộp 3 tab `Glossary`+`Characters`+`Suggestions` thành 1 tab `Glossary` (4 góc nhìn `List · Relations · Timeline · Triage` + `Scope X-ray`), làm ở v1.1 sau P19.
- Code P19b/P19c lõi (từ phiên trước, chưa commit) vẫn xanh 10/10 bước `scripts/check`.

## Đang dở
- Code P19b/P19c chưa commit (chờ người dùng duyệt diff). Chưa code: P19a, phần UI/sync alias của P19b + gộp chính tả TMDB, P19d, và đo "sau" P19-0 trên 3 bundle thật.

## Việc tiếp theo (theo thứ tự bắt buộc)
1. **VIỆC ĐẦU TIÊN:** Review & phản biện chéo toàn bộ thiết kế vừa ghi (`SPEC.md` v0.24, `ARCHITECTURE.md` v0.24, `UX.md` v0.24, `wireframe-v2.md` v2.4, `DECISIONS.md` D-04..D-06, và `docs/design/P20-GLOSSARY-WORKSPACE.md` đặc biệt mục 6).
2. Người dùng xem diff code P19b/P19c đang có ở working tree và cho phép commit.
3. Code phần còn lại của P19 (UI/TMDB P19b, P19a, P19d) kèm unit test, và chạy đo "sau" P19-0 (hỏi phép trước khi đọc homeserver / gọi model).
4. Sau khi đóng P19 mới bắt đầu P20a–P20c.

## Lưu ý cho người nhận
- "Mom/Dad" là danh xưng tương đối: chặn khi sửa UI, tuyệt đối không ném `GlossaryError` trong `from_doc` (tránh dừng job trên DB cũ).
- Production `192.168.1.60` chỉ đọc; không ghi `.env`/secret vào file commit; Windows cần ổ `C:` hoa cho Vitest và `PYTHONUTF8=1`.
