# Gemini SRT Translator for Bazarr

A small Docker sidecar for Bazarr that turns extracted subtitle files into
LLM translation jobs.

The worker is designed for media servers where Bazarr can already extract or
index source subtitles, but the final translated subtitles still need to be
created automatically. It reads the library from Bazarr's API, exposes a
small web console where you request translations (or switch on auto-request),
queues the work, translates in-process with the
`subcore` engine (Gemini, with rate limiting, retries, cue validation and
repair), writes target-language `.srt` files next to the media, then asks Bazarr
to scan the matching item again. Every published file is versioned with a
backup, so a retranslation can be reverted.

## Features

- The worker pulls the library from Bazarr (series, movies, episodes and the
  languages each still wants) and shows what each language is doing, as Missing,
  Translated, Edited, External, Requested, Ignored, and so on. Nothing is
  translated until you ask in the Library, or switch on auto-request for a
  series (or everything).
- Web console (React, built for a tablet held upright) for the library,
  activity and the queue, settings, system backups, and logs.
- Glossary, style and character cards per series or movie, kept in the database.
  Story memory reads each episode's facts so later episodes know the earlier
  ones; a translation made without the current rules is flagged Stale, never
  redone by itself.
- A **Review** inbox of suggestions (forms of address, names, characters) drawn
  from the facts and from your own corrections; nothing is applied until you
  accept it.
- **Insights**: how the worker is doing and how good its translations are, above
  all the share of cues you had to correct, per model, prompt version and style
  trial. Cues you corrected form a **golden set** per series and language
  (`GET /api/series/{id}/golden`), and `subcore compare --golden` scores a
  change of prompt or style against it before you adopt it. Words you correct
  the same way in three cues become a suggestion (no model call).
- An optional **LLM judge** (off by default, `judge_enabled`): on the Compare
  page of a candidate it says cue by cue which text reads better. It is advice
  only: it ticks nothing, edits nothing, and does not count in the style-trial
  scores, which only your own picks feed.
- Settings page shows configured secrets as `**********` and provides API-key
  test buttons for Bazarr, Gemini, and TMDB.
- Translation options are configurable in the web console, including model
  selection from a dropdown (a light default model plus a strong model for hard
  jobs), quota mode, and temperature.
  `LLM_RPM`/`LLM_TPM`/`LLM_RPD` cap the request rate per API key.
- Multi-source and multi-target output support, for example `en -> zh`,
  `ja -> zh`, `zh -> en`.
- Single-worker queue so Gemini quota is not consumed by uncontrolled parallel
  jobs.
- New jobs wait for a configurable settle window, default 10 minutes, so Bazarr
  can finish extracting other embedded subtitles before translation starts.
- Logs page can clear the worker log from the console.
- Optional TMDB context for better movie, show, and episode descriptions.
- Bazarr refresh after successful translation with per-series/per-movie scan
  when IDs are available.
- Docker-first deployment. No Python packages are installed into the Bazarr
  container.
- The web console listens on `6789` inside and outside the container.
- Small default image: FFmpeg is optional and disabled by default because Bazarr
  performs subtitle extraction.

## How It Works

```text
Bazarr (library, Embedded Subtitles provider)
        ^
        | the worker reads series, episodes, movies, missing languages
        | (every few minutes and on "Sync now"); nothing is pushed
        |
Library: you request, or auto-request asks
        |
        | jobs in the worker's database
        v
Gemini SRT Translator for Bazarr
        |
        | subcore: translate input.<source>.srt -> output.<target>.srt
        v
translated subtitle beside media
        |
        | PATCH scan-disk / fallback scan task
        v
Bazarr sees the new local subtitle
```

## Requirements

- Docker Compose.
- Bazarr with API access.
- Bazarr media paths and this worker's `/media` mount must point at the same
  files.
- A Gemini API key.
- Optional TMDB API key for richer translation context.

## Docker Image

The default image is published to GitHub Container Registry:

```text
ghcr.io/tamvcspk/llm-subtitle-translator:latest
```

The image is published for `linux/amd64` and `linux/arm64`, including Raspberry
Pi 4 running a 64-bit OS. `latest` is rebuilt from the `main` branch by GitHub
Actions when image-related files change. After a new image is published, update
a Docker host with:

```bash
docker compose pull
docker compose up -d
```

For a direct Docker install without Compose:

```bash
docker pull ghcr.io/tamvcspk/llm-subtitle-translator:latest
docker rm -f gemini-srt-translator-bazarr 2>/dev/null || true
docker run -d \
  --name gemini-srt-translator-bazarr \
  --restart unless-stopped \
  -p 6789:6789 \
  -e CONSOLE_USER=admin \
  -e CONSOLE_PASSWORD='choose-a-password' \
  -v /mnt/data:/media \
  -v /opt/docker/bazarr/config:/bazarr-config:ro \
  -v /opt/docker/gemini-srt-translator-bazarr:/state \
  ghcr.io/tamvcspk/llm-subtitle-translator:latest
```

