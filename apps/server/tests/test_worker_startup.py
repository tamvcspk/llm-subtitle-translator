import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import worker
from subserver.adapters.backups import BackupMaintenance
from subserver.runtime import BackgroundLoop


class WorkerStartupTests(unittest.TestCase):
    def run_main(self, *flags):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "STATE_DIR": str(root / "state"),
                "QUEUE_DIR": str(root / "queue"),
                "APP_CONFIG_PATH": str(root / "state" / "config.json"),
                "POSTPROCESS_TARGETS_PATH": str(root / "targets.json"),
                "TMDB_CACHE_PATH": str(root / "cache.json"),
            }
            with (
                patch.dict(os.environ, env, clear=True),
                patch("sys.argv", ["worker.py", *flags]),
                patch.object(worker, "configure_logging"),
                patch.object(worker, "start_web_server") as server,
            ):
                self.assertEqual(worker.main(), 0)
                return server

    def test_web_startup_initializes_backup_maintenance(self):
        server = self.run_main("--no-worker")
        server.assert_called_once()
        ctx = server.call_args.args[0]
        self.assertIsInstance(ctx["backups"], BackupMaintenance)

    def test_the_loops_are_handed_to_the_web_app_which_starts_them_in_its_lifespan(self):
        """The process starts no thread of its own for the console's sake: the app's lifespan does (P15d, D7)."""
        names = lambda server: [loop.name for loop in server.call_args.kwargs["loops"]]  # noqa: E731
        everything = self.run_main()
        self.assertEqual(names(everything), ["backup-scheduler", "catalog-sync", "queue-worker"])
        self.assertTrue(all(isinstance(loop, BackgroundLoop) for loop in everything.call_args.kwargs["loops"]))
        self.assertEqual(names(self.run_main("--no-worker")), ["backup-scheduler"])  # maintenance only
        self.assertEqual(names(self.run_main("--no-queue")), ["backup-scheduler", "catalog-sync"])  # sync, never a job


    def test_worker_only_has_no_console_and_starts_the_loops_itself(self):
        with (
            patch.object(worker, "start_loops") as start,
            patch.object(worker.QueueWorker, "run_forever") as run_forever,
        ):
            server = self.run_main("--worker-only")
        server.assert_not_called()
        run_forever.assert_called_once()
        self.assertEqual([loop.name for loop in start.call_args.args[0]], ["backup-scheduler", "catalog-sync"])


if __name__ == "__main__":
    unittest.main()
