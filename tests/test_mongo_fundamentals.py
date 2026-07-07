"""CVM account mapping -> StandardizedFinancials (pure, no Mongo)."""

from datetime import date
from decimal import Decimal
from typing import Any

from smaug.analysis.infrastructure.mongo_fundamentals import standardize
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
