"""A stand-in for Bazarr's API, only for ``scripts/preview.ps1 -Bazarr``: it lists the demo library of the preview seed
(Arcane Demo with three episodes, Second Demo, Demo Movie) so Sync now has something to read, and it can be told to
answer with a much smaller library so the console's shrink guard can be tried.

    python scripts/fake_bazarr.py --media local-state/preview/preview-media [--port 6791]

Control, from a browser address bar (GET, no body):
    /__fake/shrink    the next reads list only 2 of the 6 items
    /__fake/restore   the full library again
    /__fake/state     which of the two it answers now

Only what the worker reads is served: /api/series, /api/episodes, /api/movies (paged with start and length, with
``total``) and /api/system/languages. No key is checked. Nothing is ever written. Listens on 127.0.0.1 only.
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SHRUNK = {"on": False}


def _sub(code: str, path: Path) -> dict:
    return {"code2": code, "name": code, "path": str(path), "forced": False, "hi": False}


def _library(media: Path) -> tuple[list[dict], dict[str, list[dict]], list[dict]]:
    show = media / "Arcane Demo" / "Season 1"
    episodes: list[dict] = []
    for n in (1, 2, 3):
        stem = f"Arcane.Demo.S01E{n:02d}"
        subs = [_sub("en", show / f"{stem}.en.srt")]
        vi = show / f"{stem}.vi.srt"
        if vi.is_file():
            subs.append(_sub("vi", vi))
        episodes.append({
            "sonarrEpisodeId": 100 + n, "season": 1, "episode": n, "title": f"Episode {n}", "path": str(show / f"{stem}.mkv"),
            "subtitles": subs, "missing_subtitles": [] if vi.is_file() else [{"code2": "vi", "forced": False}],
        })
    movie_dir = media / "Demo Movie (2020)"
    movie = {
        "radarrId": 55, "title": "Demo Movie", "path": str(movie_dir / "Demo.Movie.mkv"),
        "subtitles": [_sub("en", movie_dir / "Demo.Movie.en.srt")], "missing_subtitles": [{"code2": "vi", "forced": False}],
    }
    series = [{"sonarrSeriesId": 7, "title": "Arcane Demo"}, {"sonarrSeriesId": 9, "title": "Second Demo"}]
    by_series = {"7": episodes, "9": []}
    bdir = media / "Bootstrap Demo" / "Season 1"
    if bdir.is_dir():
        beps = []
        for n in (1, 2, 3):
            stem = f"Bootstrap.Demo.S01E{n:02d}"
            beps.append({
                "sonarrEpisodeId": 200 + n, "season": 1, "episode": n, "title": f"Episode {n}", "path": str(bdir / f"{stem}.mkv"),
                "subtitles": [_sub("en", bdir / f"{stem}.en.srt")], "missing_subtitles": [{"code2": "vi", "forced": False}],
            })
        series.append({"sonarrSeriesId": 12, "title": "Bootstrap Demo"})
        by_series["12"] = beps
    if SHRUNK["on"]:  # 2 items of 6: far past the guard
        return series[:1], {"7": episodes[:1]}, []
    return series, by_series, [movie]


def make_handler(media: Path):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: object, content_type: str = "application/json") -> None:
            raw = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            query = parse_qs(url.query)
            series, episodes, movies = _library(media)
            if url.path == "/__fake/shrink":
                SHRUNK["on"] = True
                return self._send(200, "shrunk: the next reads list 2 of 6 items\n", "text/plain")
            if url.path == "/__fake/restore":
                SHRUNK["on"] = False
                return self._send(200, "restored: the full library\n", "text/plain")
            if url.path == "/__fake/state":
                return self._send(200, "shrunk\n" if SHRUNK["on"] else "full\n", "text/plain")
            if url.path == "/api/series":
                return self._send(200, self._page(series, query))
            if url.path == "/api/movies":
                return self._send(200, self._page(movies, query))
            if url.path == "/api/episodes":
                ids = query.get("seriesid[]") or []
                return self._send(200, {"data": [e for i in ids for e in episodes.get(i, [])]})
            if url.path == "/api/system/languages":
                return self._send(200, [{"code2": "en", "name": "English", "enabled": True},
                                        {"code2": "vi", "name": "Vietnamese", "enabled": True}])
            self._send(404, {"error": "not served by the fake Bazarr"})

        @staticmethod
        def _page(items: list[dict], query: dict[str, list[str]]) -> dict:
            start = int((query.get("start") or ["0"])[0])
            length = int((query.get("length") or ["100"])[0])
            return {"data": items[start:start + length], "total": len(items)}

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            print(f"fake-bazarr {self.address_string()} {format % args}", flush=True)

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--media", required=True, help="the preview's preview-media folder")
    parser.add_argument("--port", type=int, default=6791)
    args = parser.parse_args()
    media = Path(args.media).resolve()
    print(f"fake Bazarr on http://127.0.0.1:{args.port} for {media}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(media)).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
