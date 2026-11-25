# Quyết định kỹ thuật

Chỉ thêm, không sửa entry cũ. Đổi quyết định thì thêm entry mới và ghi "Thay thế D-xx".

Các ADR chi tiết nằm ở [docs/design/ARCHITECTURE.md](design/ARCHITECTURE.md) (ADR-1 đến ADR-18); file này chỉ là mục lục và các quyết định áp dụng khi adopt.

## D-01 · 2026-11-25 · Áp project-kit ở Mode 1

- Bối cảnh: repo đã có AGENTS.md, docs/design/ và CI; cần một lệnh kiểm tra duy nhất và file handoff cho việc đổi tool.
- Chọn: Mode 1. Giữ nguyên AGENTS.md, SPEC và ADR hiện có; thêm CLAUDE.md, .agent/rules/project.md, scripts/check, docs/HANDOFF.md.
- Vì: repo chưa dùng worktree song song; Mode 2 chưa cần.
- Đã loại: Mode 2 (không có nhiều phiên song song trên nhánh riêng); tạo docs/SPEC.md và docs/BRIEF.md mới (trùng với docs/design/SPEC.md).
- KB: không có ghi chú liên quan.
