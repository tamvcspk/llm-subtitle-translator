"""Runtime composition seam for the worker process."""
import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterable
from .services.console import ConsoleActions
from .adapters.http import HTTPClient, HTTPTransport
from .adapters.bazarr import BazarrIntegration
from .adapters.backups import BackupMaintenance


@dataclass(frozen=True)
class BackgroundLoop:
    """A loop the process runs for as long as it serves: the queue worker, the catalog sync, the daily maintenance.
    ``worker.py`` lists them; the web app starts them in its ``lifespan`` and ``--worker-only`` starts them itself."""

    name: str
    target: Callable[..., None]
    args: tuple[Any, ...] = ()


def start_loops(loops: Iterable[BackgroundLoop]) -> list[threading.Thread]:
    """Each loop on its own daemon thread (a loop never returns, and the process may end while one sleeps)."""
    threads = [threading.Thread(target=loop.target, args=loop.args, name=loop.name, daemon=True) for loop in loops]
    for thread in threads:
        thread.start()
    return threads


@dataclass
class RuntimeContext:
    queue_dir: str
    console: ConsoleActions
    settings: dict[str, Any]
    worker: Any
    http: HTTPClient
    state_dir: str
    config_path: str
    postprocess_targets_path: str
    static_dir: str
    log_dir: str
    bazarr: BazarrIntegration
    backups: BackupMaintenance
    transport: HTTPTransport
    units: Any = None  # UnitService: library, corrections, retranslation, versions, glossaries
    telemetry: Any = None  # Telemetry: the event log behind GET /api/events (P5)
    settings_service: Any = None  # SettingsService: settings and API keys in the database (P6, FR-20)
    cleanup: Any = None  # Cleanup: the retention use case behind POST /api/cleanup (P9, FR-13)
    catalog: Any = None  # CatalogActions: the Library page's use cases, behind /api/catalog and /api/jobs (P10, FR-14, FR-15)
    style: Any = None  # StyleService: a series' style, presets, character cards and the trial of a style (P12, FR-18)
    suggestions: Any = None  # SuggestionService: the Review inbox behind /api/suggestions (P13, FR-4.4, FR-11.1)
    insights: Any = None  # Insights: the figures behind /api/metrics and a series' golden set (P15, FR-19.5, FR-19.6)

    def as_handler_context(self) -> dict[str, Any]:
        return self.__dict__.copy()
