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

    def as_handler_context(self) -> dict[str, Any]:
        return self.__dict__.copy()
