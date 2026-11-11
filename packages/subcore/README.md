# subcore

Subtitle translation engine for `llm-subtitle-translator`. Pure library: it knows
nothing about Bazarr, the queue, or HTTP.

Status: P1 done in code (SRT, providers, translator, glossary, CLI). Memory (`memory`)
arrives in P4. Roadmap: `PLAN.md`, design: `docs/design/ARCHITECTURE.md`.

| Module | Role |
|---|---|
| `srt` | parse/serialize (BOM, CRLF, empty cues, odd indexes), tag validation, atomic write |
| `providers` | `Provider` protocol, typed errors (`RateLimited`, `QuotaExhausted`, `Unavailable`, `BadOutput`, `AuthError`), `gemini` and `fake` adapters |
| `translate` | planner (split at silences), prompt builder, validate → repair (2 rounds) → split |
| `glossary` | YAML load/merge/dump/validate, locked terms, timed address rules |
| `cli` | manual translation and debugging |

## CLI

```bash
export GEMINI_API_KEY=...            # never passed on the command line
uv run subcore translate Movie.en.srt --glossary glossary.yaml --target vi --source en \
    --title "Show" --season 1 --episode 6
uv run subcore translate Movie.en.srt --provider fake      # dry run, no network
uv run subcore check-glossary glossary.yaml
```

The output is `Movie.vi.srt` beside the input. An existing file is never overwritten
unless you pass `--force`.

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
