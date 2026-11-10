FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /uvx /bin/

LABEL org.opencontainers.image.title="Gemini SRT Translator for Bazarr" \
      org.opencontainers.image.description="A Bazarr companion app that translates configured source subtitles with gemini-srt-translator." \
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
COPY apps/worker/pyproject.toml /app/apps/worker/pyproject.toml
COPY packages/subcore/pyproject.toml /app/packages/subcore/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package llm-subtitle-worker

COPY apps/worker/worker.py /app/worker.py
COPY apps/worker/gst_worker /app/gst_worker
COPY apps/worker/static /app/static

CMD ["python", "/app/worker.py"]
