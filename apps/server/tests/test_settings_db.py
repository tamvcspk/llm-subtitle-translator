"""P6 (FR-20, ADR-13): settings and API keys live in the database. SPEC section 6, criterion 19 a, b, c, e, f
(19d, a job waiting on a refused key, needs the P8 queue). No network: Gemini and Bazarr are fakes."""

import contextlib
import http.client
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from subcore.providers import AuthError, FakeProvider, LLMRequest, ModelNotFound

import worker
from subserver.adapters import backups as gst_backups
from subserver.services.auth import ConsoleAuth
from subserver.adapters.llm import KeyedProvider, LiveProviderFactory, build_provider
from subserver.domain.ports import MissingApiKey
from subserver.domain.redaction import DEFAULT, REDACTED
from subserver.adapters.settings import SettingsService, last_chars
from subserver.adapters.store import Store

KEY_A = "AIzaSyAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1111"
KEY_B = "AIzaSyBBBBBBBBBBBBBBBBBBBBBBBBBBBBB2222"
KEY_C = "AIzaSyCCCCCCCCCCCCCCCCCCCCCCCCCCCCC3333"
BAZARR_KEY = "bazarr-key-0000000000009999"
REQUEST = LLMRequest(system="s", user="u", meta={"targets": [1], "source": {1: "hello"}})


