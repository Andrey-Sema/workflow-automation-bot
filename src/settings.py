"""Centralized, validated application configuration.

Replaces ad-hoc `os.getenv(...)` calls scattered across modules. Nothing here
talks to the network or the filesystem beyond reading `.env` and creating the
local data directories, so importing this module is always cheap and safe
(including during test collection, where secrets may be absent).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent


def _parse_int_list(value: object) -> list[int]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [int(chunk) for chunk in value.replace(" ", "").split(",") if chunk]
    if isinstance(value, (list, tuple)):
        return [int(v) for v in value]
    raise TypeError(f"Cannot parse int list from {value!r}")


class Settings(BaseSettings):
    """Application settings loaded from environment / `.env`."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # Lets Settings(gemini_api_key=..., onec_dry_run=...) work by field
        # name (tests, programmatic construction) in addition to loading by
        # alias from the environment (GEMINI_API_KEY, ONEC_DRY_RUN, ...).
        # Without this, `extra="ignore"` silently swallows field-name kwargs
        # instead of raising, which is a nasty trap.
        populate_by_name=True,
    )

    # --- AI ---
    gemini_api_key: SecretStr | None = Field(default=None, alias="GEMINI_API_KEY")
    vision_model_name: str = Field(default="gemini-3.6-flash", alias="VISION_MODEL_NAME")
    text_model_name: str = Field(default="gemini-3.5-flash-lite", alias="TEXT_MODEL_NAME")
    booked_model_name: str = Field(default="gemini-3.5-flash-lite", alias="BOOKED_MODEL_NAME")

    # --- Telegram ---
    telegram_bot_token: SecretStr | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_admin_ids: list[int] = Field(default_factory=list, alias="TELEGRAM_ADMIN_IDS")
    telegram_operator_ids: list[int] = Field(default_factory=list, alias="TELEGRAM_OPERATOR_IDS")
    telegram_log_chat_id: int | None = Field(default=None, alias="TELEGRAM_LOG_CHAT_ID")
    telegram_max_file_mb: int = Field(default=20, alias="TELEGRAM_MAX_FILE_MB")
    # Minimum seconds between two updates from the same chat before the
    # second one is throttled. Guards Gemini quota / duplicate 1C-entry
    # queuing against accidental rapid-fire button mashing, not abuse (the
    # bot is only reachable by allowlisted operators to begin with).
    telegram_throttle_seconds: float = Field(default=0.7, alias="TELEGRAM_THROTTLE_SECONDS")

    # --- 1C / RDP automation ---
    onec_dry_run: bool = Field(default=True, alias="ONEC_DRY_RUN")
    onec_template_confidence: float = Field(default=0.8, alias="ONEC_TEMPLATE_CONFIDENCE")
    onec_keystroke_delay: float = Field(default=0.03, alias="ONEC_KEYSTROKE_DELAY")
    onec_action_pause: float = Field(default=0.4, alias="ONEC_ACTION_PAUSE")

    # How long, at most, a shutdown (SIGTERM) waits for an in-flight 1C
    # entry to finish before giving up and closing anyway. Keep
    # docker-compose's `stop_grace_period` a bit longer than this, or
    # Docker will SIGKILL before this wait completes.
    shutdown_grace_seconds: float = Field(default=45.0, alias="SHUTDOWN_GRACE_SECONDS")

    # --- Paths ---
    data_dir: Path = Field(default=BASE_DIR / "data", alias="DATA_DIR")

    # --- Logging ---
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_json: bool = Field(default=False, alias="LOG_JSON")

    # --- Metrics (Prometheus) ---
    metrics_enabled: bool = Field(default=True, alias="METRICS_ENABLED")
    metrics_port: int = Field(default=9090, alias="METRICS_PORT")

    @field_validator("telegram_admin_ids", "telegram_operator_ids", mode="before")
    @classmethod
    def _split_ids(cls, value: object) -> list[int]:
        return _parse_int_list(value)

    @field_validator("vision_model_name", "text_model_name", "booked_model_name")
    @classmethod
    def _model_name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Model name cannot be blank")
        return value.strip()

    @property
    def input_dir(self) -> Path:
        return self.data_dir / "input"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def output_dir(self) -> Path:
        return self.data_dir / "output"

    @property
    def templates_dir(self) -> Path:
        return self.data_dir / "templates"

    @property
    def catalog_path(self) -> Path:
        return self.data_dir / "catalog.json"

    @property
    def operators_path(self) -> Path:
        return self.data_dir / "operators.json"

    @property
    def orders_db_path(self) -> Path:
        return self.data_dir / "orders.db"

    @property
    def fsm_storage_path(self) -> Path:
        return self.data_dir / "fsm_storage.db"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    def ensure_directories(self) -> None:
        for directory in (
            self.input_dir,
            self.processed_dir,
            self.output_dir,
            self.templates_dir,
            self.log_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def is_admin(self, chat_id: int) -> bool:
        return chat_id in self.telegram_admin_ids

    def is_operator(self, chat_id: int) -> bool:
        return chat_id in self.telegram_operator_ids or self.is_admin(chat_id)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process. Raises a clear error if misconfigured."""
    return Settings()  # type: ignore[call-arg]  # pydantic-settings fills from env


def require_telegram_token(settings: Settings) -> SecretStr:
    if not settings.telegram_bot_token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. Add it to .env before starting the Telegram bot."
        )
    return settings.telegram_bot_token


def require_gemini_api_key(settings: Settings) -> SecretStr:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set. Add it to .env before calling the Gemini API.")
    return settings.gemini_api_key
