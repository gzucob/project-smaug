"""Application settings, loaded from environment / ``.env``.

Single source of truth for secrets and knobs. The brapi token lives only
here (via env), never hardcoded — the repo is public.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# The active raw data source. Only one is active per run (``INGESTION_SOURCE``),
# but both implement the same ``RawDataSource`` port, so switching is a config
# change — never a rewrite. brapi is kept for the day the paid plan is bought.
IngestionSource = Literal["brapi", "cvm"]

# Default brapi modules to collect. Names are configurable via ``BRAPI_MODULES``
# (comma-separated) so they can be corrected against the live docs without a
# code change. "dividends" is a pseudo-module: the client requests it via the
# ``dividends=true`` flag instead of the ``modules=`` param.
DEFAULT_BRAPI_MODULES: tuple[str, ...] = (
    "balanceSheetHistoryQuarterly",
    "incomeStatementHistoryQuarterly",
    "cashflowHistoryQuarterly",
    "defaultKeyStatistics",
    "financialData",
    "dividends",
)

# Default CVM "modules" — the regulated statement types, not brapi module names.
# BPA/BPP = balance sheet (assets / liabilities+equity), DRE = income,
# DFC = cash flow. Configurable via ``CVM_MODULES``.
DEFAULT_CVM_MODULES: tuple[str, ...] = ("BPA", "BPP", "DRE", "DFC")


class Settings(BaseSettings):
    """Environment-backed configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Active source ----
    ingestion_source: IngestionSource = Field(default="cvm")

    # ---- brapi ----
    brapi_token: SecretStr = Field(default=SecretStr(""))
    brapi_base_url: str = Field(default="https://brapi.dev/api")
    brapi_modules: tuple[str, ...] = Field(default=DEFAULT_BRAPI_MODULES)

    # ---- CVM ----
    cvm_modules: tuple[str, ...] = Field(default=DEFAULT_CVM_MODULES)
    # Year of the ITR (quarterly) file to mirror. 2024 is verified good; bump
    # via ``CVM_YEAR`` once a newer year is published in full.
    cvm_year: int = Field(default=2024)
    # Where the downloaded/sanitized CVM ZIPs are cached (gitignored).
    cvm_cache_dir: str = Field(default=".cache/cvm")

    # ---- MongoDB ----
    mongo_uri: str = Field(default="mongodb://localhost:27017")
    mongo_db: str = Field(default="smaug")

    # ---- Collection ----
    request_delay_seconds: float = Field(default=2.0)

    @property
    def active_modules(self) -> tuple[str, ...]:
        """Modules for the currently selected source (brapi vs CVM names)."""
        if self.ingestion_source == "cvm":
            return self.cvm_modules
        return self.brapi_modules

    def require_token(self) -> str:
        """Return the raw token, failing loudly if it is empty."""
        token = self.brapi_token.get_secret_value()
        if not token:
            raise ValueError("BRAPI_TOKEN is empty. Set it in .env before collecting.")
        return token


def get_settings() -> Settings:
    """Build a fresh Settings instance (composition-root helper)."""
    return Settings()
