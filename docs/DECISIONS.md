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

## D-03 · 2026-11-27 · Đích của P19 sau khi đo mốc: prompt gọn, không phải điểm xưng hô

- Bối cảnh: P19-0 chấm tay 66 câu xưng hô trên ba tập thật (S02E04, S01E03, S01E05). Bản dịch đang chạy (Prod) sai 6/66; chạy lại cùng bundle sai 9/66, và chỉ 10/34 câu giống hệt nhau giữa hai lần chạy. 5/6 lỗi của Prod có rule đúng trong Glossary. Prompt mốc: 72 đến 152 dòng Forms of address, 7/7 đến 18/23 cặp Relationship state trùng rule Glossary.
- Chọn: tiêu chí P19b/P19c là prompt (số dòng Forms of address, 0 cặp trùng, không còn `Mom`/`Dad` làm nhân vật, token) cộng điều kiện không tệ hơn mốc (≤ 9/66 sai khi chấm tay, ngoài CI). Không hứa điểm xưng hô cao hơn rõ rệt. Spec ở SPEC v0.22 (FR-5.9 đến FR-5.12, tiêu chí 25, quyết định #30).
- Vì: độ ồn của model gần bằng tỉ lệ lỗi, nên không phân biệt được cải thiện nhỏ bằng 66 câu; nhiễu và mâu thuẫn trong prompt là thứ đo được và sửa được bằng 0 lời gọi.
- Đã loại: đo bằng so chuỗi; hứa giảm số câu sai; mở `speaker`/`addressee` ngay (backlog, SPEC Non-goals) vì mốc chưa cho thấy xưng hô sai nhiều.
- Giới hạn: bộ golden không khó (mốc 91% đúng); muốn chứng minh P19 sửa xưng hô cần bộ câu mà Relationship state đè sai lên Glossary. Chưa có.
