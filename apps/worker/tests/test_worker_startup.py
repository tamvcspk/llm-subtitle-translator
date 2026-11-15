import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import worker
from gst_worker.backups import BackupMaintenance


class WorkerStartupTests(unittest.TestCase):
    def test_web_startup_initializes_backup_maintenance(self):
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
                patch("sys.argv", ["worker.py", "--no-worker"]),
                patch.object(worker, "configure_logging"),
                patch.object(worker.threading, "Thread") as thread,
                patch.object(worker, "start_web_server") as server,
            ):
                self.assertEqual(worker.main(), 0)
                server.assert_called_once()
                ctx = server.call_args.args[0]
                self.assertIsInstance(ctx["backups"], BackupMaintenance)
                # The backup scheduler is started; the telemetry writer thread may be started beside it (P5).
                started = [call.kwargs.get("target") for call in thread.call_args_list]
                self.assertEqual(started.count(worker.run_backup_scheduler), 1)
                thread.return_value.start.assert_called()