@contextlib.contextmanager
def started(legacy=None, env=None, store=None):
    """A started service over a throwaway database; ``legacy`` is written as the old config.json."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        if legacy is not None:
            (root / "config.json").write_text(json.dumps(legacy), encoding="utf-8")
        own = store or Store(root / "app.db")
        try:
            service = SettingsService(
                own, config_path=str(root / "config.json"), postprocess_targets_path=str(root / "targets.json"),
                env={"BAZARR_CONFIG_PATH": str(root / "none.yaml"), **(env or {})},
            )
            service.start()
            yield service, root, own
        finally:
            if store is None:
                own.close()


def restart(store, root, env=None):
    service = SettingsService(
        store, config_path=str(root / "config.json"), postprocess_targets_path=str(root / "targets.json"),
        env={"BAZARR_CONFIG_PATH": str(root / "none.yaml"), **(env or {})},
    )
    service.start()
    return service


class FakeHttp:
    """Answers a key test: ``outcome`` is a result or an exception to raise."""

    def __init__(self, outcome=None):
        self.outcome = outcome if outcome is not None else {}
        self.calls = []

    def get_json(self, url, params=None, headers=None):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def http_error(code):
    return urllib.error.HTTPError("https://example.invalid/x", code, "refused", {}, None)


class PrecedenceTests(unittest.TestCase):
    """19a: a saved value is live at once and beats the environment; the environment beats the default."""

    def test_stored_value_beats_environment_beats_default_without_a_restart(self):
        env = {"BAZARR_URL": "http://env-bazarr:6767", "GST_MODEL": "env-model"}
        with started(env=env) as (service, root, store):
            self.assertEqual(service.settings()["bazarr_url"], "http://env-bazarr:6767")
            self.assertEqual(service.settings()["gst_model"], "env-model")
            self.assertEqual(service.settings()["job_settle_seconds"], 600)  # the default

            service.save({"bazarr_url": "http://db-bazarr:6767", "gst_model": "db-model", "job_settle_seconds": 5})

            settings = service.settings()  # the same service object: nothing was restarted
            self.assertEqual(settings["bazarr_url"], "http://db-bazarr:6767")
            self.assertEqual(settings["gst_model"], "db-model")
            self.assertEqual(settings["job_settle_seconds"], 5)

    def test_clearing_a_stored_address_falls_back_to_the_environment(self):
        with started(env={"BAZARR_URL": "http://env-bazarr:6767"}) as (service, root, store):
            service.save({"bazarr_url": "http://db-bazarr:6767"})
            service.save({"bazarr_url": ""})
            self.assertEqual(service.settings()["bazarr_url"], "http://env-bazarr:6767")

    def test_subscribers_hear_of_every_change_so_the_worker_adopts_it(self):
        with started() as (service, root, store):
            heard = []
            service.subscribe(lambda: heard.append(service.settings()["gst_model"]))
            service.save({"gst_model": "m-1"})
            service.add_key({"provider": "gemini", "value": KEY_A})
            self.assertEqual(heard, ["m-1", "m-1"])

    def test_a_subscriber_that_fails_does_not_undo_the_save(self):
        with started() as (service, root, store):
            service.subscribe(lambda: 1 / 0)
            with self.assertLogs(level="ERROR"):
                service.save({"gst_model": "m-2"})
            self.assertEqual(service.settings()["gst_model"], "m-2")

    def test_system_settings_cannot_be_written_from_the_console(self):
        with started() as (service, root, store):
            service.save({
                "state_dir": "/elsewhere", "db_path": "/elsewhere/app.db", "web_port": 1, "queue_dir": "/q",
                "console_user": "mallory", "console_password": "hunter2hunter2", "data_dir": "/d", "gst_model": "ok-model",
            })
            stored = {row.key for row in store.settings_rows()}
            for key in ("state_dir", "db_path", "web_port", "queue_dir", "console_user", "console_password", "data_dir"):
                self.assertNotIn(key, stored)
            self.assertIn("gst_model", stored)

    def test_markers_of_the_worker_are_not_settings_the_console_can_edit(self):
        with started() as (service, root, store):
            self.assertTrue({row.key for row in store.settings_rows()} >= {"_config_json_imported", "_keys_seeded"})
            service.save({"_keys_seeded": "never", "_config_json_imported": "never"})
            self.assertNotEqual(
                {row.key: row.value for row in store.settings_rows()}["_keys_seeded"], "never"
            )
            self.assertNotIn("_keys_seeded", service.app_config())


class KeySeedingTests(unittest.TestCase):
    """19b: the keys are seeded from the environment on the first run; after that the database wins."""

    def test_first_run_takes_the_keys_from_the_environment(self):
        env = {"GEMINI_API_KEY": KEY_A, "GEMINI_API_KEY2": KEY_B, "BAZARR_API_KEY": BAZARR_KEY, "TMDB_API_KEY": "tmdb-k-123456"}
        with started(env=env) as (service, root, store):
            keys = store.api_keys()
            self.assertEqual([(k.provider, k.value) for k in keys],
                             [("bazarr", BAZARR_KEY), ("tmdb", "tmdb-k-123456"), ("gemini", KEY_A), ("gemini", KEY_B)])
            self.assertTrue(all(k.status == "ok" for k in keys))
            settings = service.settings()
            self.assertEqual((settings["gemini_api_key"], settings["gemini_api_key2"]), (KEY_A, KEY_B))

    def test_a_later_change_of_the_environment_does_not_overwrite_the_database(self):
        with started(env={"GEMINI_API_KEY": KEY_A}) as (service, root, store):
            service.save({"gemini_api_key": KEY_B})  # pasted in the console
            again = restart(store, root, env={"GEMINI_API_KEY": KEY_C})
            self.assertEqual(again.settings()["gemini_api_key"], KEY_B)
            self.assertEqual([k.value for k in store.api_keys("gemini")], [KEY_B])

    def test_a_key_the_admin_deleted_is_not_seeded_again(self):
        with started(env={"GEMINI_API_KEY": KEY_A}) as (service, root, store):
            [key] = store.api_keys("gemini")
            service.remove_key(key.id)
            again = restart(store, root, env={"GEMINI_API_KEY": KEY_A})
            self.assertEqual(again.settings()["gemini_api_key"], "")
            self.assertEqual(store.api_keys("gemini"), [])

    def test_keys_in_the_old_config_file_come_before_the_environment(self):
        legacy = {"gemini_api_key": KEY_B, "bazarr_api_key": BAZARR_KEY}
        with started(legacy=legacy, env={"GEMINI_API_KEY": KEY_A}) as (service, root, store):
            self.assertEqual(service.settings()["gemini_api_key"], KEY_B)
            self.assertEqual(service.settings()["bazarr_api_key"], BAZARR_KEY)
            self.assertEqual(len(store.api_keys("gemini")), 1)

    def test_the_seeded_keys_are_hidden_from_the_log_filter(self):
        secret = "AIzaSyDDDDDDDDDDDDDDDDDDDDDDDDDDDD4444"
        with started(env={"GEMINI_API_KEY": secret}) as (service, root, store):
            self.assertEqual(DEFAULT.redact(f"calling with {secret}"), f"calling with {REDACTED}")

    def test_a_key_added_in_the_console_is_hidden_at_once(self):
        secret = "console-pasted-secret-7777"
        with started() as (service, root, store):
            service.add_key({"provider": "tmdb", "value": secret})
            self.assertNotIn(secret, DEFAULT.redact(f"error: {secret} was refused"))


class ConfigImportTests(unittest.TestCase):
    """19f: config.json is imported once; running again never overwrites what was edited."""

    LEGACY = {"gst_model": "legacy-model", "job_settle_seconds": 30, "media_roots": ["/tv"], "gst_batch_size": 500}

    def test_values_come_in_and_the_old_options_stay_out(self):
        with started(legacy=self.LEGACY) as (service, root, store):
            config = service.app_config()
            self.assertEqual((config["gst_model"], config["job_settle_seconds"], config["media_roots"]),
                             ("legacy-model", 30, ["/tv"]))
            self.assertNotIn("gst_batch_size", {row.key for row in store.settings_rows()})

    def test_running_the_importer_again_does_not_overwrite_an_edited_value(self):
        with started(legacy=self.LEGACY) as (service, root, store):
            service.save({"gst_model": "edited-model", "job_settle_seconds": 99})
            again = restart(store, root)  # config.json is still there, and still says legacy-model
            self.assertTrue((root / "config.json").exists())
            self.assertEqual(again.app_config()["gst_model"], "edited-model")
            self.assertEqual(again.app_config()["job_settle_seconds"], 99)

    def test_a_new_edit_of_the_old_file_is_not_imported_after_the_first_run(self):
        with started(legacy=self.LEGACY) as (service, root, store):
            (root / "config.json").write_text(json.dumps({"gst_model": "changed-later"}), encoding="utf-8")
            self.assertEqual(restart(store, root).app_config()["gst_model"], "legacy-model")

    def test_a_value_already_in_the_database_is_kept_when_the_import_runs_late(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(root / "app.db")
            try:
                store.setting_save({"gst_model": "already-here"})
                (root / "config.json").write_text(json.dumps(self.LEGACY), encoding="utf-8")
                service = restart(store, root)
                self.assertEqual(service.app_config()["gst_model"], "already-here")
                self.assertEqual(service.app_config()["job_settle_seconds"], 30)
            finally:
                store.close()

    def test_an_unreadable_config_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.json").write_text("{not json", encoding="utf-8")
            store = Store(root / "app.db")
            try:
                with self.assertLogs(level="WARNING"):
                    service = restart(store, root)
                self.assertEqual(service.app_config()["gst_model"], "gemini-3.1-flash-lite")
            finally:
                store.close()

    def test_targets_json_follows_the_stored_languages(self):
        with started(legacy={"target_languages": [{"code": "vi", "language": "Vietnamese", "enabled": True}]}) \
                as (service, root, store):
            targets = json.loads((root / "targets.json").read_text(encoding="utf-8"))
            self.assertEqual(targets["target_languages"], [{"code": "vi", "language": "Vietnamese", "enabled": True}])


class KeyViewTests(unittest.TestCase):
    """19c: the API returns the last four characters of a key and nothing more."""

    def test_only_the_last_four_characters_of_a_long_key_are_shown(self):
        self.assertEqual(last_chars(KEY_A), "1111")
        self.assertEqual(last_chars("short-key"), "")  # four characters of a short secret are too much of it
        self.assertEqual(last_chars(""), "")

    def test_listing_and_public_settings_never_hold_a_value(self):
        with started() as (service, root, store):
            service.save({"gemini_api_key": KEY_A, "gemini_api_key2": KEY_B, "bazarr_api_key": BAZARR_KEY})
            text = json.dumps({"keys": service.list_keys(), "settings": service.public()})
            for value in (KEY_A, KEY_B, BAZARR_KEY):
                self.assertNotIn(value, text)
                self.assertNotIn(value[:-4], text)
            self.assertIn("1111", text)
            self.assertIn("2222", text)
            self.assertIn("9999", text)

    def test_a_repr_of_a_key_row_holds_no_value(self):
        with started() as (service, root, store):
            service.add_key({"provider": "gemini", "value": KEY_A})
            self.assertNotIn(KEY_A, repr(store.api_keys()))

    def test_add_validates_provider_and_value(self):
        with started() as (service, root, store):
            for body in ({"provider": "nope", "value": KEY_A}, {"provider": "gemini", "value": ""},
                         {"provider": "gemini", "value": "**********"}, {}):
                with self.assertRaises(ValueError):
                    service.add_key(body)
            self.assertEqual(store.api_keys(), [])

    def test_update_changes_label_and_status_but_a_new_value_resets_the_status(self):
        with started() as (service, root, store):
            key = service.add_key({"provider": "gemini", "value": KEY_A, "label": "main"})
            service.update_key(key["id"], {"status": "invalid"})
            self.assertEqual(store.api_keys()[0].status, "invalid")
            renamed = service.update_key(key["id"], {"label": "spare"})
            self.assertEqual((renamed["label"], renamed["status"]), ("spare", "invalid"))
            replaced = service.update_key(key["id"], {"value": KEY_B})
            self.assertEqual(replaced["status"], "ok")
            self.assertEqual(store.api_keys()[0].value, KEY_B)
            self.assertIsNone(service.update_key(999, {"label": "x"}))
            with self.assertRaises(ValueError):
                service.update_key(key["id"], {"status": "weird"})

    def test_the_database_file_is_for_the_worker_user_only(self):
        if os.name == "nt":
            self.skipTest("POSIX file modes")
        with started() as (service, root, store):
            self.assertEqual((root / "app.db").stat().st_mode & 0o777, 0o600)


class RefusedKeyTests(unittest.TestCase):
    """FR-17.5: a refused key becomes invalid and is passed over; a new key or a good test opens the provider."""

    def test_an_auth_error_marks_that_key_invalid_and_the_other_key_takes_over(self):
        with started() as (service, root, store):
            service.save({"gemini_api_key": KEY_A, "gemini_api_key2": KEY_B})

            def refuse(req, n):
                raise AuthError("API key not valid")

            bad = KeyedProvider(FakeProvider(handler=refuse), store.api_keys("gemini")[0].id, service)
            with self.assertRaises(AuthError):
                bad.generate(REQUEST)

            first, second = store.api_keys("gemini")
            self.assertEqual((first.status, second.status), ("invalid", "ok"))
            self.assertEqual([k.value for k in service.usable_keys("gemini")], [KEY_B])
            self.assertEqual(service.settings()["gemini_api_key"], KEY_B)  # the live settings left the dead key out
            self.assertEqual([w["kind"] for w in service.warnings()], ["key-invalid"])

    def test_the_factory_builds_from_the_stored_keys_and_skips_a_refused_one(self):
        with started() as (service, root, store):
            service.save({"gemini_api_key": KEY_A, "gemini_api_key2": KEY_B})
            service.set_key_status(store.api_keys("gemini")[0].id, "invalid")
            provider = LiveProviderFactory(service)(service.settings(), "gemini-x")
            self.assertEqual(provider.model, "gemini-x")
            self.assertFalse(hasattr(provider, "providers"))  # one usable key: no failover chain

    def test_every_key_refused_says_so_instead_of_claiming_there_is_none(self):
        with started() as (service, root, store):
            service.save({"gemini_api_key": KEY_A})
            service.key_refused(store.api_keys("gemini")[0].id)
            with self.assertRaises(MissingApiKey) as caught:
                LiveProviderFactory(service)(service.settings(), "m")
            self.assertIn("refused", str(caught.exception))
            self.assertNotIn(KEY_A, str(caught.exception))

    def test_without_any_key_the_message_stays_the_old_one(self):
        with started() as (service, root, store):
            with self.assertRaises(MissingApiKey) as caught:
                LiveProviderFactory(service)({}, "m")
            self.assertIn("not configured", str(caught.exception))

    def test_a_new_key_in_the_same_slot_is_used_by_the_next_call_without_a_restart(self):
        with started() as (service, root, store):
            service.save({"gemini_api_key": KEY_A})
            service.key_refused(store.api_keys("gemini")[0].id)
            factory = LiveProviderFactory(service)
            with self.assertRaises(MissingApiKey):
                factory(service.settings(), "m")

            service.save({"gemini_api_key": KEY_B})  # pasted into the console

            self.assertEqual(store.api_keys("gemini")[0].status, "ok")
            self.assertEqual(factory(service.settings(), "m").model, "m")

    def test_testing_a_key_that_works_reopens_it(self):
        with started() as (service, root, store):
            service.save({"gemini_api_key": KEY_A})
            key_id = store.api_keys("gemini")[0].id
            service.key_refused(key_id)

            result = service.test_key(key_id, FakeHttp({"models": []}))

            self.assertTrue(result["ok"])
            self.assertEqual(store.api_keys("gemini")[0].status, "ok")
            self.assertNotIn(KEY_A, json.dumps(result))

    def test_testing_a_key_the_service_refuses_marks_it_invalid(self):
        with started() as (service, root, store):
            service.save({"gemini_api_key": KEY_A})
            key_id = store.api_keys("gemini")[0].id
            result = service.test_key(key_id, FakeHttp(http_error(400)))  # Google answers 400 for an unknown key
            self.assertFalse(result["ok"])
            self.assertEqual(store.api_keys("gemini")[0].status, "invalid")
            self.assertIn("rejected", result["message"])

    def test_an_unreachable_service_does_not_condemn_the_key(self):
        with started() as (service, root, store):
            service.save({"gemini_api_key": KEY_A})
            key_id = store.api_keys("gemini")[0].id
            for failure in (http_error(503), urllib.error.URLError(ConnectionRefusedError())):
                result = service.test_key(key_id, FakeHttp(failure))
                self.assertFalse(result["ok"])
                self.assertEqual(store.api_keys("gemini")[0].status, "ok")

    def test_the_test_sends_the_stored_key_not_a_typed_one(self):
        with started() as (service, root, store):
            service.save({"gemini_api_key": KEY_A})
            http = FakeHttp({"models": []})
            service.test_key(store.api_keys("gemini")[0].id, http)
            self.assertEqual(http.calls[0][1], {"key": KEY_A})

    def test_a_bazarr_key_is_tested_against_the_saved_address(self):
        with started() as (service, root, store):
            service.save({"bazarr_url": "http://bazarr.local:6767", "bazarr_api_key": BAZARR_KEY})
            http = FakeHttp([])
            result = service.test_key(store.api_keys("bazarr")[0].id, http)
            self.assertTrue(result["ok"])
            self.assertEqual(http.calls[0][0], "http://bazarr.local:6767/api/system/languages")
            self.assertEqual(http.calls[0][2], {"X-API-KEY": BAZARR_KEY})

    def test_the_key_slots_of_the_form_settle_a_status_the_same_way(self):
        with started() as (service, root, store):
            service.save({"gemini_api_key": KEY_A})
            service.record_slot_test("gemini_api_key", {"ok": False, "auth_failed": True})
            self.assertEqual(service.public()["gemini_api_key_status"], "invalid")
            service.record_slot_test("gemini_api_key", {"ok": False})  # unreachable: nothing changes
            self.assertEqual(service.public()["gemini_api_key_status"], "invalid")
            service.record_slot_test("gemini_api_key", {"ok": True})
            self.assertEqual(service.public()["gemini_api_key_status"], "ok")

    def test_a_use_of_a_key_is_recorded_at_most_every_half_minute(self):
        now = [1000.0]
        with started() as (service, root, store):
            service._clock = lambda: now[0]
            service.save({"gemini_api_key": KEY_A})
            key_id = store.api_keys("gemini")[0].id
            service.key_used(key_id)
            now[0] += 5
            service.key_used(key_id)
            self.assertEqual(store.api_keys("gemini")[0].last_used_at, 1000.0)
            now[0] += 60
            service.key_used(key_id)
            self.assertEqual(store.api_keys("gemini")[0].last_used_at, 1065.0)

    def test_the_events_of_key_changes_name_no_key(self):
        events = []

        class Sink:
            def event(self, kind, **data):
                events.append((kind, data))

        with started() as (service, root, store):
            service.telemetry = Sink()
            service.add_key({"provider": "gemini", "value": KEY_A})
            service.key_refused(store.api_keys("gemini")[0].id)
            service.save({"gemini_api_key": KEY_B})
            self.assertEqual([kind for kind, _ in events], ["key.added", "key.status", "key.replaced", "key.status"])
            self.assertNotIn(KEY_A, json.dumps(events))
            self.assertNotIn(KEY_B, json.dumps(events))


class ModelNotFoundTests(unittest.TestCase):
    def test_an_unknown_model_is_a_warning_on_settings_and_not_a_key_problem(self):
        with started() as (service, root, store):
            service.save({"gemini_api_key": KEY_A, "gst_model": "gemini-gone"})

            def unknown(req, n):
                raise ModelNotFound("models/gemini-gone is not found")

            provider = KeyedProvider(FakeProvider(handler=unknown, model="gemini-gone"), store.api_keys()[0].id, service)
            with self.assertRaises(ModelNotFound):
                provider.generate(REQUEST)

            self.assertEqual(store.api_keys()[0].status, "ok")
            [warning] = service.public()["key_warnings"]
            self.assertEqual((warning["kind"], warning["model"]), ("model-not-found", "gemini-gone"))
            service.save({"gst_model": "gemini-3.1-flash-lite"})  # the admin picked another model
            self.assertEqual(service.public()["key_warnings"], [])

    def test_a_good_call_clears_the_warning(self):
        with started() as (service, root, store):
            service.save({"gemini_api_key": KEY_A, "gst_model": "m-1"})
            service.model_not_found("m-1", "gone")
            self.assertEqual(len(service.warnings()), 1)
            service.model_found("m-1")
            self.assertEqual(service.warnings(), [])


class BackupTests(unittest.TestCase):
    """19e: a backup holds no API key unless asked to, and a restore never touches the console account."""

    def settings_with_keys(self, service):
        service.save({
            "gemini_api_key": KEY_A, "bazarr_api_key": BAZARR_KEY, "gst_model": "backed-up-model",
            "target_languages": [{"code": "vi", "language": "Vietnamese", "enabled": True}],
        })

    def backup(self, service, root, **kwargs):
        info = gst_backups.create_backup(
            str(root / "state"), str(root / "config.json"), str(root / "targets.json"), settings=service, **kwargs
        )
        with zipfile.ZipFile(info["path"]) as archive:
            return info, {name: archive.read(name).decode("utf-8") for name in archive.namelist()}

    def test_the_default_backup_holds_the_settings_and_no_key(self):
        with started() as (service, root, store):
            self.settings_with_keys(service)
            store.save_auth_account("admin", "scrypt$1$1$1$salt$hash-should-never-leave")
            info, members = self.backup(service, root, reason="manual")

            self.assertEqual(sorted(members), ["backup.json", "config/config.json", "postprocess/targets.json"])
            everything = "\n".join(members.values())
            for secret in (KEY_A, BAZARR_KEY, "hash-should-never-leave"):
                self.assertNotIn(secret, everything)
            self.assertEqual(json.loads(members["config/config.json"])["gst_model"], "backed-up-model")
            self.assertFalse(json.loads(members["backup.json"])["includes_api_keys"])

    def test_the_whole_database_file_is_not_in_the_backup(self):
        with started() as (service, root, store):
            self.settings_with_keys(service)
            info, members = self.backup(service, root, reason="manual", include_keys=True)
            self.assertFalse([name for name in members if name.endswith((".db", ".sqlite"))])

    def test_a_scheduled_backup_never_holds_keys(self):
        with started() as (service, root, store):
            self.settings_with_keys(service)
            info = gst_backups.create_scheduled_backup_if_due(
                str(root / "state"), str(root / "config.json"), str(root / "targets.json"), settings=service
            )
            with zipfile.ZipFile(info["path"]) as archive:
                self.assertNotIn("config/api_keys.json", archive.namelist())
                self.assertNotIn(KEY_A, b"".join(archive.read(n) for n in archive.namelist()).decode("utf-8"))

    def test_keys_are_in_the_backup_only_when_asked_for(self):
        with started() as (service, root, store):
            self.settings_with_keys(service)
            info, members = self.backup(service, root, reason="manual", include_keys=True)
            keys = json.loads(members["config/api_keys.json"])
            self.assertEqual({(k["provider"], k["value"]) for k in keys}, {("gemini", KEY_A), ("bazarr", BAZARR_KEY)})
            self.assertTrue(json.loads(members["backup.json"])["includes_api_keys"])
            self.assertNotIn(KEY_A, members["config/config.json"])

    def archive(self, members):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            for name, text in members.items():
                archive.writestr(name, text)
        return payload.getvalue()

    def test_restoring_brings_back_settings_and_leaves_the_console_account_alone(self):
        with started() as (source, source_root, source_store):
            self.settings_with_keys(source)
            info, members = self.backup(source, source_root, reason="manual", include_keys=True)
        with started() as (target, root, store):
            store.save_auth_account("admin", "scrypt$16384$8$1$salt$kept")
            heard = []
            target.subscribe(lambda: heard.append(1))

            result = gst_backups.restore_backup_archive(
                self.archive(members), str(root / "state"), str(root / "config.json"), str(root / "targets.json"),
                settings=target,
            )

            self.assertEqual(target.settings()["gst_model"], "backed-up-model")
            self.assertEqual({k.value for k in store.api_keys()}, {KEY_A, BAZARR_KEY})
            self.assertEqual(store.auth_account().user, "admin")
            self.assertEqual(store.auth_account().password_hash, "scrypt$16384$8$1$salt$kept")
            self.assertIn("config/api_keys.json", result["imported"])
            self.assertTrue(heard)  # the running worker is told
            with zipfile.ZipFile(result["pre_import_backup"]["path"]) as archive:
                self.assertNotIn(KEY_A, b"".join(archive.read(n) for n in archive.namelist()).decode("utf-8"))

    def test_a_backup_without_keys_leaves_the_stored_keys_in_place(self):
        with started() as (source, source_root, source_store):
            self.settings_with_keys(source)
            info, members = self.backup(source, source_root, reason="manual")
        with started() as (target, root, store):
            target.save({"gemini_api_key": KEY_C})
            gst_backups.restore_backup_archive(
                self.archive(members), str(root / "state"), str(root / "config.json"), str(root / "targets.json"),
                settings=target,
            )
            self.assertEqual([k.value for k in store.api_keys("gemini")], [KEY_C])
            self.assertEqual(target.settings()["gst_model"], "backed-up-model")

    def test_an_old_backup_with_keys_in_config_json_fills_only_empty_slots(self):
        old = {"gst_model": "old-model", "gemini_api_key": KEY_A, "tmdb_api_key": "tmdb-old-0000000"}
        members = {
            "backup.json": '{"app":"gemini-srt-translator-bazarr"}', "config/config.json": json.dumps(old),
            "postprocess/targets.json": '{"target_languages":[]}',
        }
        with started() as (target, root, store):
            target.save({"gemini_api_key": KEY_C})
            gst_backups.restore_backup_archive(
                self.archive(members), str(root / "state"), str(root / "config.json"), str(root / "targets.json"),
                settings=target,
            )
            self.assertEqual([k.value for k in store.api_keys("gemini")], [KEY_C])  # the slot was taken
            self.assertEqual([k.value for k in store.api_keys("tmdb")], ["tmdb-old-0000000"])  # this one was empty

    def test_a_backup_with_a_bad_keys_member_is_refused_before_anything_changes(self):
        members = {
            "backup.json": '{"app":"gemini-srt-translator-bazarr"}', "config/config.json": '{"gst_model":"x"}',
            "postprocess/targets.json": "{}", "config/api_keys.json": "not json",
        }
        with started() as (target, root, store):
            with self.assertRaises(ValueError):
                gst_backups.restore_backup_archive(
                    self.archive(members), str(root / "state"), str(root / "config.json"),
                    str(root / "targets.json"), settings=target,
                )
            self.assertNotEqual(target.settings()["gst_model"], "x")


class HttpApiTests(unittest.TestCase):
    """19c over the real handler: /api/settings and /api/keys answer with the tail of a key and nothing more."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "index.html").write_text("<html></html>", encoding="utf-8")
        self.store = Store(root / "app.db")
        self.addCleanup(self.store.close)
        self.service = SettingsService(
            self.store, config_path=str(root / "config.json"), postprocess_targets_path=str(root / "targets.json"),
            env={"BAZARR_CONFIG_PATH": str(root / "none.yaml")},
        )
        self.service.start()
        self.http = FakeHttp({"models": []})
        self.ctx = {
            "static_dir": str(root), "queue_dir": str(root / "queue"), "log_dir": str(root / "logs"),
            "settings_service": self.service, "http": self.http, "settings": self.service.settings(),
            "state_dir": str(root / "state"), "config_path": str(root / "config.json"),
            "postprocess_targets_path": str(root / "targets.json"),
        }
        server = ThreadingHTTPServer(("127.0.0.1", 0), worker.ConsoleHandler)
        server.ctx = self.ctx
        server.auth = ConsoleAuth.from_env({"CONSOLE_AUTH": "off"})
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            payload = json.dumps(body).encode("utf-8") if body is not None else (b"{}" if method != "GET" else None)
            conn.request(method, path, body=payload, headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            text = response.read().decode("utf-8")
            try:
                data = json.loads(text) if text else None
            except ValueError:  # a static page, not an API answer
                data = None
            return response.status, text, data
        finally:
            conn.close()

    def test_saving_settings_over_http_is_live_and_returns_no_key(self):
        status, text, data = self.call("POST", "/api/settings", {"gst_model": "http-model", "gemini_api_key": KEY_A})
        self.assertEqual(status, 200)
        self.assertEqual(data["gst_model"], "http-model")
        self.assertEqual(data["gemini_api_key"], "**********")
        self.assertEqual(data["gemini_api_key_last4"], "1111")
        self.assertNotIn(KEY_A, text)
        status, text, data = self.call("GET", "/api/settings")
        self.assertEqual(data["gst_model"], "http-model")
        self.assertNotIn(KEY_A, text)
        self.assertEqual(self.service.settings()["gemini_api_key"], KEY_A)

    def test_the_model_list_follows_the_provider_over_http(self):
        """P7: with ``openai_compat`` the model dropdown lists the server's models, asked with the stored key."""
        self.http.outcome = {"data": [{"id": "llama3.1"}, {"id": "qwen3"}]}
        self.call("POST", "/api/settings", {
            "llm_provider": "openai_compat", "llm_base_url": "http://ollama:11434/v1", "openai_compat_api_key": KEY_A,
        })
        status, text, data = self.call("GET", "/api/gemini-models")
        self.assertEqual((status, [item["id"] for item in data["items"]]), (200, ["llama3.1", "qwen3"]))
        self.assertEqual(self.http.calls[-1][0], "http://ollama:11434/v1/models")
        self.assertEqual(self.http.calls[-1][2], {"Authorization": f"Bearer {KEY_A}"})
        status, text, data = self.call("GET", "/api/settings")
        self.assertEqual((data["llm_provider"], data["llm_base_url"]), ("openai_compat", "http://ollama:11434/v1"))
        self.assertNotIn(KEY_A, text)

    def test_the_key_routes_list_add_rename_test_and_remove(self):
        status, text, data = self.call("POST", "/api/keys", {"provider": "gemini", "value": KEY_A, "label": "main"})
        self.assertEqual(status, 200)
        self.assertEqual((data["label"], data["status"], data["last4"]), ("main", "ok", "1111"))
        self.assertNotIn("value", data)
        key_id = data["id"]

        status, text, data = self.call("GET", "/api/keys")
        self.assertEqual([item["id"] for item in data["items"]], [key_id])
        self.assertNotIn(KEY_A, text)

        status, text, data = self.call("PUT", f"/api/keys/{key_id}", {"label": "renamed", "status": "invalid"})
        self.assertEqual((data["label"], data["status"]), ("renamed", "invalid"))
        self.assertEqual(self.call("GET", "/api/keys")[2]["warnings"][0]["kind"], "key-invalid")

        status, text, data = self.call("POST", f"/api/keys/{key_id}/test")
        self.assertTrue(data["ok"])
        self.assertEqual(data["key"]["status"], "ok")
        self.assertNotIn(KEY_A, text)
        self.assertEqual(self.http.calls[-1][1], {"key": KEY_A})

        status, text, data = self.call("POST", f"/api/keys/{key_id}/remove")
        self.assertEqual((status, data), (200, {"ok": True}))
        self.assertEqual(self.call("GET", "/api/keys")[2]["items"], [])

    def test_bad_requests_and_unknown_keys(self):
        self.assertEqual(self.call("POST", "/api/keys", {"provider": "nope", "value": KEY_A})[0], 400)
        self.assertEqual(self.call("POST", "/api/keys", {"provider": "gemini"})[0], 400)
        self.assertEqual(self.call("PUT", "/api/keys/99", {"label": "x"})[0], 404)
        self.assertEqual(self.call("POST", "/api/keys/99/test")[0], 404)
        self.assertEqual(self.call("POST", "/api/keys/99/remove")[0], 404)
        self.assertEqual(self.call("GET", "/api/keys/99")[0], 200)  # not a route of its own: the static fallback

    def test_testing_a_stored_key_from_the_form_settles_its_status(self):
        self.call("POST", "/api/settings", {"gemini_api_key": KEY_A})
        self.http.outcome = http_error(400)
        status, text, data = self.call("POST", "/api/test-connection", {"kind": "gemini_api_key", "gemini_api_key": "**********"})
        self.assertFalse(data["ok"])
        self.assertEqual(self.call("GET", "/api/settings")[2]["gemini_api_key_status"], "invalid")
        self.http.outcome = {"models": []}
        self.call("POST", "/api/test-connection", {"kind": "gemini_api_key", "gemini_api_key": "**********"})
        self.assertEqual(self.call("GET", "/api/settings")[2]["gemini_api_key_status"], "ok")

    def test_testing_a_typed_key_does_not_touch_the_stored_one(self):
        self.call("POST", "/api/settings", {"gemini_api_key": KEY_A})
        self.http.outcome = http_error(400)
        self.call("POST", "/api/test-connection", {"kind": "gemini_api_key", "gemini_api_key": KEY_B})
        self.assertEqual(self.call("GET", "/api/settings")[2]["gemini_api_key_status"], "ok")

    def test_a_manual_backup_over_http_holds_no_key_unless_asked(self):
        self.call("POST", "/api/settings", {"gemini_api_key": KEY_A})
        for body, expected in (({}, False), ({"include_keys": True}, True), ({"include_keys": "yes"}, False)):
            with self.subTest(body=body):
                status, text, data = self.call("POST", "/api/backups", body)
                self.assertEqual(status, 200)
                with zipfile.ZipFile(data["backup"]["path"]) as archive:
                    self.assertEqual("config/api_keys.json" in archive.namelist(), expected)


class StartupTests(unittest.TestCase):
    """The running process: the service is wired to the worker, and the first run takes the environment in."""

    def run_main(self, env, probe):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            full = {
                "STATE_DIR": str(root / "state"), "QUEUE_DIR": str(root / "queue"),
                "APP_CONFIG_PATH": str(root / "state" / "config.json"),
                "POSTPROCESS_TARGETS_PATH": str(root / "targets.json"), "TMDB_CACHE_PATH": str(root / "cache.json"),
                "BAZARR_CONFIG_PATH": str(root / "none.yaml"), "CONSOLE_AUTH": "off", **env,
            }
            results = []
            with (
                patch.dict(os.environ, full, clear=True),
                patch("sys.argv", ["worker.py", "--no-worker"]),
                patch.object(worker, "configure_logging"),
                patch.object(worker.threading, "Thread"),
                patch.object(worker, "start_web_server", side_effect=lambda ctx, host, port, auth: results.append(probe(ctx))),
            ):
                self.assertEqual(worker.main(), 0)
        return results[0]

    def test_a_saved_setting_and_a_new_key_reach_the_running_worker(self):
        def probe(ctx):
            service, queue_worker = ctx["settings_service"], ctx["worker"]
            before = queue_worker.settings["gst_model"]
            service.save({"gst_model": "live-model"})
            after_save = queue_worker.settings["gst_model"]
            service.add_key({"provider": "gemini", "value": KEY_B})
            return before, after_save, queue_worker.settings["gemini_api_key"], ctx["backups"].settings is service

        before, after_save, key, backups_use_it = self.run_main({"GEMINI_API_KEY": KEY_A}, probe)
        self.assertEqual(before, "gemini-3.1-flash-lite")
        self.assertEqual(after_save, "live-model")
        self.assertEqual(key, KEY_A)  # the seeded key is still the first; the added one is the second
        self.assertTrue(backups_use_it)

    def test_the_environment_seeds_the_first_run_only(self):
        def probe(ctx):
            return ctx["worker"].settings["gemini_api_key"], ctx["settings_service"].repo.api_keys("gemini")[0].label

        key, label = self.run_main({"GEMINI_API_KEY": KEY_A}, probe)
        self.assertEqual((key, label), (KEY_A, "Key 1"))

    def test_the_old_config_json_is_taken_in_on_the_first_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state").mkdir()
            (root / "state" / "config.json").write_text(
                json.dumps({"gst_model": "from-old-file", "gemini_api_key": KEY_C}), encoding="utf-8"
            )
            full = {
                "STATE_DIR": str(root / "state"), "QUEUE_DIR": str(root / "queue"),
                "APP_CONFIG_PATH": str(root / "state" / "config.json"),
                "POSTPROCESS_TARGETS_PATH": str(root / "targets.json"), "TMDB_CACHE_PATH": str(root / "cache.json"),
                "BAZARR_CONFIG_PATH": str(root / "none.yaml"), "CONSOLE_AUTH": "off",
            }
            seen = []
            with (
                patch.dict(os.environ, full, clear=True),
                patch("sys.argv", ["worker.py", "--no-worker"]),
                patch.object(worker, "configure_logging"),
                patch.object(worker.threading, "Thread"),
                patch.object(worker, "start_web_server",
                             side_effect=lambda ctx, host, port, auth: seen.append(
                                 (ctx["worker"].settings["gst_model"], ctx["worker"].settings["gemini_api_key"]))),
            ):
                worker.main()
        self.assertEqual(seen, [("from-old-file", KEY_C)])


class BuildProviderTests(unittest.TestCase):
    def test_the_plain_settings_path_still_builds_a_provider_for_the_tests_and_scripts(self):
        provider = build_provider({"gemini_api_key": KEY_A, "gemini_api_key2": KEY_B}, "m")
        self.assertEqual(len(provider.providers), 2)

    def test_stored_keys_are_wrapped_so_the_worker_hears_about_them(self):
        with started() as (service, root, store):
            service.save({"gemini_api_key": KEY_A, "gemini_api_key2": KEY_B})
            provider = build_provider({}, "m", keys=service.usable_keys("gemini"), ledger=service)
            self.assertEqual(len(provider.providers), 2)
            self.assertTrue(all(isinstance(p.inner, KeyedProvider) for p in provider.providers))


if __name__ == "__main__":
    unittest.main()
