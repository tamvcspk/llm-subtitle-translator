from pathlib import Path
import re
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def read_html() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


def read_scripts() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted((STATIC / "js").rglob("*.js")))


def view_section(html: str, view: str) -> str:
    match = re.search(rf'<section id="view-{view}" class="view"[^>]*>(.*?)\n    </section>', html, re.S)
    assert match, f"missing view section {view}"
    return match.group(1)


class FrontendStructureTests(unittest.TestCase):
    def test_module_imports_resolve_to_files(self):
        # The console ships without a build step, so a broken relative import
        # only shows up as a blank page in the browser.
        html = read_html()
        self.assertIn('<script type="module" src="/js/main.js"></script>', html)
        for path in (STATIC / "js").rglob("*.js"):
            for target in re.findall(r'from "(\.{1,2}/[^"]+)"', path.read_text(encoding="utf-8")):
                self.assertTrue((path.parent / target).resolve().is_file(), f"{path.name} imports missing {target}")

    def test_every_nav_item_has_a_view(self):
        html = read_html()
        pairs = re.findall(r'class="nav-item" href="#(\w+)" data-view="(\w+)"', html)
        self.assertTrue(all(href == view for href, view in pairs))
        views = {view for _, view in pairs}
        self.assertEqual(views, {"dashboard", "queue", "wanted", "scan", "settings", "system", "logs"})
        for view in views:
            self.assertIn(f'id="view-{view}" class="view" data-view="{view}"', html)

    def test_backups_live_under_system_view(self):
        html = read_html()
        system = view_section(html, "system")
        settings = view_section(html, "settings")

        for element in ('id="create-backup"', 'id="import-backup"', 'id="backup-file-input"'):
            self.assertIn(element, system)
            self.assertNotIn(element, settings)

    def test_frontend_copy_is_not_english_only(self):
        html = read_html()
        scripts = read_scripts()

        self.assertNotIn("Bazarr English subtitles", html)
        self.assertNotIn("Local English Subtitles", html)
        self.assertNotIn("English subtitles to Gemini", scripts)

    def test_settings_inputs_are_named_after_api_fields(self):
        settings = view_section(read_html(), "settings")
        names = set(re.findall(r'name="(\w+)"', settings))

        self.assertEqual(names, {
            "bazarr_url", "bazarr_api_key", "gemini_api_key", "gemini_api_key2", "tmdb_api_key",
            "gst_model", "gst_fallback_model", "gst_batch_size", "gst_retry_batch_size", "job_settle_seconds",
            "gst_paid_quota", "gst_skip_upgrade", "gst_quiet", "gst_progress_log", "gst_thoughts_log",
            "gst_token_report", "gst_temperature", "gst_top_p", "gst_top_k", "gst_context_size",
            "gst_thinking_budget", "gst_thinking_level", "gst_no_streaming", "gst_no_thinking",
            "media_roots", "scan_limit",
        })
        self.assertIn('<select id="f-gst_model" name="gst_model"', settings)
        self.assertIn('<select id="f-gst_fallback_model" name="gst_fallback_model"', settings)
        self.assertIn("Token report", settings)
        self.assertNotIn("gst_token_stats", settings + read_scripts())
        self.assertNotIn("gst_resume_fallback_batch_size", settings)

    def test_secret_fields_have_masks_and_test_buttons(self):
        settings = view_section(read_html(), "settings")
        scripts = read_scripts()

        for kind, name in [
            ("bazarr", "bazarr_api_key"),
            ("gemini_api_key", "gemini_api_key"),
            ("gemini_api_key2", "gemini_api_key2"),
            ("tmdb_api_key", "tmdb_api_key"),
        ]:
            self.assertIn(f'data-test-kind="{kind}"', settings)
            self.assertRegex(settings, rf'name="{name}"[^>]*type="password"')
        self.assertIn('const SECRET_MASK = "**********";', scripts)
        self.assertIn("/api/test-connection", scripts)
        self.assertIn("/api/gemini-models", scripts)

    def test_logs_have_clear_button(self):
        self.assertIn('id="clear-logs"', view_section(read_html(), "logs"))
        self.assertIn("/api/logs/clear", read_scripts())

    def test_failed_jobs_support_retry_and_cancellation(self):
        scripts = read_scripts()

        self.assertIn("/api/queue/retry", scripts)
        self.assertIn("/api/queue/cancel", scripts)
        self.assertIn("Cancel this failed translation?", scripts)

    def test_deferred_jobs_show_retry_time_and_support_cancellation(self):
        html = read_html()
        scripts = read_scripts()

        self.assertIn('id="stat-deferred"', html)
        self.assertIn('export const QUEUE_STATES = ["pending", "processing", "deferred", "done", "failed"];', scripts)
        self.assertIn("Retry after", scripts)
        self.assertIn('post("/api/queue/delete", { state, job_id: job.job_id })', scripts)
        self.assertIn('state === "pending" || state === "deferred"', scripts)

    def test_quota_pause_is_shown_in_local_time(self):
        self.assertIn('id="quota-banner"', read_html())
        self.assertIn("daily_quota_pause", read_scripts())

    def test_backup_ui_supports_download_and_import(self):
        scripts = read_scripts()

        self.assertIn("/api/backups/download", scripts)
        self.assertIn("/api/backups/import", scripts)
        self.assertIn('"Content-Type": "application/zip"', scripts)

    def test_navigation_uses_url_hash_and_has_no_refresh_button(self):
        html = read_html()
        scripts = read_scripts()

        self.assertNotIn('id="refresh"', html)
        self.assertIn("window.location.hash", scripts)
        self.assertIn('window.addEventListener("hashchange"', scripts)
        self.assertIn("switchView(viewFromHash())", scripts)

    def test_dynamic_text_is_not_written_as_html(self):
        # Paths and provider errors are untrusted; only the constant icon set may use innerHTML.
        scripts = read_scripts()
        self.assertEqual(len(re.findall(r"\.(?:innerHTML|outerHTML)\s*=|insertAdjacentHTML", scripts)), 1)
        self.assertIn("svg.innerHTML = ICONS[name]", scripts)


