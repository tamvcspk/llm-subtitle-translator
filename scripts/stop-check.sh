#!/usr/bin/env bash
# Claude Code Stop hook: chạy scripts/check khi có thay đổi chưa commit.
# Thoát 0 nếu stop_hook_active=true (tránh lặp vô hạn) hoặc workspace sạch.
# Thoát 2 (chặn dừng) nếu scripts/check lỗi.
set -u

input=""
if [ ! -t 0 ]; then
  input="$(cat)"
fi

if printf '%s' "$input" | grep -q '"stop_hook_active"[[:space:]]*:[[:space:]]*true'; then
  exit 0
fi

cd "$(dirname "$0")/.." || exit 0

if [ -z "$(git status --porcelain 2>/dev/null)" ]; then
  exit 0
fi

runner="${BASH:-bash}"
if ! out="$("$runner" scripts/check 2>&1)"; then
  printf '%s\n' "$out" | tail -n 40 >&2
  echo "stop-check: scripts/check failed; fix errors before stopping." >&2
  exit 2
fi

exit 0
