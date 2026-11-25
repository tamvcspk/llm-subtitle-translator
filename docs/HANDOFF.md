# Handoff
Ghi đè toàn bộ file mỗi lần chuyển tool. Tối đa 30 dòng.

- Thời điểm: 2026-11-26 +07:00
- Từ: Claude Code → Sang: (phiên kế tiếp)
- Nhánh / worktree: main (commit cuối: 297d614 chore: adopt project-kit; thay đổi dưới đây chưa commit)
- `scripts/check`: check: OK (exit 0, 10/10 bước)

## Mục tiêu đang làm
P19 (Giai đoạn 1: Dịch & Glossary) theo trace *Malcolm in the Middle* trên homeserver.

## Đã xong
- Review chéo và chốt phạm vi P19: `docs/DECISIONS.md` D-02.
- Ghi mục **P19** vào `PLAN.md` (dòng tổng quan + mục chi tiết sau P18): P19-0 harness, P19b danh tính (danh xưng chung, `_Names.add`, extract `x4` với `senior`, TMDB cast, `Character.aliases`), P19c prompt sạch (Glossary thắng, Forms of address theo phạm vi tập, nén Story so far > 3 tập), P19a model theo tác vụ, P19d Bootstrap qua Estimate; ý tưởng `speaker`/`addressee` ở backlog.

## Đang dở
- Chưa sửa SPEC/ARCHITECTURE/UX, chưa code.

## File đã đụng
- `PLAN.md`, `docs/DECISIONS.md`, `docs/HANDOFF.md`; `local-state/handoff/P19-TRACE-AND-PLAN.md` (git-ignored).

## Việc tiếp theo (theo thứ tự)
1. Commit các file docs trên (khi người dùng cho phép).
2. P19-0: xin phép đọc homeserver, lấy bundle S02E04 vào `local-state/p19/`, lưu `render-prompt` làm mốc, chọn ~30 câu xưng hô.
3. SPEC/ARCHITECTURE cho P19b (skill `design-docs`, `architect`), rồi code P19b → P19c → P19a → P19d theo `PLAN.md`.

## Lưu ý cho người nhận
- "Mom/Dad" là danh xưng tương đối, không bao giờ làm alias toàn cục. `subcore` không biết TMDB: chỉ nhận danh sách tên qua bundle.
- Không ghi `.env`/secret homeserver vào file commit; đối chiếu `local-state/feedback/UX-FEEDBACK-01..03.md`.