class DeploymentConfigTests(unittest.TestCase):
    def test_compose_uses_6789_inside_and_outside_container(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        worker_py = (ROOT / "worker.py").read_text(encoding="utf-8")

        self.assertIn("name: gemini-srt-translator-bazarr", compose)
        self.assertIn("WEB_PORT: 6789", compose)
        self.assertIn("- 6789:6789", compose)
        self.assertNotIn("WEB_PORT=", env_example)
        self.assertNotIn("HOST_PORT=", env_example)
        self.assertIn('os.getenv("WEB_PORT", "6789")', worker_py)
        self.assertNotIn(":8080", compose)

    def test_default_state_dir_mounts_project_root_not_nested_state_dir(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

        self.assertIn("${WORKER_STATE_DIR:-/opt/docker/gemini-srt-translator-bazarr}:/state", compose)
        self.assertNotIn("${WORKER_STATE_DIR:-/opt/docker/gemini-srt-translator-bazarr/state}:/state", compose)
        self.assertIn("WORKER_STATE_DIR=/opt/docker/gemini-srt-translator-bazarr", env_example)
        self.assertNotIn("WORKER_STATE_DIR=/opt/docker/gemini-srt-translator-bazarr/state", env_example)

    def test_queue_mount_is_outside_worker_state_mount(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        worker_py = (ROOT / "worker.py").read_text(encoding="utf-8")

        self.assertIn("QUEUE_DIR: /queue", compose)
        self.assertIn("${BAZARR_POSTPROCESS_DIR:-/opt/docker/bazarr/postprocess}/queue:/queue", compose)
        self.assertNotIn("QUEUE_DIR: /state/queue", compose)
        self.assertNotIn(":/state/queue", compose)
        self.assertIn('os.getenv("QUEUE_DIR", "/queue")', worker_py)

    def test_docker_build_skips_ffmpeg_by_default_after_upstream_fix(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

        self.assertIn("uv sync --locked --no-dev", dockerfile)
        self.assertIn("ARG INSTALL_FFMPEG=false", dockerfile)
        self.assertIn("apt-get install -y --no-install-recommends ffmpeg", dockerfile)
        self.assertIn("INSTALL_FFMPEG: ${INSTALL_FFMPEG:-false}", compose)
        self.assertIn("INSTALL_FFMPEG=false", env_example)

    def test_upstream_dependency_allows_compatible_3x_updates(self):
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        dependencies = pyproject["project"]["dependencies"]
        upstream = [item for item in dependencies if item.startswith("gemini-srt-translator")]

        self.assertEqual(len(upstream), 1)
        self.assertRegex(upstream[0], r"^gemini-srt-translator>=\d+\.\d+\.\d+,<4$")


if __name__ == "__main__":
    unittest.main()
