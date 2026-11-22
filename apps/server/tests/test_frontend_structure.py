from pathlib import Path
import re
import tomllib
import unittest


APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parents[1]
WEB = REPO_ROOT / "apps" / "web"
LOGIN = WEB / "public" / "login.html"


class ConsoleDeliveryTests(unittest.TestCase):
    """The console is the React build of apps/web; this member holds no page of its own (ADR-17, P12b)."""

    def test_the_old_console_is_gone(self):
        self.assertFalse((APP_ROOT / "static").exists())

    def test_the_image_gets_only_the_build_and_serves_it_where_the_worker_looks(self):
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        worker_py = (APP_ROOT / "worker.py").read_text(encoding="utf-8")
        final = dockerfile.split("FROM python:", 1)[1]

        self.assertIn("COPY --from=web /web/dist /app/static", dockerfile)
        self.assertNotIn("static-next", dockerfile)
        self.assertNotIn("COPY apps/server/static", dockerfile)
        self.assertIn('os.getenv("STATIC_DIR", "/app/static")', worker_py)
        # Only dist/ crosses into the final stage: no Node, node_modules or sources of the UI.
        for forbidden in ("node:", "npm ", "node_modules", "COPY apps/web"):
            self.assertNotIn(forbidden, final)

    def test_the_sign_in_page_is_the_one_file_outside_the_bundle_and_pulls_in_nothing(self):
        # /login is served without a session, so it must not need any other file (no CDN, no stylesheet, no script src).
        public = sorted(path.name for path in (WEB / "public").iterdir() if path.is_file())
        self.assertEqual(public, ["login.html"])
        html = LOGIN.read_text(encoding="utf-8")
        self.assertEqual(re.findall(r'\s(?:src|href)="([^"]*)"', html), ["data:,"])
        self.assertNotIn("@import", html)
        self.assertNotRegex(html, r"url\(\s*['\"]?(?!data:)")

    def test_what_the_old_console_alone_used_is_gone_from_the_api(self):
        from subserver.domain.config import DEFAULT_APP_CONFIG

        handler = (APP_ROOT / "subserver" / "api" / "web.py").read_text(encoding="utf-8")
        for route in ("/api/wanted", "/api/scan", "/api/enqueue-scan"):
            self.assertNotIn(f'"{route}"', handler)
        self.assertNotIn("scan_limit", DEFAULT_APP_CONFIG)

    def test_a_backup_made_before_scan_limit_went_still_restores(self):
        from subserver.domain.config import normalize_app_config

        self.assertNotIn("scan_limit", normalize_app_config({"scan_limit": 500, "media_roots": ["/m"]}))


class DeploymentConfigTests(unittest.TestCase):
    def test_compose_uses_6789_inside_and_outside_container(self):
        compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        worker_py = (APP_ROOT / "worker.py").read_text(encoding="utf-8")

        self.assertIn("name: gemini-srt-translator-bazarr", compose)
        self.assertIn("WEB_PORT: 6789", compose)
        self.assertIn("- 6789:6789", compose)
        self.assertNotIn("WEB_PORT=", env_example)
        self.assertNotIn("HOST_PORT=", env_example)
        self.assertIn('os.getenv("WEB_PORT", "6789")', worker_py)
        self.assertNotIn(":8080", compose)

    def test_default_state_dir_mounts_project_root_not_nested_state_dir(self):
        compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

        self.assertIn("${WORKER_STATE_DIR:-/opt/docker/gemini-srt-translator-bazarr}:/state", compose)
        self.assertNotIn("${WORKER_STATE_DIR:-/opt/docker/gemini-srt-translator-bazarr/state}:/state", compose)
        self.assertIn("WORKER_STATE_DIR=/opt/docker/gemini-srt-translator-bazarr", env_example)
        self.assertNotIn("WORKER_STATE_DIR=/opt/docker/gemini-srt-translator-bazarr/state", env_example)

    def test_queue_mount_is_outside_worker_state_mount(self):
        compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        worker_py = (APP_ROOT / "worker.py").read_text(encoding="utf-8")

        self.assertIn("QUEUE_DIR: /queue", compose)
        self.assertIn("${BAZARR_POSTPROCESS_DIR:-/opt/docker/bazarr/postprocess}/queue:/queue", compose)
        self.assertNotIn("QUEUE_DIR: /state/queue", compose)
        self.assertNotIn(":/state/queue", compose)
        self.assertIn('os.getenv("QUEUE_DIR", "/queue")', worker_py)

    def test_docker_build_skips_ffmpeg_by_default_after_upstream_fix(self):
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

        self.assertIn("uv sync --locked --no-dev", dockerfile)
        self.assertIn("ARG INSTALL_FFMPEG=false", dockerfile)
        self.assertIn("apt-get install -y --no-install-recommends ffmpeg", dockerfile)
        self.assertIn("INSTALL_FFMPEG: ${INSTALL_FFMPEG:-false}", compose)
        self.assertIn("INSTALL_FFMPEG=false", env_example)

    def test_worker_uses_the_workspace_engine_not_the_gst_cli(self):
        pyproject = tomllib.loads((APP_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        dependencies = pyproject["project"]["dependencies"]

        # The engine, and the HTTP stack of the console (ADR-18): nothing else, and the engine knows none of it.
        self.assertEqual(sorted(re.split(r"[<>=!~ ]", dependency)[0] for dependency in dependencies), ["fastapi", "subcore", "uvicorn"])
        self.assertEqual(pyproject["tool"]["uv"]["sources"]["subcore"], {"workspace": True})
        subcore = tomllib.loads((APP_ROOT.parent.parent / "packages" / "subcore" / "pyproject.toml").read_text(encoding="utf-8"))
        engine_dependencies = " ".join(subcore["project"].get("dependencies", [])).lower()
        for web in ("fastapi", "uvicorn", "starlette", "httpx"):
            self.assertNotIn(web, engine_dependencies)


if __name__ == "__main__":
    unittest.main()
