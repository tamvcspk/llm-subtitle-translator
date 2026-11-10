# subcore

Subtitle translation engine for `llm-subtitle-translator`. Pure library: it knows
nothing about Bazarr, the queue, or HTTP.

Status: skeleton (P0). Planned modules (P1+, see `docs/design/ARCHITECTURE.md`):

- `srt` — parse/serialize, tag validation
- `providers` — `Provider` protocol, Gemini, OpenAI-compatible, fake
- `translate` — planner, prompt builder, validate/repair loop
- `memory` — facts extraction, fold, compression, `context_hash`
- `glossary` — YAML load/merge/validate, pending suggestions
- `cli` — manual translation and debugging

Tests use stdlib `unittest` and never call the network:

```bash
uv run --locked python -m unittest discover -s packages/subcore/tests -t packages/subcore -v
```
