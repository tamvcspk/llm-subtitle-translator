# Gemini SRT Translator for Bazarr

A small Docker sidecar for Bazarr that turns extracted subtitle files into
gemini-srt-translator jobs.

The worker is designed for media servers where Bazarr can already extract or
index source subtitles, but the final translated subtitles still need to be
created automatically. It listens for Bazarr post-processing events, exposes a
small web console, queues translation work, calls the `gst` CLI from
[`gemini-srt-translator`](https://github.com/MaKTaiL/gemini-srt-translator),
writes target-language `.srt` files next to the media, then asks Bazarr to scan
the matching item again.

## Features

- Bazarr post-processing integration for embedded subtitles in configured source
  languages.
- Web console for status, wanted items, local scans, queue management, settings,
  system backups, and logs.
- Settings page shows configured secrets as `**********` and provides API-key
  test buttons for Bazarr, Gemini, and TMDB.
- gemini-srt-translator options are configurable in the web console, including
  model selection from a dropdown, batch size, quota mode, logs, token reports,
  and model tuning parameters.
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
Bazarr Embedded Subtitles provider
        |
        | extracts *.en.srt, *.ja.srt, ...
        v
Bazarr custom post-processing
        |
        | writes queue JSON
        v
Gemini SRT Translator for Bazarr
        |
        | gst translate -i input.<source>.srt -l target -o output.<target>.srt
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
- A Gemini API key for `gemini-srt-translator`.
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
  -v /opt/docker/bazarr/postprocess:/bazarr-postprocess \
  -v /opt/docker/gemini-srt-translator-bazarr:/state \
  -v /opt/docker/bazarr/postprocess/queue:/queue \
  ghcr.io/tamvcspk/llm-subtitle-translator:latest
```

The container already defaults to `/state`, `/queue`,
`/bazarr-postprocess/targets.json`, and web port `6789`, so the direct
`docker run` form only needs the host paths and public settings.
The console requires a login (`CONSOLE_USER` / `CONSOLE_PASSWORD`, HTTP Basic
Auth); without them it answers `503` while the queue worker keeps running. See
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

Secret values are stored in `/state/config.json` and are not returned by the
public settings API.

gemini-srt-translator settings such as model, batch size, paid quota mode,
progress/thought logs, token reports, and model tuning options are also stored in
`/state/config.json`.

Create the shared post-processing directory and install the enqueue script:

```bash
sudo mkdir -p /opt/docker/bazarr/postprocess
sudo cp bazarr-postprocess/gst_enqueue.sh /opt/docker/bazarr/postprocess/gst_enqueue.sh
sudo chmod +x /opt/docker/bazarr/postprocess/gst_enqueue.sh
```

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

- `.env`: deployment defaults and Docker path mapping.
- `/state/config.json`: web-console settings, including Bazarr/Gemini/TMDB
  secrets if configured through the UI.

The web console can create manual backups similar to the *arr `System ->
Backups` flow. It also creates a scheduled backup every 7 days and removes
backup archives older than 30 days. Backup archives are written to:

```text
/opt/docker/gemini-srt-translator-bazarr/backups
```

They include:

- `/state/config.json`
- `/bazarr-postprocess/targets.json`
- `backup.json` metadata

Because the console currently has no authentication, keep it on a trusted
network. Backup archives can be downloaded from the UI and imported back through
the System page. Import restores `/state/config.json` and
`/bazarr-postprocess/targets.json`, creates a `pre-import` backup first, and
does not restore queue files, logs, or caches.

Also back up `.env` separately because it is Docker deployment configuration and
is not mounted inside the worker container:

```bash
tar -czf gemini-srt-translator-bazarr-backup.tgz \
  /opt/docker/gemini-srt-translator-bazarr/.env \
  /opt/docker/gemini-srt-translator-bazarr/config.json \
  /opt/docker/bazarr/postprocess/targets.json
```

Queue files and logs are runtime state rather than required configuration.

## Bazarr Setup

In Bazarr, enable `Settings -> Subtitles -> Use Custom Post-Processing` and use
this command:

```bash
/config/postprocess/gst_enqueue.sh "{{episode}}" "{{subtitles}}" "{{subtitles_language_code2}}" "{{provider}}" "{{series_id}}" "{{episode_id}}" 2>&1
```

The enqueue script intentionally accepts only:

- provider: `embeddedsubtitles`
- source subtitle language: one of the configured source languages
- missing target output files, for example no existing `.zh.srt`

See [docs/BAZARR.md](docs/BAZARR.md) for detailed setup notes.

## Languages

The default source is English and the default target is Simplified Chinese:

```json
{
  "source_languages": [
    {"code": "en", "language": "English", "enabled": true}
  ],
  "target_languages": [
    {"code": "zh", "language": "Simplified Chinese", "enabled": true}
  ]
}
```

Use the web console settings page to choose source and target languages from
Bazarr's supported language list. Each language has:

- `code`: filename language code, such as `zh`, `ja`, `ko`, `es`.
- `language`: value passed to `gst translate -l`, such as `Japanese`.
- `enabled`: `true` or `false`.

Example output:

```text
Movie.en.srt -> Movie.zh.srt
Movie.ja.srt -> Movie.zh.srt
Movie.zh.srt -> Movie.en.srt
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Bazarr integration](docs/BAZARR.md)
- [Configuration](docs/CONFIGURATION.md)
- [HTTP API](docs/API.md)
- [Development](docs/DEVELOPMENT.md)
- [Security](SECURITY.md)
- [Contributing](CONTRIBUTING.md)

## Project Status

This project is focused on one workflow: use gemini-srt-translator to translate
SRT files that Bazarr can already see or extract. It is not a full
subtitle provider replacement and it does not install anything inside the Bazarr
image.

## License

MIT. See [LICENSE](LICENSE).

Runtime translation depends on
[`gemini-srt-translator`](https://github.com/MaKTaiL/gemini-srt-translator),
which is licensed separately by its upstream authors.
