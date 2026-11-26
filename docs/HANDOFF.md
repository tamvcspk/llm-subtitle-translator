# Handoff

Ghi đè toàn bộ file mỗi lần chuyển tool. Tối đa 30 dòng.

- Thời điểm: 2026-11-27
- Từ: Claude Code → Sang: phiên kế tiếp (Claude Code hoặc Antigravity)
- Nhánh / worktree: main (5 file docs sửa, chưa commit)
- Commit cuối: 9999037 feat(P19): add scope and details for Phase 19
- `scripts/check`: check: OK (exit 0, 10/10 bước)

## Mục tiêu đang làm
P19 (Dịch & Glossary) theo trace *Malcolm in the Middle*; hiện ở bước spec xong, chưa code.

## Đã xong
- P19-0: mốc trên 3 tập (S02E04, S01E03, S01E05) trong `local-state/p19/` (git-ignored); Prod sai 6/66, chạy lại sai 9/66; số mốc ở `PLAN.md` mục P19-0.
- SPEC v0.22 (FR-5.9 đến 5.12, tiêu chí 25, #30), ARCHITECTURE v0.22 ("Quyết định P19"), `docs/DECISIONS.md` D-03.

## Đang dở
- Chưa code P19b/P19c. P19a và P19d chưa có spec/UX. Chưa chạy "sau" (cần code trước).

## File đã đụng
- `PLAN.md`, `docs/DECISIONS.md`, `docs/HANDOFF.md`, `docs/design/SPEC.md`, `docs/design/ARCHITECTURE.md`.

## Việc tiếp theo (theo thứ tự)
1. Commit 5 file docs trên (khi người dùng cho phép).
2. Kiểm `Facts.hash` và tra cứu facts theo `prompt_version` (`adapters/store/memory.py`): đổi `x3` → `x4` không được kéo extract lại.
3. Code P19b → P19c kèm unit test; rồi chạy "sau" (`render-prompt`, `measure.py`, chấm tay 66 câu; gọi model cần hỏi người dùng, key ở `local-state/secrets/gemini.key`). Sau đó P19a, P19d (docs/UX trước).

## Lưu ý cho người nhận
- "Mom/Dad" là danh xưng tương đối, không bao giờ làm alias toàn cục. `subcore` không biết TMDB, chỉ nhận danh sách tên qua bundle.
- Production là container `llm-translator` (v1.0.0-rc.2) trên 192.168.1.60, chỉ đọc (DB `mode=ro`). Windows cần `PYTHONUTF8=1` cho `subcore`.
- Không ghi `.env`/secret homeserver vào file commit.
