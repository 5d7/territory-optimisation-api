"""Runtime configuration sourced from environment variables."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TERRITORY_", env_file=".env", extra="ignore")

    project_id: str = "local-project"
    region: str = "europe-west2"
    spreadsheet_id: str = "local-spreadsheet"
    job_name: str = "territory-optimisation-worker"
    hmac_secret: str = Field(default="local-development-secret", min_length=16)
    state_backend: Literal["memory", "firestore"] = "memory"
    sheets_backend: Literal["memory", "google"] = "memory"
    job_backend: Literal["memory", "cloud_run"] = "memory"
    engine_version: str = "development"
    signature_ttl_seconds: int = 300
    lease_seconds: int = 2700
    run_history_limit: int = 500

    @property
    def job_resource(self) -> str:
        return f"projects/{self.project_id}/locations/{self.region}/jobs/{self.job_name}"