The container already defaults to `/state` and web port `6789`, so the direct
`docker run` form only needs the host paths and public settings. (When upgrading
from a version that used Bazarr's post-processing script, also mount the old
queue folder, `-v /opt/docker/bazarr/postprocess/queue:/queue`, for the first
start only: the worker settles it into its database once. See
[docs/BAZARR.md](docs/BAZARR.md).)
The console requires a login (a form for browsers, HTTP Basic Auth for scripts).
`CONSOLE_USER` / `CONSOLE_PASSWORD` create the account on the first run; change
the password in Settings afterwards. Without them (and no stored account) it
answers `503` while the queue worker keeps running. See
[Console Authentication](docs/CONFIGURATION.md#console-authentication).
Open `http://<docker-host>:6789/`, sign in, and enter the Bazarr, Gemini, and
TMDB settings in the web console.

If you prefer to seed values from a file, create
`/opt/docker/gemini-srt-translator-bazarr/.env` first, then add this option to
the `docker run` command:

```bash
--env-file /opt/docker/gemini-srt-translator-bazarr/.env
```

If `BAZARR_URL` uses a container hostname such as `http://bazarr:6767`, attach
this container to the same Docker network as Bazarr, for example with
`--network <bazarr-network>`. Otherwise set `BAZARR_URL` to a LAN URL such as
`http://192.168.1.10:6767`.

## Quick Start

Clone or copy this project to the Docker host, for example:

```bash
sudo mkdir -p /opt/docker/gemini-srt-translator-bazarr
cd /opt/docker/gemini-srt-translator-bazarr
```

Create the environment file:

```bash
cp .env.example .env
nano .env
```

Set at least one Gemini API key. You can put it in `.env` before first start:

```dotenv
GEMINI_API_KEY=<gemini-api-key>
```

Or open the web console and fill these fields in Settings:

- Bazarr URL
- Bazarr API key
- Gemini API key 1
- Gemini API key 2
- TMDB API key

The keys in `.env` only seed the worker on its first run. After that the keys and
every setting (model, paid quota mode, languages, ...) live in the worker's database
(`/state/app.db`), you change them in Settings, and a change is live at once with no
restart. The API returns only the last four characters of a key. An older
`/state/config.json` is imported once on the first start and is not read again.

Start the worker:

```bash
docker compose pull
docker compose up -d
```

For a local source build instead of the published GHCR image, run
`docker compose up -d --build`.

Set `CONSOLE_USER` and `CONSOLE_PASSWORD` in `.env` first (the console is
locked until you do), then open the console:

```text
http://<docker-host>:6789/
```

## Configuration Storage and Backup

The worker has two configuration layers:

- `.env`: deployment defaults, Docker path mapping, and the first-run seed of the API keys.
- `/state/app.db`: web-console settings and the Bazarr/Gemini/TMDB API keys.

The web console can create manual backups similar to the *arr `System ->
Backups` flow. It also creates a scheduled backup every 7 days and removes
backup archives older than 30 days. Backup archives are written to:

```text
/opt/docker/gemini-srt-translator-bazarr/backups
```

They include:

- the saved settings (`config/config.json` in the zip), **without any API key**
  (tick "Include the API keys" on the Backups page to add them; scheduled backups never do)
- the language list file the old Bazarr script read (`postprocess/targets.json`; a leftover, no longer used)
- `backup.json` metadata

The login is not encrypted, so keep the console on a trusted network or behind
a TLS reverse proxy. Backup archives can be downloaded from the UI and imported back through
the System page. Import restores the settings (and the keys, when the backup has them),
never the console account, creates a `pre-import` backup first, and does not restore
logs or caches.

Because a backup holds no key, back up `.env` and the database separately; `.env` is
Docker deployment configuration and is not mounted inside the worker container:

```bash
tar -czf gemini-srt-translator-bazarr-backup.tgz \
  /opt/docker/gemini-srt-translator-bazarr/.env \
  /opt/docker/gemini-srt-translator-bazarr/app.db
```

Jobs are rows of the database; logs are runtime state rather than required configuration.

## Bazarr Setup

Enter Bazarr's address and API key in Settings. That is all: Bazarr needs no
post-processing command and no shared script folder. Open **Library**, press
**Sync now**, and request translations, or switch on **Auto-request** for a
series. See [docs/BAZARR.md](docs/BAZARR.md) for how the worker reads the
library, what a request costs, and how to remove the old post-processing
command when upgrading.

## Languages

The default source is English and the default target is Vietnamese:

```json
{
  "source_languages": [
    {"code": "en", "language": "English", "enabled": true}
  ],
  "target_languages": [
    {"code": "vi", "language": "Vietnamese", "enabled": true}
  ]
}
```

Use the web console settings page to choose source and target languages from
Bazarr's supported language list. Each language has:

- `code`: filename language code, such as `zh`, `ja`, `ko`, `es`.
- `language`: the language name given to the model, such as `Japanese`.
- `enabled`: `true` or `false`.

Example output:

```text
Movie.en.srt -> Movie.vi.srt
Movie.ja.srt -> Movie.vi.srt
Movie.vi.srt -> Movie.en.srt
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Bazarr integration](docs/BAZARR.md)
- [Configuration](docs/CONFIGURATION.md)
- [HTTP API](docs/API.md)
- [Measuring translation quality](docs/QUALITY.md) (golden set, `subcore compare`, the judge)
- [Development](docs/DEVELOPMENT.md)
- [Security](SECURITY.md)
- [Contributing](CONTRIBUTING.md)

## Project Status

This project is focused on one workflow: translate SRT files that Bazarr can
already see or extract, with the `subcore` engine in `packages/subcore` (also
usable on its own through the `subcore` command). It is not a full subtitle
provider replacement and it does not install anything inside the Bazarr image.
The roadmap and its state are in [PLAN.md](PLAN.md).

## License

MIT. See [LICENSE](LICENSE).

This is a fork of
[`gemini-srt-translator-bazarr`](https://github.com/kingzleshe/gemini-srt-translator-bazarr).
Translation runs through the `google-genai` SDK (or an OpenAI-compatible
endpoint), which are licensed separately by their authors.
