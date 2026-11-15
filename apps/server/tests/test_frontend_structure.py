from pathlib import Path
import re
import tomllib
import unittest


APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parents[1]
STATIC = APP_ROOT / "static"


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
        self.assertEqual(views, {"dashboard", "queue", "library", "wanted", "scan", "settings", "system", "logs"})
        for view in views | {"episode", "glossary", "style", "compare"}:  # detail pages have no nav item of their own
            self.assertIn(f'id="view-{view}" class="view" data-view="{view}"', html)

    def test_backups_live_under_system_view(self):
        html = read_html()
        system = view_section(html, "system")
        settings = view_section(html, "settings")

        for element in ('id="create-backup"', 'id="import-backup"', 'id="backup-file-input"'):
            self.assertIn(element, system)
            self.assertNotIn(element, settings)

    def test_cleanup_previews_before_it_deletes_and_pruned_versions_cannot_be_restored(self):
        html = read_html()
        scripts = read_scripts()

        system = view_section(html, "system")
        for element in ('id="cleanup-preview"', 'id="cleanup-run"', 'id="cleanup-report"'):
            self.assertIn(element, system)
        self.assertIn('"/api/cleanup", { dry_run: true }', scripts)  # the preview is the default and is asked first
        self.assertIn('"/api/cleanup", { dry_run: false }', scripts)
        self.assertIn("Clean up now?", scripts)  # the confirmation comes between the two
        self.assertIn('item.status !== "pruned"', scripts)

    def test_frontend_copy_is_not_english_only(self):
        html = read_html()
        scripts = read_scripts()

        self.assertNotIn("Bazarr English subtitles", html)
        self.assertNotIn("Local English Subtitles", html)
        self.assertNotIn("English subtitles to Gemini", scripts)

    def test_settings_inputs_are_named_after_api_fields(self):
        settings = view_section(read_html(), "settings")
        names = set(re.findall(r'name="(\w+)"', settings))

        # Only settings the in-process engine reads are offered (P3 dropped the old `gst` CLI options).
        self.assertEqual(names, {
            "bazarr_url", "bazarr_api_key", "gemini_api_key", "gemini_api_key2", "tmdb_api_key",
            "gst_model", "gst_fallback_model", "job_settle_seconds", "gst_paid_quota",
            "gst_temperature", "gst_thinking_budget", "gst_no_thinking",
            "auto_request", "auto_request_daily_cap", "catalog_sync_interval", "catalog_shrink_guard",
            "media_roots", "scan_limit",
            "memory_enabled", "memory_model", "memory_gap_wait_seconds", "memory_budget_tokens",
            "memory_recent_episodes", "few_shot_budget_tokens", "glossary_filter_threshold",
            "log_level", "log_prompt_bodies", "log_max_mb", "log_retention_days", "log_body_max_bytes",
            "events_retention_days", "llm_cost_in_per_mtok", "llm_cost_out_per_mtok",
            "retention_done_days", "retention_failed_days", "retention_versions_keep", "retention_glossary_keep",
        })
        self.assertIn('<select id="f-gst_model" name="gst_model"', settings)
        self.assertIn('<select id="f-gst_fallback_model" name="gst_fallback_model"', settings)
        self.assertNotIn("gst_token_stats", settings + read_scripts())
        self.assertNotIn("gst_resume_fallback_batch_size", settings)

    def test_settings_form_names_match_the_saved_config_keys(self):
        from subserver.config import DEFAULT_APP_CONFIG

        names = set(re.findall(r'name="(\w+)"', view_section(read_html(), "settings")))
        self.assertEqual(names - set(DEFAULT_APP_CONFIG), set())

    def test_library_pages_use_the_documented_endpoints_and_show_status(self):
        html = read_html()
        scripts = read_scripts()

        for endpoint in (
            "/api/library/series", "/cues?", "/corrections", "/retranslate", "/versions",
            "/diff", "/revert", "/api/series/", "/glossary", "/context", "/context/extract", "/memory",
        ):
            self.assertIn(endpoint, scripts)
        for element in ('id="episode-jump"', 'id="episode-search"', 'id="cue-dialog"', 'id="retranslate-dialog"',
                        'id="episode-version-list"', 'id="glossary-yaml"', 'id="glossary-check"',
                        'id="episode-context-pane"', 'id="context-policy"', 'id="context-extract"'):
            self.assertIn(element, html)
        self.assertIn("Stale", scripts)
        self.assertIn("is only flagged", html)

    def test_the_style_page_checks_before_it_saves_and_tries_a_style_without_writing(self):
        """P12: the form, the cards, the trial and the comparison reach the documented endpoints."""
        html = read_html()
        scripts = read_scripts()
        style = view_section(html, "style")

        for element in ('id="style-fields"', 'id="style-check"', 'id="style-save"', 'id="style-preset"', 'id="style-suggest"',
                        'id="style-cards"', 'id="try-run"', 'id="variant-run"'):
            self.assertIn(element, style)
        for element in ('id="card-dialog"', 'id="card-save"', 'id="card-style"'):
            self.assertIn(element, html)
        for endpoint in ("/style?lang=", "/style/suggestions", "/style/preview", "/cards?lang=", "/cards/delete?lang=",
                         "/variant", "/versions/${state.version}/compare", "/versions/${state.version}/choose",
                         "/glossary/history", "/glossary/restore", "/glossary/copy"):
            self.assertIn(endpoint, scripts)
        # "Check" asks the server to count what a save would make stale, and writes nothing.
        self.assertIn("dry_run: true", scripts)
        # The glossary page edits a document through a form; the YAML tab is the exchange format.
        glossary = view_section(html, "glossary")
        for element in ('id="glossary-form"', 'id="glossary-yaml"', 'id="glossary-tabs"', 'id="glossary-more"'):
            self.assertIn(element, glossary)
        self.assertNotIn("backups/glossary", html + scripts)
        compare = view_section(html, "compare")
        for element in ('id="compare-list"', 'id="compare-verdict"', 'id="compare-apply"', 'id="compare-adopt"'):
            self.assertIn(element, compare)

    def test_the_library_browses_the_catalog_and_a_bulk_request_shows_its_cost_before_it_asks(self):
        html = read_html()
        scripts = read_scripts()
        library = view_section(html, "library")

        for endpoint in ("/api/catalog", "/api/catalog/movies", "/api/catalog/sync", "/api/catalog/ignore",
                         "/api/catalog/unignore", "/policy", '"/api/jobs"'):
            self.assertIn(endpoint, scripts)
        for element in ('id="library-request"', 'id="library-retranslate"', 'id="library-ignore"', 'id="library-search"'):
            self.assertIn(element, library)
        # The estimate is asked first, then the admin confirms, then the request is made (FR-15.5).
        self.assertLess(scripts.index("estimate_only: true"), scripts.index("planned_total > 1"))
        self.assertIn("estimateText(preview)", scripts)
        self.assertIn("Sync now", scripts)

    def test_the_hold_inbox_and_its_endpoints_are_gone_from_the_console(self):
        html = read_html()
        scripts = read_scripts()

        for gone in ("queue/held", "queue/rejected", "nav-held-count", "queue-approve", "admission_mode"):
            self.assertNotIn(gone, html + scripts)

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

    def test_waiting_jobs_say_what_they_wait_for_and_support_cancellation_stop_and_the_gap_button(self):
        html = read_html()
        scripts = read_scripts()

        self.assertIn('id="stat-deferred"', html)
        self.assertIn('export const QUEUE_STATES = ["pending", "processing", "deferred", "done", "failed"];', scripts)
        self.assertIn("Waiting · daily quota spent", scripts)
        self.assertIn("Waiting · earlier episodes' facts", scripts)
        self.assertIn('post("/api/queue/request-prerequisites"', scripts)
        self.assertIn('post("/api/queue/stop"', scripts)
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

        self.assertEqual(dependencies, ["subcore"])
        self.assertEqual(pyproject["tool"]["uv"]["sources"]["subcore"], {"workspace": True})


if __name__ == "__main__":
    unittest.main()
