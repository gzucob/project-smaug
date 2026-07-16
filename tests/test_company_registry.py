"""Ticker -> CVM identity resolution from a synthetic FCA archive.

No network: a small FCA ZIP is built with the real member names and column
headers, placed where the cache would be, and read back through the public
``resolve`` API. The last test proves the registry reproduces the hand-curated
``cvm_codes.py`` keys for the nine — the contract that lets it stand in for them.
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import httpx

from smaug.portfolio.domain.company import CompanyIdentity
from smaug.portfolio.domain.cvm_codes import TICKER_TO_CNPJ, TICKER_TO_CVM_CODE
from smaug.portfolio.infrastructure.cvm_registry import CvmCompanyRegistry

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
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"fca_cia_aberta_{_YEAR}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"fca_cia_aberta_geral_{_YEAR}.csv", _csv(_GERAL_COLS, geral))
        archive.writestr(
            f"fca_cia_aberta_valor_mobiliario_{_YEAR}.csv",
            _csv(_SEC_COLS, securities),
        )


def _registry(cache_dir: Path) -> CvmCompanyRegistry:
    # The cache file exists, so no download runs and the client is never used.
    return CvmCompanyRegistry(httpx.AsyncClient(), year=_YEAR, cache_dir=str(cache_dir))


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


async def test_registry_reproduces_the_curated_nine(tmp_path: Path) -> None:
    """The registry must resolve the nine to the same keys ``cvm_codes.py`` curates.

    This is the contract that lets the registry stand in for the hand maps: build
    an FCA archive from the curated codes/CNPJs and assert it round-trips.
    """
    geral: list[dict[str, str]] = []
    securities: list[dict[str, str]] = []
    for ticker, code in TICKER_TO_CVM_CODE.items():
        cnpj = TICKER_TO_CNPJ[ticker]
        geral.append(
            {
                "CNPJ_Companhia": cnpj,
                "Versao": "1",
                "Nome_Empresarial": ticker,
                "Codigo_CVM": code.zfill(6),  # CVM files it zero-padded
                "Situacao_Registro_CVM": "Ativo",
                "Setor_Atividade": "Diversos",
            }
        )
        securities.append(
            {
                "CNPJ_Companhia": cnpj,
                "Versao": "1",
                "Codigo_Negociacao": ticker,
                "Mercado": "Bolsa",
                "Data_Fim_Negociacao": "",
            }
        )
    _write_fca_zip(tmp_path, geral, securities)

    resolved = await _registry(tmp_path).resolve_all(TICKER_TO_CVM_CODE.keys())

    assert {t: i.cd_cvm for t, i in resolved.items()} == TICKER_TO_CVM_CODE
    assert {t: i.cnpj for t, i in resolved.items()} == TICKER_TO_CNPJ


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


async def test_two_classes_of_the_same_kind_yield_no_classes(tmp_path: Path) -> None:
    # Per-kind filed counts cannot price two PN classes without double-counting, so
    # an ambiguous company gets no classes (the cap stays a named null), not a guess.
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
            )
        ],
    )

    identity = await _registry(tmp_path).resolve("EFGH3")

    assert identity is not None
    assert identity.share_classes == ()
