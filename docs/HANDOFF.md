# Handoff

Ghi đè toàn bộ file mỗi lần chuyển tool. Tối đa 30 dòng.

- Thời điểm: 2026-11-25
- Nhánh: main
- Kiểm tra CI (10/10 bước Python + web): OK (exit 0)

## Mục tiêu đang làm

Hoàn tất áp project-kit Mode 1 vào repo (`chore: adopt project-kit`). Roadmap và spec gốc ở `PLAN.md` và `docs/design/`.

## Đã xong

- Thêm `CLAUDE.md`, `.agent/rules/project.md`, `scripts/check`, `scripts/stop-check.sh`, `docs/HANDOFF.md`, `docs/DECISIONS.md`.
- Bổ sung mục kit vào `AGENTS.md` và Stop hook vào `.claude/settings.json`.
- Chạy toàn bộ 10 bước CI (Python `unittest`, `compileall`, `subcore`, `openapi --check`, `doccheck` + web `typecheck`, `lint`, `vitest`, `build`) từ `C:/Src/llm-subtitle-translator`: exit 0.

## Việc tiếp theo

- Tiếp tục công việc theo `PLAN.md` và `docs/design/SPEC.md`.

## Lưu ý

- Không đọc hay ghi giá trị trong `.env` (đã git-ignore).
- Luật gốc nằm trong `AGENTS.md` và `docs/design/SPEC.md`; không sửa entry cũ trong `docs/design/ARCHITECTURE.md`.
