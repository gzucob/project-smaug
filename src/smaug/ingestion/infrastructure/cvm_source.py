"""CVM raw data source — skeleton plugged into the ``RawDataSource`` port.

This is the *seam* that lets CVM stand in for brapi without touching the use
case: it implements the same port (``fetch(ticker, module) -> RawFetchResult``).
The parsing itself is not implemented yet — this ships only the wiring so the
source can be selected via ``INGESTION_SOURCE`` while brapi stays untouched.

Real implementation plan (future PR):
  1. Map ticker -> CVM code (e.g. BBAS3 -> 1023) in the ``portfolio`` context.
  2. Download the yearly ITR/DFP ZIP from dados.cvm.gov.br (httpx), cached.
  3. Apply the DMPL workaround (empty the DMPL CSV members: pycvm's DMPL parser
     crashes on the real 2024 ITR with ``KeyError: 'Patrimônio Líquido'``).
  4. Parse with pycvm and pick the statement for ``module`` (BPA/BPP/DRE/DFC),
     packing the raw accounts (code/name/quantity) into a ``RawFetchResult``.
"""

from __future__ import annotations

from smaug.ingestion.domain.ports import RawFetchResult


class CvmDataSource:
    """Fetch one statement (module) for one ticker from CVM open data."""

    async def fetch(self, ticker: str, module: str) -> RawFetchResult:
        """Not implemented yet — see the module docstring for the real plan."""
        raise NotImplementedError(
            f"CVM ingestion is not implemented yet (asked for {ticker}/{module}). "
            "Set INGESTION_SOURCE=brapi to collect, or wait for the CVM PR."
        )
