"""Runtime composition seam for the worker process."""
from dataclasses import dataclass
from typing import Any
from .console import ConsoleActions
from .http import HTTPClient, HTTPTransport
from .bazarr import BazarrIntegration
from .backups import BackupMaintenance

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
