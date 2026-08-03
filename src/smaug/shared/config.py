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

# Where market prices come from. Transitional: "b3" is wired and tested but not
# yet the default — it serves the as-traded price, and the analysis needs the
# corporate-action-adjusted one to match its restated share counts (ADR 0032).
PriceSource = Literal["b3", "vendors"]

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
# DFC = cash flow, DMPL = changes in equity, DVA = value added, DRA = comprehensive
# income. The last three are mirrored but not yet read by an indicator: the mirror
# does not decide what will turn out to be useful (ADR 0016) — the DMPL already
# settled #78, which the DRE alone could not.
#
# The two CAPITAL modules are the odd ones out — share counts, not statements.
# CAPITAL comes from the FRE file (the primary count, ADR 0004); CAPITAL_DFP comes
# from the statements ZIP and is what carries **treasury shares**. Configurable via
# ``CVM_MODULES``.
DEFAULT_CVM_MODULES: tuple[str, ...] = (
    "BPA",
    "BPP",
    "DRE",
    "DFC",
    "DMPL",
    "DVA",
    "DRA",
    "CAPITAL",
    "CAPITAL_DFP",
    # The FRE's declared corporate actions (split/grupamento/bonificação) with
    # their approval date — what ADR 0027 infers from count ratios instead.
    "CAPITAL_EVENT",
    # The same events as **B3** publishes them: no share counts, but the last
    # session quoted on the old base, which is where a price series is cut
    # (ADR 0033). CVM's member stops after the 2023 FRE; B3 has what follows.
    "CAPITAL_EVENT_B3",
    # The cash B3 says the company paid, with the closing price each payment went
    # ex against. No published series carries the dividend-adjusted price, so the
    # third basis is rebuilt from this (ADR 0039).
    "CASH_DIVIDEND_B3",
)


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

    # ---- Yahoo Finance (closed-year price history) ----
    # brapi's free plan withholds multi-year daily history for all but its demo
    # tickers (ADR 0007/0011), so the closed-year averages come from Yahoo's
    # public chart endpoint. No token; a browser-like User-Agent is set per call.
    yahoo_base_url: str = Field(default="https://query1.finance.yahoo.com")

    # ---- B3 (the exchange's own published quote series) ----
    # Which source prices the analysis. "b3" reads COTAHIST, the series B3
    # publishes itself — but it publishes the price **as traded**, while the
    # share counts are restated onto the current base (ADR 0027). Until the
    # corporate-action factors land and put both on the same base, selecting it
    # would overstate the cap of every company that ever split (BBAS3 by 2x), so
    # the default stays on the vendor chain (ADR 0032).
    price_source: PriceSource = Field(default="vendors")
    b3_series_base_url: str = Field(
        default="https://bvmf.bmfbovespa.com.br/InstDados/SerHist"
    )
    # Where the yearly COTAHIST archives and their reductions are cached
    # (gitignored). The archives are large — about 520 MB for 2015-2026 — which
    # is why the reduction beside each one is what a second run reads.
    b3_cache_dir: str = Field(default=".cache/b3")
    # The listed-companies proxy, which serves the corporate-action history
    # (``GetListedSupplementCompany``). A different host from the quote series.
    b3_listed_base_url: str = Field(
        default="https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall"
    )

    # ---- CVM ----
    cvm_modules: tuple[str, ...] = Field(default=DEFAULT_CVM_MODULES)
    # Which CVM document to mirror: DFP = annual closed year (default, used by the
    # historical analysis view), ITR = quarterly. Same statements, different file.
    cvm_document: Literal["ITR", "DFP"] = Field(default="DFP")
    # Year of the CVM file to mirror. 2024 is verified good; bump via ``CVM_YEAR``
    # once a newer year is published in full.
    cvm_year: int = Field(default=2024)
    # Where the downloaded/sanitized CVM ZIPs are cached (gitignored).
    cvm_cache_dir: str = Field(default=".cache/cvm")

    # ---- MongoDB (Phase 1 raw mirror) ----
    mongo_uri: str = Field(default="mongodb://localhost:27017")
    mongo_db: str = Field(default="smaug")

    # ---- PostgreSQL (Phase 2 derived indicators) ----
    postgres_uri: str = Field(
        default="postgresql+asyncpg://smaug:smaug@localhost:5432/smaug"
    )

    # ---- Collection ----
    # Courtesy pacing between calls. It exists for brapi, which makes one HTTP
    # request per ticker/module and rate-limits the free plan.
    request_delay_seconds: float = Field(default=2.0)

    @property
    def active_modules(self) -> tuple[str, ...]:
        """Modules for the currently selected source (brapi vs CVM names)."""
        if self.ingestion_source == "cvm":
            return self.cvm_modules
        return self.brapi_modules

    @property
    def active_delay_seconds(self) -> float:
        """Pacing between calls for the active source — zero for CVM.

        A CVM run downloads the year's archive once and then serves every
        ticker/module from an index in memory, so there is no request to pace:
        pausing between them is waiting between pages of a book already open on
        the desk. It is also the whole difference between a whole-exchange run
        that takes twenty minutes and one that takes two hundred hours
        (368 companies x 9 modules x 11 years x 2s).
        """
        return 0.0 if self.ingestion_source == "cvm" else self.request_delay_seconds

    def require_token(self) -> str:
        """Return the raw token, failing loudly if it is empty."""
        token = self.brapi_token.get_secret_value()
        if not token:
            raise ValueError("BRAPI_TOKEN is empty. Set it in .env before collecting.")
        return token


def get_settings() -> Settings:
    """Build a fresh Settings instance (composition-root helper)."""
    return Settings()
