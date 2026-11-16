"""Production and local dependency construction."""

from functools import lru_cache

from .config import AppConfig
from .jobs import CloudRunJobLauncher, InMemoryJobLauncher, JobLauncher
from .sheets import GoogleSheetRepository, InMemorySheetRepository, SheetRepository
from .state import FirestoreStateStore, InMemoryStateStore, StateStore


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    return AppConfig()


@lru_cache(maxsize=1)
def get_state() -> StateStore:
    config = get_config()
    if config.state_backend == "firestore":
        return FirestoreStateStore(config.project_id, config.spreadsheet_id)
    return InMemoryStateStore()


@lru_cache(maxsize=1)
def get_sheets() -> SheetRepository:
    config = get_config()
    if config.sheets_backend == "google":
        return GoogleSheetRepository(config.spreadsheet_id)
    return InMemorySheetRepository()


@lru_cache(maxsize=1)
def get_launcher() -> JobLauncher:
    config = get_config()
    if config.job_backend == "cloud_run":
        return CloudRunJobLauncher(config.job_resource)
    return InMemoryJobLauncher()
