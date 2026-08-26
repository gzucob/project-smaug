"""Application settings, loaded from environment / ``.env``.

Single source of truth for knobs. Every source is public and unauthenticated —
CVM's archives and B3's own files and endpoints — so there is no secret here to
keep out of a public repo (ADR 0041).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# The CVM "modules" — the regulated statement types.
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

# 2026 is the latest complete FCA publication at the time this contract was
# introduced. Keep the selection explicit and reproducible; advancing it is a
# deliberate configuration change, not an implicit date-based lookup.
DEFAULT_CVM_FCA_YEAR = 2026


class Settings(BaseSettings):
    """Environment-backed configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- B3 (the exchange's own published quote series) ----
    # COTAHIST prices the analysis, and nothing else does: the series the
    # exchange publishes itself (ADR 0032), put on the same base as the share
    # counts session by session (ADRs 0027/0033) and carrying the dividend basis
    # rebuilt from B3's payouts (ADR 0039). The vendor chain that preceded it is
    # gone (ADR 0041) — an absent price now reads as missing, loudly, instead of
    # being answered by a second source on another basis.
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
    # Year of the complete FCA snapshot used for current identity and universe
    # selection. This is intentionally independent from ``cvm_year``: the
    # accounting archive may remain on a verified closed year while the current
    # listed universe advances to a newer FCA publication.
    cvm_fca_year: int = Field(default=DEFAULT_CVM_FCA_YEAR)
    # Where the downloaded/sanitized CVM ZIPs are cached (gitignored).
    cvm_cache_dir: str = Field(default=".cache/cvm")
    # Durable Bronze archive storage. Content is immutable and has no automatic
    # eviction; cache cleanup must never remove ingestion provenance.
    source_artifact_dir: str = Field(default=".artifacts/sources")

    # ---- MongoDB (Phase 1 raw mirror) ----
    mongo_uri: str = Field(default="mongodb://localhost:27017")
    mongo_db: str = Field(default="smaug")

    # ---- PostgreSQL (Phase 2 derived indicators) ----
    postgres_uri: str = Field(
        default="postgresql+asyncpg://smaug:smaug@localhost:5432/smaug"
    )

    # ---- API (Phase 2 read API + the portfolio's write surface, #151) ----
    # Every existing read is fetched server-side by the frontend (RULES_FRONTEND:
    # "no CORS surface"). The one exception is the favorite-ticker toggle, proxied
    # through a same-origin Next.js route — so this only ever needs to admit that
    # one server, never the browser directly.
    api_cors_origins: tuple[str, ...] = Field(default=("http://localhost:3000",))


def get_settings() -> Settings:
    """Build a fresh Settings instance (composition-root helper)."""
    return Settings()
