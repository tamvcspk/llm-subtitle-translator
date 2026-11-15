# subcore

Subtitle translation engine for `llm-subtitle-translator`. Pure library: it knows
nothing about Bazarr, the queue, or HTTP.

Status: P1 done in code (SRT, providers, translator, glossary, CLI); memory (`memory`) in P4; the
OpenAI-compatible provider in P7; the bundle and the declarative CLI in P11. Roadmap: `PLAN.md`, design: `docs/design/ARCHITECTURE.md`.

| Module | Role |
|---|---|
| `srt` | parse/serialize (BOM, CRLF, empty cues, odd indexes), tag validation, atomic write |
| `providers` | `Provider` protocol, typed errors (`RateLimited`, `QuotaExhausted`, `Unavailable`, `BadOutput`, `AuthError`, `ModelNotFound`), `gemini`, `openai_compat` (OpenRouter, Ollama, LM Studio, vLLM; standard library only) and `fake` adapters |
| `bundle` | the bundle a translation reads (plain data, `bundle_version`, `bundle_hash`), `matches` for display |
| `translate` | engine: `make_bundle` → `run` (planner, prompt renderer, validate → repair → split), `render_requests`, `review` (shared with the worker) |
| `compare` | side-by-side numbers for two configurations or a golden set |
| `glossary` | YAML load/merge/dump/validate, locked terms, timed address rules |
| `cli` | manual translation and debugging |

## CLI

Every translating command works from a bundle: `--bundle FILE` loads one, `bundle` writes one, and flags given
with a bundle override it.

```bash
export GEMINI_API_KEY=...            # or --api-key-file key.txt; a flag shows in ps and shell history
uv run subcore translate Movie.en.srt --glossary glossary.yaml --target vi --source en \
    --title "Show" --season 1 --episode 6                    # -> Movie.vi.srt + Movie.vi.report.json
uv run subcore bundle Movie.en.srt --glossary glossary.yaml --title "Show" -o job.json
uv run subcore translate Movie.en.srt --bundle job.json --provider fake   # the same bundle, the same output
uv run subcore render-prompt --bundle job.json           # the requests it would send; no key, no call
uv run subcore validate Movie.en.srt Movie.vi.srt --glossary glossary.yaml   # exit 1 on review errors
uv run subcore compare Movie.en.srt --bundle a.json --bundle b.json --provider fake
uv run subcore compare Movie.en.srt --bundle a.json --golden reviewed.vi.srt
uv run subcore check-glossary glossary.yaml
```

The API key comes from `--api-key`, then the environment variable (`--api-key-env`, default `GEMINI_API_KEY`),
then `--api-key-file` (first non-empty line). An existing output file is never overwritten unless you pass
`--force`. Any OpenAI-compatible server works with `--provider openai_compat --base-url ... --model ...`.

`subcore` never imports the worker, SQLite or the HTTP server (`tests/test_boundary.py`). The worker's job spec
(`GET /api/jobs/{id}/spec`) is a bundle this CLI runs unchanged.

## Glossary

```yaml
version: 1
target_lang: vi
style: "Informal; keep honorifics"
terms:
  - source: Kyber crystal
    target: tinh thể Kyber
    aliases: [Kyber crystals]
    locked: true          # cues containing the term must contain the translation
characters:
  - {name: Alice, gender: f}
address_rules:
  - a: Alice
    b: Bob
    a_to_b: "em–anh"      # "self–other" when Alice speaks to Bob
    from: "S01E06 00:32:10"   # inclusive; time optional
    until: "S02E01"           # exclusive; optional
```

## Tests

Stdlib `unittest`, never the network (fake provider):

```bash
uv run --locked python -m unittest discover -s packages/subcore/tests -t packages/subcore -v
```
