"""Ticker -> CVM identity resolution from a synthetic FCA archive.

No network: a small FCA ZIP is built with the real member names and column
headers, placed where the cache would be, and read back through the public
``resolve`` API — the only way a ticker's registrant keys are resolved (#212).
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date
from pathlib import Path

import httpx

from smaug.portfolio.domain.company import (
    CompanyIdentity,
    InstrumentKind,
    fundamental_exclusion,
    is_unit,
    per_share_components,
)
from smaug.portfolio.domain.provenance import FCA_SOURCE
from smaug.portfolio.domain.share_classes import (
    EconomicRightsStatus,
    PerShareClass,
    ShareClassMappingStatus,
    UnitComponent,
)
from smaug.portfolio.infrastructure.cvm_registry import (
    CVM_FCA_BASE_URL,
    CvmCompanyRegistry,
)
from smaug.portfolio.infrastructure.cvm_securities import CvmSecurityHistory
from smaug.shared.artifacts import SourceArtifact

_YEAR = 2024

_GERAL_COLS = (
    "CNPJ_Companhia",
    "Versao",
    "Nome_Empresarial",
    "Codigo_CVM",
    "Situacao_Registro_CVM",
    "Setor_Atividade",
)
_SEC_COLS = (
    "CNPJ_Companhia",
    "Versao",
    "Codigo_Negociacao",
    "Mercado",
    "Data_Fim_Negociacao",
    "Valor_Mobiliario",
    "Composicao_BDR_Unit",
)


def _csv(columns: tuple[str, ...], rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=columns, delimiter=";", extrasaction="ignore"
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("latin-1")


def _write_fca_zip(
    cache_dir: Path,
    geral: list[dict[str, str]],
    securities: list[dict[str, str]],
    *,
    year: int = _YEAR,
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"fca_cia_aberta_{year}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"fca_cia_aberta_geral_{year}.csv", _csv(_GERAL_COLS, geral))
        archive.writestr(
            f"fca_cia_aberta_valor_mobiliario_{year}.csv",
            _csv(_SEC_COLS, securities),
        )


def _registry(cache_dir: Path, *, year: int = _YEAR) -> CvmCompanyRegistry:
    # The cache file exists, so no download runs and the client is never used.
    return CvmCompanyRegistry(httpx.AsyncClient(), year=year, cache_dir=str(cache_dir))


_KLABIN_CNPJ = "89.637.490/0001-45"


async def test_resolves_a_unit_ticker_joining_securities_and_cadastre(
    tmp_path: Path,
) -> None:
    _write_fca_zip(
        tmp_path,
        geral=[
            {
                "CNPJ_Companhia": _KLABIN_CNPJ,
                "Versao": "1",
                "Nome_Empresarial": "KLABIN S.A.",
                "Codigo_CVM": "012653",  # zero-padded as CVM files it
                "Situacao_Registro_CVM": "Ativo",
                "Setor_Atividade": "Papel e Celulose",
            }
        ],
        securities=[
            {
                "CNPJ_Companhia": _KLABIN_CNPJ,
                "Versao": "1",
                "Codigo_Negociacao": "KLBN11",
                "Mercado": "Bolsa",
                "Data_Fim_Negociacao": "",
                "Valor_Mobiliario": "Units",
            }
        ],
    )

    identity = await _registry(tmp_path).resolve("KLBN11")

    assert identity == CompanyIdentity(
        ticker="KLBN11",
        cd_cvm="12653",  # leading zeros stripped to match the statements' key
        cnpj=_KLABIN_CNPJ,
        denom="KLABIN S.A.",
        cvm_sector="Papel e Celulose",
        situation="Ativo",
        instrument_kind=InstrumentKind.UNIT,
        instrument_type="Units",
    )


async def test_resolve_is_case_insensitive_and_unknown_is_none(
    tmp_path: Path,
) -> None:
    _write_fca_zip(
        tmp_path,
        geral=[
            {
                "CNPJ_Companhia": _KLABIN_CNPJ,
                "Versao": "1",
                "Nome_Empresarial": "KLABIN S.A.",
                "Codigo_CVM": "012653",
                "Situacao_Registro_CVM": "Ativo",
                "Setor_Atividade": "Papel e Celulose",
            }
        ],
        securities=[
            {
                "CNPJ_Companhia": _KLABIN_CNPJ,
                "Versao": "1",
                "Codigo_Negociacao": "KLBN11",
                "Mercado": "Bolsa",
                "Data_Fim_Negociacao": "",
            }
        ],
    )
    registry = _registry(tmp_path)

    assert (await registry.resolve("klbn11")) is not None
    assert (await registry.resolve("NOPE99")) is None


async def test_registry_exposes_snapshot_source_provenance(tmp_path: Path) -> None:
    _write_fca_zip(tmp_path, geral=[], securities=[])
    registry = _registry(tmp_path)

    provenance = await registry.provenance()

    assert provenance.year == _YEAR
    assert provenance.source == FCA_SOURCE
    assert provenance.source_url == (f"{CVM_FCA_BASE_URL}/fca_cia_aberta_{_YEAR}.zip")
    assert provenance.artifact_id is None


async def test_resolve_all_skips_unlisted_tickers(tmp_path: Path) -> None:
    _write_fca_zip(
        tmp_path,
        geral=[
            {
                "CNPJ_Companhia": _KLABIN_CNPJ,
                "Versao": "1",
                "Nome_Empresarial": "KLABIN S.A.",
                "Codigo_CVM": "012653",
                "Situacao_Registro_CVM": "Ativo",
                "Setor_Atividade": "Papel e Celulose",
            }
        ],
        securities=[
            {
                "CNPJ_Companhia": _KLABIN_CNPJ,
                "Versao": "1",
                "Codigo_Negociacao": "KLBN11",
                "Mercado": "Bolsa",
                "Data_Fim_Negociacao": "",
            }
        ],
    )

    resolved = await _registry(tmp_path).resolve_all(["KLBN11", "NOPE99"])

    assert set(resolved) == {"KLBN11"}


async def test_delisted_listing_loses_to_a_still_trading_one(tmp_path: Path) -> None:
    other_cnpj = "11.111.111/0001-11"
    _write_fca_zip(
        tmp_path,
        geral=[
            {
                "CNPJ_Companhia": _KLABIN_CNPJ,
                "Versao": "1",
                "Nome_Empresarial": "KLABIN S.A.",
                "Codigo_CVM": "012653",
                "Situacao_Registro_CVM": "Ativo",
                "Setor_Atividade": "Papel e Celulose",
            },
            {
                "CNPJ_Companhia": other_cnpj,
                "Versao": "1",
                "Nome_Empresarial": "OUTRA S.A.",
                "Codigo_CVM": "000999",
                "Situacao_Registro_CVM": "Cancelado",
                "Setor_Atividade": "Outros",
            },
        ],
        securities=[
            # Same ticker reused: a delisted row (has an end date) and a live one.
            {
                "CNPJ_Companhia": other_cnpj,
                "Versao": "1",
                "Codigo_Negociacao": "KLBN11",
                "Mercado": "Bolsa",
                "Data_Fim_Negociacao": "2010-01-01",
            },
            {
                "CNPJ_Companhia": _KLABIN_CNPJ,
                "Versao": "1",
                "Codigo_Negociacao": "KLBN11",
                "Mercado": "Bolsa",
                "Data_Fim_Negociacao": "",
            },
        ],
    )

    identity = await _registry(tmp_path).resolve("KLBN11")

    assert identity is not None
    assert identity.cnpj == _KLABIN_CNPJ  # the still-trading listing won


def test_same_priority_ticker_to_cnpj_collision_is_explicit(
    tmp_path: Path,
) -> None:
    first = "12.000.000/0001-00"
    second = "13.000.000/0001-00"
    _write_fca_zip(
        tmp_path,
        geral=[_cadastre_row(first, "120"), _cadastre_row(second, "130")],
        securities=[
            {
                "CNPJ_Companhia": cnpj,
                "Versao": "1",
                "Codigo_Negociacao": "ABCD3",
                "Mercado": "Bolsa",
                "Data_Fim_Negociacao": "",
                "Valor_Mobiliario": "Ações Ordinárias",
            }
            for cnpj in (first, second)
        ],
    )

    identity = _registry(tmp_path)._build_index(
        tmp_path / f"fca_cia_aberta_{_YEAR}.zip"
    )["ABCD3"]

    assert identity is not None
    assert identity.ambiguous_cnpjs == tuple(sorted((first, second)))
    assert fundamental_exclusion(identity) == (
        f"ambiguous ticker-to-CNPJ mapping ({first}, {second})"
    )


def test_same_cnpj_codes_for_one_class_preserve_unresolved_class_evidence(
    tmp_path: Path,
) -> None:
    cnpj = "14.000.000/0001-00"
    _write_fca_zip(
        tmp_path,
        geral=[_cadastre_row(cnpj, "140")],
        securities=[
            {
                "CNPJ_Companhia": cnpj,
                "Versao": "1",
                "Codigo_Negociacao": code,
                "Mercado": "Bolsa",
                "Data_Fim_Negociacao": "",
                "Valor_Mobiliario": "Ações Ordinárias",
            }
            for code in ("ABCD3", "ABCE3")
        ],
    )

    identity = _registry(tmp_path)._build_index(
        tmp_path / f"fca_cia_aberta_{_YEAR}.zip"
    )["ABCD3"]

    assert identity.share_classes == ()
    assert len(identity.share_class_mappings) == 1
    mapping = identity.share_class_mappings[0]
    assert mapping.status is ShareClassMappingStatus.UNRESOLVED
    assert mapping.economic_rights is EconomicRightsStatus.RESOLVED
    assert mapping.symbol is None
    assert {item.symbol for item in mapping.code_evidence} == {"ABCD3", "ABCE3"}


def _cadastre_row(cnpj: str, code: str) -> dict[str, str]:
    return {
        "CNPJ_Companhia": cnpj,
        "Versao": "1",
        "Nome_Empresarial": "TEST S.A.",
        "Codigo_CVM": code,
        "Situacao_Registro_CVM": "Ativo",
        "Setor_Atividade": "Diversos",
    }


def _classes(identity: object) -> set[tuple[str, str]]:
    return {(c.symbol, c.kind.value) for c in identity.share_classes}  # type: ignore[attr-defined]


async def test_share_classes_from_explicit_rows_and_from_a_unit_composition(
    tmp_path: Path,
) -> None:
    explicit = "10.000.000/0001-00"  # files ON + PN rows directly
    unit_only = "20.000.000/0001-00"  # files only the unit (like Klabin)
    _write_fca_zip(
        tmp_path,
        geral=[_cadastre_row(explicit, "000111"), _cadastre_row(unit_only, "000222")],
        securities=[
            {
                "CNPJ_Companhia": explicit,
                "Codigo_Negociacao": "ABCD3",
                "Mercado": "Bolsa",
                "Data_Fim_Negociacao": "",
                "Valor_Mobiliario": "Ações Ordinárias",
            },
            {
                "CNPJ_Companhia": explicit,
                "Codigo_Negociacao": "ABCD4",
                "Mercado": "Bolsa",
                "Data_Fim_Negociacao": "",
                "Valor_Mobiliario": "Ações Preferenciais",
            },
            {
                "CNPJ_Companhia": unit_only,
                "Codigo_Negociacao": "WXYZ11",
                "Mercado": "Bolsa",
                "Data_Fim_Negociacao": "",
                "Valor_Mobiliario": "Units",
                "Composicao_BDR_Unit": "1 WXYZ3 + 4 WXYZ4",
            },
        ],
    )
    registry = _registry(tmp_path)

    a = await registry.resolve("ABCD3")
    assert a is not None
    assert _classes(a) == {("ABCD3", "common"), ("ABCD4", "preferred")}

    unit = await registry.resolve("WXYZ11")
    assert unit is not None
    assert _classes(unit) == {("WXYZ3", "common"), ("WXYZ4", "preferred")}
    # "1 WXYZ3 + 4 WXYZ4": one unit bundles 1 + 4 = 5 underlying shares (#212).
    assert unit.shares_per_unit == 5
    assert unit.unit_components == (
        UnitComponent(1, PerShareClass.ORDINARY, "WXYZ3"),
        UnitComponent(4, PerShareClass.PREFERRED, "WXYZ4"),
    )
    assert a.shares_per_unit is None  # a plain ON row is not a unit


async def test_all_current_fca_units_parse_textual_or_symbol_compositions(
    tmp_path: Path,
) -> None:
    cases = (
        ("ALUP11", "1 ON E 2 PN", 3),
        ("BRBI11", "2 ações preferenciais e 1 ação ordinária", 3),
        ("ENGI11", "1 ação ordinária e 4 ações preferenciais", 5),
        ("IGTI11", "1 ON e 2 PN", 3),
        ("KLBN11", "1 KLBN3 + 4 KLBN4", 5),
        ("SANB11", "1 ON + 1 PN", 2),
        ("SAPR11", "1 ON e 4 PN", 5),
        ("TAEE11", "1 ON / 2 PN", 3),
    )
    cnpjs = {
        ticker: f"{position:02}.000.000/0001-00"
        for position, (ticker, _composition, _count) in enumerate(cases, start=1)
    }
    _write_fca_zip(
        tmp_path,
        geral=[
            _cadastre_row(cnpjs[ticker], str(position))
            for position, (ticker, _composition, _count) in enumerate(cases, start=1)
        ],
        securities=[
            {
                "CNPJ_Companhia": cnpjs[ticker],
                "Codigo_Negociacao": ticker,
                "Mercado": "Bolsa",
                "Data_Fim_Negociacao": "",
                "Valor_Mobiliario": "Units",
                "Composicao_BDR_Unit": composition,
            }
            for ticker, composition, _count in cases
        ],
    )

    identities = await _registry(tmp_path).resolve_all(
        ticker for ticker, _composition, _count in cases
    )

    assert set(identities) == {ticker for ticker, _composition, _count in cases}
    for ticker, _composition, count in cases:
        identity = identities[ticker]
        assert is_unit(identity)
        assert identity.instrument_kind is InstrumentKind.UNIT
        assert identity.shares_per_unit == count
        assert (
            sum(component.quantity for component in identity.unit_components) == count
        )
        assert per_share_components(identity) == identity.unit_components


async def test_suffix_11_warrants_are_not_units_or_listed_equities(
    tmp_path: Path,
) -> None:
    tickers = ("BEEF11", "CALI11", "IFCM11", "VIVR11")
    cnpjs = {
        ticker: f"{position + 20:02}.000.000/0001-00"
        for position, ticker in enumerate(tickers)
    }
    _write_fca_zip(
        tmp_path,
        geral=[
            _cadastre_row(cnpjs[ticker], str(position + 20))
            for position, ticker in enumerate(tickers)
        ],
        securities=[
            {
                "CNPJ_Companhia": cnpjs[ticker],
                "Codigo_Negociacao": ticker,
                "Mercado": "Bolsa",
                "Data_Fim_Negociacao": "",
                "Valor_Mobiliario": "Bônus de Subscrição",
                "Composicao_BDR_Unit": "04 Ações Ordinárias",
            }
            for ticker in tickers
        ],
    )
    registry = _registry(tmp_path)

    identities = await registry.resolve_all(tickers)

    assert await registry.companies() == ()
    for ticker in tickers:
        identity = identities[ticker]
        assert not is_unit(identity)
        assert identity.instrument_kind is InstrumentKind.SUBSCRIPTION_WARRANT
        assert fundamental_exclusion(identity) == (
            "FCA instrument type is 'Bônus de Subscrição'"
        )


async def test_terminated_codes_remain_diagnosable_but_leave_the_universe(
    tmp_path: Path,
) -> None:
    bmgb_cnpj = "31.000.000/0001-00"
    kepl_cnpj = "32.000.000/0001-00"
    _write_fca_zip(
        tmp_path,
        geral=[
            _cadastre_row(bmgb_cnpj, "31"),
            _cadastre_row(kepl_cnpj, "32"),
        ],
        securities=[
            {
                "CNPJ_Companhia": bmgb_cnpj,
                "Codigo_Negociacao": "BMGB11",
                "Mercado": "Bolsa",
                "Data_Fim_Negociacao": "2019-11-28",
                "Valor_Mobiliario": "Units",
                "Composicao_BDR_Unit": "1 PN + 3 Recibos de Subscrição",
            },
            {
                "CNPJ_Companhia": kepl_cnpj,
                "Codigo_Negociacao": "KEPL11",
                "Mercado": "Bolsa",
                "Data_Fim_Negociacao": "2021-06-15",
                "Valor_Mobiliario": "Bônus de Subscrição",
            },
        ],
    )
    registry = _registry(tmp_path)

    bmgb = await registry.resolve("BMGB11")
    kepl = await registry.resolve("KEPL11")

    assert bmgb is not None
    assert kepl is not None
    assert bmgb.trading_ended == date(2019, 11, 28)
    assert kepl.trading_ended == date(2021, 6, 15)
    assert bmgb.shares_per_unit is None  # do not accept the readable PN fragment
    assert fundamental_exclusion(bmgb) == "trading ended on 2019-11-28"
    assert fundamental_exclusion(kepl) == "trading ended on 2021-06-15"
    assert await registry.companies() == ()


async def test_pn_pna_and_pnb_are_distinct_listed_classes(tmp_path: Path) -> None:
    cnpj = "30.000.000/0001-00"
    _write_fca_zip(
        tmp_path,
        geral=[_cadastre_row(cnpj, "000333")],
        securities=[
            {
                "CNPJ_Companhia": cnpj,
                "Codigo_Negociacao": sym,
                "Mercado": "Bolsa",
                "Data_Fim_Negociacao": "",
                "Valor_Mobiliario": kind,
            }
            for sym, kind in (
                ("EFGH3", "Ações Ordinárias"),
                ("EFGH4", "Ações Preferenciais"),
                ("EFGH5", "Ações Preferenciais Classe A"),
                ("EFGH6", "Ações Preferenciais Classe B"),
            )
        ],
    )

    identity = await _registry(tmp_path).resolve("EFGH3")
    pna = await _registry(tmp_path).resolve("EFGH5")

    assert identity is not None
    assert _classes(identity) == {
        ("EFGH3", "common"),
        ("EFGH4", "preferred"),
        ("EFGH5", "preferred"),
        ("EFGH6", "preferred"),
    }
    assert {item.symbol: item.per_share_class for item in identity.share_classes} == {
        "EFGH3": PerShareClass.ORDINARY,
        "EFGH4": PerShareClass.PREFERRED,
        "EFGH5": PerShareClass.PREFERRED_A,
        "EFGH6": PerShareClass.PREFERRED_B,
    }
    assert pna is not None
    assert per_share_components(pna) == (
        UnitComponent(1, PerShareClass.PREFERRED_A, "EFGH5"),
    )


async def test_companies_group_trading_codes_and_drop_the_archives_non_tickers(
    tmp_path: Path,
) -> None:
    # The real 2024 archive files 41 rows whose Codigo_Negociacao is not a ticker;
    # resolving one at a time never met them, a whole-exchange run meets them all.
    listed, junk = "40.000.000/0001-00", "41.000.000/0001-00"
    _write_fca_zip(
        tmp_path,
        geral=[_cadastre_row(listed, "000444"), _cadastre_row(junk, "000555")],
        securities=[
            {
                "CNPJ_Companhia": listed,
                "Codigo_Negociacao": code,
                "Mercado": "Bolsa",
                "Data_Fim_Negociacao": "",
                "Valor_Mobiliario": kind,
            }
            for code, kind in (
                ("IJKL4", "Ações Preferenciais"),
                ("IJKL3", "Ações Ordinárias"),
            )
        ]
        + [
            {
                "CNPJ_Companhia": junk,
                "Codigo_Negociacao": "1545-8",
                "Mercado": "Bolsa",
                "Data_Fim_Negociacao": "",
                "Valor_Mobiliario": "Ações Ordinárias",
            }
        ],
    )

    companies = await _registry(tmp_path).companies()

    assert len(companies) == 1
    assert companies[0].cd_cvm == "444"
    assert companies[0].ticker == "IJKL3"  # the ON names the company
    assert companies[0].tickers == ("IJKL3", "IJKL4")
    assert companies[0].cnpj == listed


async def test_current_fca_snapshot_is_independent_from_a_filing_year_snapshot(
    tmp_path: Path,
) -> None:
    cnpj = "50.000.000/0001-00"
    current_cnpj = "51.000.000/0001-00"
    _write_fca_zip(
        tmp_path,
        geral=[_cadastre_row(cnpj, "500")],
        securities=[
            {
                "CNPJ_Companhia": cnpj,
                "Versao": "1",
                "Codigo_Negociacao": "OLDE3",
                "Mercado": "Bolsa",
                "Data_Fim_Negociacao": "",
                "Valor_Mobiliario": "Ações Ordinárias",
            }
        ],
        year=2024,
    )
    _write_fca_zip(
        tmp_path,
        geral=[_cadastre_row(cnpj, "500"), _cadastre_row(current_cnpj, "510")],
        securities=[
            {
                "CNPJ_Companhia": cnpj,
                "Versao": "1",
                "Codigo_Negociacao": "OLDE3",
                "Mercado": "Bolsa",
                "Data_Fim_Negociacao": "2025-01-01",
                "Valor_Mobiliario": "Ações Ordinárias",
            },
            {
                "CNPJ_Companhia": current_cnpj,
                "Versao": "1",
                "Codigo_Negociacao": "NEWE3",
                "Mercado": "Bolsa",
                "Data_Fim_Negociacao": "",
                "Valor_Mobiliario": "Ações Ordinárias",
            },
        ],
        year=2026,
    )

    filing_year = _registry(tmp_path, year=2024)
    current = _registry(tmp_path, year=2026)

    assert (await filing_year.companies())[0].ticker == "OLDE3"
    assert (await current.companies())[0].ticker == "NEWE3"
    assert await current.resolve("OLDE3") is not None  # explicit diagnosis only
    assert await current.resolve("NEWE3") is not None


async def test_current_fca_artifact_is_acquired_once_for_registry_and_history(
    tmp_path: Path,
) -> None:
    """A clean run shares the current FCA Bronze artifact with history."""
    cnpj = "52.000.000/0001-00"
    ticker = "ONCE3"
    archive_dir = tmp_path / "archive"
    _write_fca_zip(
        archive_dir,
        geral=[_cadastre_row(cnpj, "520")],
        securities=[
            {
                "CNPJ_Companhia": cnpj,
                "Versao": "1",
                "Codigo_Negociacao": ticker,
                "Mercado": "Bolsa",
                "Data_Fim_Negociacao": "",
                "Valor_Mobiliario": "Ações Ordinárias",
            }
        ],
        year=2026,
    )
    archive_path = archive_dir / "fca_cia_aberta_2026.zip"
    artifact = SourceArtifact(
        artifact_id="sha256:" + "a" * 64,
        sha256="a" * 64,
        byte_size=archive_path.stat().st_size,
        path=archive_path,
        source_url="https://example.test/fca_cia_aberta_2026.zip",
    )

    class CountingStore:
        def __init__(self) -> None:
            self.acquire_calls = 0
            self.open_calls = 0

        async def acquire(
            self, source_url: str, *, follow_redirects: bool = False
        ) -> SourceArtifact:
            del source_url, follow_redirects
            self.acquire_calls += 1
            return artifact

        async def open(self, artifact_id: str) -> SourceArtifact:
            assert artifact_id == artifact.artifact_id
            self.open_calls += 1
            return artifact

    store = CountingStore()
    async with httpx.AsyncClient() as http:
        registry = CvmCompanyRegistry(
            http,
            year=2026,
            cache_dir=str(tmp_path / "registry-cache"),
            artifact_store=store,
        )
        assert await registry.resolve(ticker) is not None
        provenance = await registry.provenance()
        assert provenance.artifact_id == artifact.artifact_id

        history = CvmSecurityHistory(
            http,
            through=2026,
            since=2026,
            cache_dir=str(tmp_path / "history-cache"),
            artifact_store=store,
            snapshot_year=provenance.year,
            snapshot_artifact_id=provenance.artifact_id,
        )
        resolver = await history.resolver()

    assert resolver(ticker) == ()
    assert store.acquire_calls == 1
    assert store.open_calls == 1
