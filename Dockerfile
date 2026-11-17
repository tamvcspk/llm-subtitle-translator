# Console build. The output is plain files, so it is built once on the build host (no QEMU for arm64)
# and only dist/ reaches the final image: no Node, node_modules or UI sources there (ADR-17).
FROM --platform=$BUILDPLATFORM node:24.22.1-slim AS web

WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json apps/web/.npmrc ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY apps/web/ ./
RUN npm run build

FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /uvx /bin/

LABEL org.opencontainers.image.title="Gemini SRT Translator for Bazarr" \
      org.opencontainers.image.description="A Bazarr companion app that translates configured source subtitles with an in-process LLM engine (subcore)." \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

ARG INSTALL_FFMPEG=false
RUN if [ "$INSTALL_FFMPEG" = "true" ]; then \
      apt-get update \
      && apt-get install -y --no-install-recommends ffmpeg \
      && rm -rf /var/lib/apt/lists/*; \
    fi

# uv workspace: every member's pyproject.toml must be present for --locked.
# Only the worker member is installed; the runtime layout under /app is unchanged.
COPY pyproject.toml uv.lock /app/
COPY apps/server/pyproject.toml /app/apps/server/pyproject.toml
COPY packages/subcore/pyproject.toml packages/subcore/README.md /app/packages/subcore/
# subcore is a workspace dependency of the worker, so its sources are needed to install it.
COPY packages/subcore/src /app/packages/subcore/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package llm-subtitle-server

COPY apps/server/worker.py /app/worker.py
COPY apps/server/subserver /app/subserver
# The console: the React build (and, beside it, the standalone sign-in page from apps/web/public). Only dist/ comes over.
COPY --from=web /web/dist /app/static

CMD ["python", "/app/worker.py"]
