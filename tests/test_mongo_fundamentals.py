"""CVM account mapping -> StandardizedFinancials (pure, no Mongo)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from smaug.analysis.infrastructure.mongo_fundamentals import (
    MongoFundamentalsReader,
    standardize,
)
from smaug.portfolio.domain.sectors import Sector


def _acc(code: str, name: str, qty: str) -> dict[str, Any]:
    return {"code": code, "name": name, "quantity": qty}


def test_standardize_nonfinancial_pulls_every_line() -> None:
    by_module = {
        "BPA": {
            "accounts": [
                _acc("1", "Ativo Total", "1000"),
                _acc("1.01", "Ativo Circulante", "400"),
                _acc("1.01.01", "Caixa e Equivalentes de Caixa", "100"),
            ]
        },
        "BPP": {
            "accounts": [
                _acc("2.01", "Passivo Circulante", "200"),
                _acc("2.01.04", "Empréstimos e Financiamentos", "50"),
                _acc("2.02.01", "Empréstimos e Financiamentos", "150"),
                _acc("2.03", "Patrimônio Líquido Consolidado", "600"),
            ]
        },
        "DRE": {
            "accounts": [
                _acc("3.01", "Receita de Venda de Bens e/ou Serviços", "900"),
                _acc("3.03", "Resultado Bruto", "300"),
                _acc("3.05", "Resultado Antes do Resultado Financeiro", "200"),
                _acc("3.11", "Lucro/Prejuízo Consolidado do Período", "120"),
            ]
        },
        "DFC": {"accounts": [_acc("6.01.01.04", "Depreciação e amortização", "80")]},
    }

    f = standardize(by_module, Sector.COMMODITY, date(2024, 9, 30))

    assert f.total_assets == Decimal("1000")
    assert f.equity == Decimal("600")
    assert f.net_income == Decimal("120")
    assert f.revenue == Decimal("900")
    assert f.gross_profit == Decimal("300")
    assert f.ebit == Decimal("200")
    assert f.dep_amort == Decimal("80")
    assert f.ebitda == Decimal("280")  # ebit + D&A
    assert f.cash == Decimal("100")
    assert f.current_assets == Decimal("400")
    assert f.current_liabilities == Decimal("200")
    assert f.total_debt == Decimal("200")  # 50 + 150


def test_standardize_applies_currency_size_to_absolute_reais() -> None:
    # CVM reports in thousands; the mapper must scale to keep market ratios sane.
    by_module = {
        "BPA": {
            "currency_size": 1000,
            "accounts": [_acc("1", "Ativo Total", "5")],
        }
    }
    f = standardize(by_module, Sector.COMMODITY, date(2024, 9, 30))
    assert f.total_assets == Decimal("5000")


def test_standardize_bank_pulls_core_and_leaves_rest_none() -> None:
    by_module = {
        "BPA": {
            "accounts": [
                _acc("1", "Ativo Total", "5000"),
                _acc("1.01", "Caixa e Equivalentes de Caixa", "300"),
            ]
        },
        "BPP": {"accounts": [_acc("2.07", "Patrimônio Líquido Consolidado", "800")]},
        "DRE": {
            "accounts": [
                _acc("3.01", "Receitas de Intermediação Financeira", "400"),
                _acc("3.09", "Lucro ou Prejuízo das Operações Continuadas", "90"),
            ]
        },
    }

    f = standardize(by_module, Sector.BANK, date(2024, 9, 30))

    assert f.total_assets == Decimal("5000")
    assert f.equity == Decimal("800")  # matched by name, code 2.07
    assert f.revenue == Decimal("400")
    assert f.net_income == Decimal("90")
    # Not applicable to a bank -> stay None:
    assert f.total_debt is None
    assert f.current_assets is None
    assert f.ebitda is None


def test_standardize_takes_controllers_share_wide_cash_and_dividends() -> None:
    # A normal company exposes the split as consolidated total + a minority
    # sub-line; controllers = total - minority. Cash adds 1.01.02, and dividends
    # come from the DFC financing outflows (minority line excluded).
    by_module = {
        "BPA": {
            "accounts": [
                _acc("1", "Ativo Total", "10000"),
                _acc("1.01", "Ativo Circulante", "4000"),
                _acc("1.01.01", "Caixa e Equivalentes de Caixa", "300"),
                _acc("1.01.02", "Aplicações Financeiras", "200"),
            ]
        },
        "BPP": {
            "accounts": [
                _acc("2.01", "Passivo Circulante", "2000"),
                _acc("2.03", "Patrimônio Líquido Consolidado", "1000"),
                _acc("2.03.09", "Participação dos Acionistas Não Controladores", "100"),
            ]
        },
        "DRE": {
            "accounts": [
                _acc("3.01", "Receita de Venda de Bens e/ou Serviços", "900"),
                _acc("3.11", "Lucro/Prejuízo Consolidado do Período", "200"),
                _acc("3.11.01", "Atribuído a Sócios da Empresa Controladora", "180"),
                _acc("3.11.02", "Atribuído a Sócios Não Controladores", "20"),
            ]
        },
        "DFC": {
            "period_start_date": "2025-01-01",
            "accounts": [
                _acc("6.03.05", "Dividendos pagos aos controladores", "-50"),
                _acc("6.03.06", "Dividendos pagos aos não controladores", "-5"),
            ],
        },
    }

    f = standardize(by_module, Sector.COMMODITY, date(2025, 12, 31))

    assert f.equity == Decimal("900")  # 1000 consolidated - 100 minority
    assert f.net_income == Decimal("180")  # explicit controllers line
    assert f.cash == Decimal("500")  # 1.01.01 + 1.01.02
    assert f.dividends_paid == Decimal("50")  # abs, minority line excluded
    assert f.dfc_period_start == date(2025, 1, 1)


def test_standardize_bank_uses_explicit_controllers_line() -> None:
    # Banks file an explicit "attributed to the controller" line for both equity
    # and net income; the mapper must prefer it over the consolidated total.
    by_module = {
        "BPA": {"accounts": [_acc("1", "Ativo Total", "9000")]},
        "BPP": {
            "accounts": [
                _acc("2.07", "Patrimônio Líquido Consolidado", "2000"),
                _acc("2.07.01", "Patrimônio Líquido Atribuído ao Controlador", "1900"),
                _acc(
                    "2.07.02",
                    "Patrimônio Líquido Atribuído aos Não Controladores",
                    "100",
                ),
            ]
        },
        "DRE": {
            "accounts": [
                _acc("3.01", "Receitas de Intermediação Financeira", "500"),
                _acc("3.11", "Lucro ou Prejuízo Líquido Consolidado do Período", "300"),
                _acc("3.11.01", "Atribuído aos Sócios da Empresa Controladora", "250"),
                _acc("3.11.02", "Atribuído aos Sócios não Controladores", "50"),
            ]
        },
        "DFC": {
            "accounts": [
                _acc(
                    "6.03.04",
                    "Dividendos ou juros sobre o capital próprio pagos aos "
                    "acionistas controladores",
                    "-30",
                ),
            ]
        },
    }

    f = standardize(by_module, Sector.BANK, date(2025, 12, 31))

    assert f.equity == Decimal("1900")  # explicit controller line, not 2000
    assert f.net_income == Decimal("250")  # explicit controller line, not 300
    assert f.dividends_paid == Decimal("30")  # dividends + JCP paid to controllers


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    async def to_list(self, _length: int | None) -> list[dict[str, Any]]:
        return self._docs


class _FakeCollection:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def find(self, _filter: dict[str, Any]) -> _FakeCursor:
        return _FakeCursor(self._docs)


def _doc(
    module: str,
    ref: str,
    accounts: list[dict[str, Any]],
    *,
    document_type: str | None = None,
    period_start: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"reference_date": ref, "accounts": accounts}
    if document_type is not None:
        payload["document_type"] = document_type
    if period_start is not None:
        payload["period_start_date"] = period_start
    return {
        "payload": payload,
        "module": module,
        "fetched_at": datetime(2026, 7, 2, tzinfo=UTC),
    }


async def test_history_returns_quarters_and_annual_returns_the_dfp() -> None:
    # An ITR quarter (September) and the annual DFP (December) coexist. history()
    # serves the quarters (raw material for the TTM); annual() serves the DFP.
    reader = MongoFundamentalsReader(
        _FakeCollection(
            [
                _doc(
                    "DRE",
                    "2024-09-30",
                    [_acc("3.01", "Receita", "100")],
                    document_type="ITR",
                    period_start="2024-07-01",
                ),
                _doc(
                    "DRE",
                    "2024-12-31",
                    [_acc("3.01", "Receita", "400")],
                    document_type="DFP",
                ),
            ]
        )
    )

    history = await reader.history("PETR4")
    annual = await reader.annual("PETR4")

    assert [f.reference_date for f in history] == [date(2024, 9, 30)]
    assert history[0].revenue == Decimal("100")
    assert history[0].period_start == date(2024, 7, 1)  # read from the payload
    assert annual is not None
    assert annual.reference_date == date(2024, 12, 31)
    assert annual.revenue == Decimal("400")
