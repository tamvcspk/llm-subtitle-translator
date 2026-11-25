# Quyết định kỹ thuật

Chỉ thêm, không sửa entry cũ. Đổi quyết định thì thêm entry mới và ghi "Thay thế D-xx".

Các ADR chi tiết nằm ở [docs/design/ARCHITECTURE.md](design/ARCHITECTURE.md) (ADR-1 đến ADR-18); file này chỉ là mục lục và các quyết định áp dụng khi adopt.

## D-01 · 2026-11-25 · Áp project-kit ở Mode 1

- Bối cảnh: repo đã có AGENTS.md, docs/design/ và CI; cần một lệnh kiểm tra duy nhất và file handoff cho việc đổi tool.
- Chọn: Mode 1. Giữ nguyên AGENTS.md, SPEC và ADR hiện có; thêm CLAUDE.md, .agent/rules/project.md, scripts/check, docs/HANDOFF.md.
- Vì: repo chưa dùng worktree song song; Mode 2 chưa cần.
- Đã loại: Mode 2 (không có nhiều phiên song song trên nhánh riêng); tạo docs/SPEC.md và docs/BRIEF.md mới (trùng với docs/design/SPEC.md).
- KB: không có ghi chú liên quan.

## D-02 · 2026-11-26 · Phạm vi P19 (Dịch & Glossary) sau review chéo

- Bối cảnh: trace homeserver (*Malcolm in the Middle*) cho thấy nhân vật bị tách đôi (`Mom`/`Lois`, bug `_Names.add` trong `fold.py`), Relationship state mâu thuẫn Glossary, prompt phình 72 dòng Forms of address. Bản nháp P19a–d đề xuất gộp alias `Mom→Lois` và lọc theo facts tự trích.
- Chọn: P19a–d bản sửa. (1) Danh xưng chung (Mom, Dad, Sir, Grandma…) bị lọc khỏi `name` và `aliases` trước `fold()`; chỉ gộp alias là tên riêng. (2) Hướng quan hệ đổi từ enum `a_above`/`b_above` sang `senior: <tên> | equal`. (3) TMDB Credits theo tập (cast + guest_stars, 0 model call) là nền của P19b/c: chính tả chuẩn và phạm vi lọc Forms of address. (4) Harness hồi quy: diff `subcore render-prompt` trước/sau (0 call) + A/B nhỏ ~30 câu xưng hô trên S02E04. (5) LLM-as-a-Judge đóng băng nguyên trạng (`judge_enabled = false`, không sửa code).
- Vì: "Mom" là danh xưng tương đối (Lois gọi Ida là "Mom"); gộp làm alias toàn cục quy mọi "X & Mom" về Lois (đúng lỗi `Hal ↔ Mom` S05E15). Các dòng "Malcolm is the senior" sai cùng chiều, khả năng model đảo enum hướng. Lọc theo facts tự trích mất rule khi extract bỏ sót nhân vật. Không đo trước/sau thì không biết P19 có sửa được xưng hô.
- Đã loại: gộp alias `Mom→Lois` (bản nháp); TMDB tuỳ chọn cuối P19d; ẩn hoặc gỡ Judge khỏi UI; thêm `speaker`/`addressee` vào output dịch ngay trong P19 (ghi vào backlog PLAN.md, làm nếu harness cho thấy còn sai).
- Giới hạn còn lại: SRT không có người nói; P19 giảm nhiễu prompt nhưng không bảo đảm model đoán đúng ai nói với ai.
