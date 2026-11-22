# subcore

Subtitle translation engine for `llm-subtitle-translator`. Pure library: it knows
nothing about Bazarr, the queue, or HTTP.

Status: P1 done in code (SRT, providers, translator, glossary, CLI); memory (`memory`) in P4; the
OpenAI-compatible provider in P7; the bundle and the declarative CLI in P11; style, character cards and few-shot in P12.
Roadmap: `PLAN.md`, design: `docs/design/ARCHITECTURE.md`.

| Module | Role |
|---|---|
| `srt` | parse/serialize (BOM, CRLF, empty cues, odd indexes), tag validation, atomic write |
| `providers` | `Provider` protocol, typed errors (`RateLimited`, `QuotaExhausted`, `Unavailable`, `BadOutput`, `AuthError`, `ModelNotFound`), `gemini`, `openai_compat` (OpenRouter, Ollama, LM Studio, vLLM; standard library only) and `fake` adapters |
| `bundle` | the bundle a translation reads (plain data, `bundle_version` 2, `bundle_hash`), `matches` for display |
| `style` | a style as data (axes, subtitle limits, notes), `resolve_style` to lay layers on each other, character cards, genre presets, `render_style`/`render_voices` for the prompt |
| `fewshot` | `select`: the closest corrected lines of a series to the text being translated, within a token budget (lexical, no embeddings) |
| `translate` | engine: `make_bundle` → `run` (planner, prompt renderer, validate → repair → split), `render_requests`, `review` (shared with the worker) |
| `matching` | the one test of "does this line mention that name" (CJK and Korean anywhere in the text, other names as whole words with up to two letters of ending); the glossary filter, character cards, locked terms and the console all ask it |
| `compare` | side-by-side numbers for two configurations or a golden set |
| `golden` | the golden set of a series (`golden.json`: the cues an admin corrected and the text they should have), read by `compare --golden` |
| `feedback` | what an admin keeps correcting: the same swap of words in several corrected cues, and the source word they share (pure; the worker turns it into a suggestion) |
| `glossary` | YAML load/merge/dump/validate, `from_doc`/`to_doc` (the document the worker stores), locked terms, timed address rules |
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
uv run subcore compare Movie.en.srt --bundle a.json --bundle b.json --golden show.vi.golden.json   # scores only the cues the admin corrected
uv run subcore check-glossary glossary.yaml
```

The API key comes from `--api-key`, then the environment variable (`--api-key-env`; default `GEMINI_API_KEY` for
`gemini` and `OPENAI_COMPAT_API_KEY` for `openai_compat`, so a Gemini key is never sent to another host), then
`--api-key-file` (first non-empty line). An existing subtitle is never overwritten unless you pass `--force`; the report
of a run (`<output>.report.json`) is replaced each time, one named with `--report` needs `--force` like the subtitle.
Any OpenAI-compatible server works with `--provider openai_compat --base-url ... --model ...`.

A bundle written by `subcore bundle` leaves the model and the output limit unset unless `--model` or
`--max-output-tokens` says otherwise: the provider that runs it supplies them, and the hash is the same before and after
the run. `validate` narrows `--glossary` with `--season`/`--episode` (or takes a `--bundle`'s own glossary), as the worker does.
`compare` builds every provider before it pays for the first run and prints what it got even if the other side fails.

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

## Style

A bundle carries the **effective** style (the layers already laid on each other) and the cards of the characters the
dialogue names, and `render` puts them in two sections after the glossary: *Effective style* and *Character voices*. The
glossary section comes first and says it is authoritative. With more than `glossary_filter_threshold` terms (a bundle
parameter, default 50) each request lists only the terms its lines mention; forms of address and voices are never filtered
by words. The CLI runs a bundle the worker exported, so it reproduces a style exactly; it does not build a style by itself.

## Tests

Stdlib `unittest`, never the network (fake provider):

```bash
uv run --locked python -m unittest discover -s packages/subcore/tests -t packages/subcore -v
```
