"""CVM account mapping -> StandardizedFinancials (pure, no Mongo)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from smaug.analysis.domain.financials import AccountingRegime
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
        "DFC": {
            "accounts": [
                _acc("6.01", "Caixa Líquido Atividades Operacionais", "500"),
                _acc("6.01.01.04", "Depreciação e amortização", "80"),
                _acc("6.02", "Caixa Líquido Atividades de Investimento", "-260"),
                _acc("6.02.01", "Aquisição de Imobilizado", "-150"),
                _acc("6.02.02", "Aquisição de Intangível", "-40"),
                _acc("6.02.03", "Alienação de Imobilizado", "30"),  # inflow: ignored
                _acc("6.02.04", "Aplicações Financeiras", "-100"),  # not capex
            ]
        },
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
    assert f.cfo == Decimal("500")  # operating cash flow (6.01)
    assert f.capex == Decimal("190")  # 150 + 40 PP&E/intangible outflows only


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


def test_standardize_bank_reads_its_own_chart_of_accounts() -> None:
    # The codes and labels below are the real ones in the raw mirror (BBAS3 DFP).
    # A bank's balance sheet has no current/non-current split and no borrowings
    # line, and its cash sits at 1.01 whole — there is no 1.01.01/1.01.02 to sum.
    by_module = {
        "BPA": {
            "accounts": [
                _acc("1", "Ativo Total", "5000"),
                _acc("1.01", "Caixa e Equivalentes de Caixa", "300"),
                _acc("1.02", "Ativos Financeiros", "4000"),
            ]
        },
        "BPP": {
            "accounts": [
                _acc("2.02", "Passivos Financeiros ao Custo Amortizado", "3900"),
                _acc("2.07", "Patrimônio Líquido Consolidado", "800"),
            ]
        },
        "DRE": {
            "accounts": [
                _acc("3.01", "Receitas de Intermediação Financeira", "400"),
                _acc("3.01.01", "Receita de Juros", "400"),
                _acc("3.02", "Despesas de Intermediação Financeira", "-260"),
                _acc("3.02.01", "Despesa de Juros", "-260"),
                _acc("3.03", "Resultado Bruto de Intermediação Financeira", "140"),
                _acc("3.04.01", "Despesa de Provisão para Perda Esperada", "-70"),
                _acc("3.04.02", "Receitas de Prestação de Serviços", "45"),
                _acc("3.05", "Resultado antes dos Tributos sobre o Lucro", "60"),
                _acc("3.09", "Lucro ou Prejuízo das Operações Continuadas", "90"),
            ]
        },
        "DFC": {
            "accounts": [
                _acc("6.01", "Caixa Líquido das Atividades Operacionais", "500"),
                _acc("6.02.05", "Compra de ativo imobilizado", "-150"),
                _acc("6.02.09", "Aquisição de ativos intangíveis", "-40"),
            ]
        },
    }

    f = standardize(by_module, Sector.BANK, date(2024, 9, 30))

    assert f.total_assets == Decimal("5000")
    assert f.equity == Decimal("800")  # matched by name, code 2.07
    assert f.revenue == Decimal("400")
    assert f.net_income == Decimal("90")
    assert f.cash == Decimal("300")  # 1.01 whole, not 1.01.01 + 1.01.02
    assert f.gross_profit == Decimal("140")  # 3.03 = net interest income
    assert f.ebit == Decimal("60")  # 3.05 = pre-tax profit (ADR 0015)
    assert f.cfo == Decimal("500")
    assert f.capex == Decimal("190")  # 150 + 40
    # The bank-specific lines #27 needs, signed as filed:
    assert f.interest_income == Decimal("400")
    assert f.interest_expense == Decimal("-260")
    assert f.loan_loss_provision == Decimal("-70")
    assert f.fee_income == Decimal("45")
    # Unbuildable from a bank's schema — never read, never guessed. 2.02 above is
    # the bank's funding (deposits), and must not be mistaken for debt.
    assert f.total_debt is None
    assert f.current_assets is None
    assert f.current_liabilities is None
    # D&A is the one line we still skip, and it is recorded as such (ADR 0015).
    assert f.unmapped_fields == frozenset({"dep_amort", "ebitda"})


def test_standardize_insurer_reads_ebit_at_307_and_no_debt_line() -> None:
    # The two dead needles ADR 0005 warns about, both live in the real mirror:
    # 3.05 is EBIT for a corporate filer but "Outras Receitas e Despesas
    # Operacionais" for an insurer (whose EBIT is 3.07), and 2.01.04 is
    # "Empréstimos e Financiamentos" for a corporate filer but "Capitalização" for
    # an insurer. Reading either by code alone silently yields a wrong number.
    by_module = {
        "BPA": {
            "accounts": [
                _acc("1", "Ativo Total", "9000"),
                _acc("1.01", "Ativo Circulante", "4000"),
                _acc("1.01.01", "Caixa e Equivalentes de Caixa", "1200"),
                _acc("1.01.02", "Aplicações Financeiras", "300"),
            ]
        },
        "BPP": {
            "accounts": [
                _acc("2.01", "Passivo Circulante", "2000"),
                _acc("2.01.04", "Capitalização", "999"),  # NOT a borrowings line
                _acc("2.02.01", "Passivo Exigível a Longo Prazo", "777"),  # nor this
                _acc("2.03", "Patrimônio Líquido Consolidado", "5000"),
            ]
        },
        "DRE": {
            "accounts": [
                _acc("3.01", "Receitas das Atividades Seguradoras", "600"),
                _acc("3.01.01", "Receitas com Seguros", "600"),
                _acc("3.02.01", "Despesas com Serviços de Seguros", "-250"),
                _acc("3.03", "Resultado Bruto", "350"),
                _acc("3.05", "Outras Receitas e Despesas Operacionais", "-99"),
                _acc("3.07", "Resultado Antes do Resultado Financeiro", "300"),
                _acc("3.13", "Lucro/Prejuízo Consolidado do Período", "210"),
            ]
        },
    }

    f = standardize(by_module, Sector.INSURER, date(2024, 12, 31))

    assert f.filed_regime is AccountingRegime.INSURANCE
    assert f.ebit == Decimal("300")  # 3.07 — and emphatically not 3.05's -99
    assert f.total_debt is None  # 2.01.04 + 2.02.01 must not be summed here
    assert f.cash == Decimal("1500")  # 1.01.01 + 1.01.02, as for a corporate
    assert f.current_assets == Decimal("4000")  # the split a bank does not file
    assert f.current_liabilities == Decimal("2000")
    assert f.earned_premium == Decimal("600")
    assert f.claims_incurred == Decimal("-250")


def test_standardize_detects_the_filed_regime_from_the_dre_opening_line() -> None:
    # The 3.01 labels below are the real ones in the raw mirror (BBAS3, BBSE3,
    # SAPR11) — the accounting regime is a property of the filing, not of the
    # Sector enum.
    cases = [
        ("Receitas de Intermediação Financeira", AccountingRegime.BANK),
        (
            "Receitas das Atividades Seguradoras/Resseguradoras",
            AccountingRegime.INSURANCE,
        ),
        ("Receita de Venda de Bens e/ou Serviços", AccountingRegime.CORPORATE),
    ]
    for label, regime in cases:
        by_module = {"DRE": {"accounts": [_acc("3.01", label, "100")]}}
        f = standardize(by_module, Sector.COMMODITY, date(2024, 12, 31))
        assert f.filed_regime is regime

    # No DRE, or an unknown opening line -> undetected, never guessed.
    assert standardize({}, Sector.COMMODITY, date(2024, 12, 31)).filed_regime is None
    unknown = {"DRE": {"accounts": [_acc("3.01", "Alguma Outra Coisa", "1")]}}
    assert (
        standardize(unknown, Sector.COMMODITY, date(2024, 12, 31)).filed_regime is None
    )


def test_standardize_maps_the_insurer_that_files_as_a_holding_corporately() -> None:
    # CXSE3 (ADR 0006): sector says insurer, but the DRE opens with the corporate
    # "Receita de Venda" line. ADR 0015: the mapper follows the *filing*, not the
    # sector — so this filer gets the corporate chart of accounts, and its EBIT is
    # read at 3.05. The mismatch is still recorded; the calculator turns it into
    # the "unexpected regime" cause.
    by_module = {
        "BPA": {
            "accounts": [
                _acc("1", "Ativo Total", "3000"),
                _acc("1.01", "Ativo Circulante", "800"),
            ]
        },
        "BPP": {"accounts": [_acc("2.01", "Passivo Circulante", "400")]},
        "DRE": {
            "accounts": [
                _acc("3.01", "Receita de Venda de Bens e/ou Serviços", "900"),
                _acc("3.05", "Resultado Antes do Resultado Financeiro", "250"),
                _acc("3.11", "Lucro/Prejuízo Consolidado do Período", "120"),
            ]
        },
    }

    f = standardize(by_module, Sector.INSURER, date(2024, 12, 31))

    assert f.filed_regime is AccountingRegime.CORPORATE
    assert f.revenue == Decimal("900")
    # It files corporately, so it is mapped corporately — no financial early return:
    assert f.ebit == Decimal("250")  # 3.05, the corporate EBIT
    assert f.current_assets == Decimal("800")
    assert f.current_liabilities == Decimal("400")
    assert f.unmapped_fields == frozenset()


def test_standardize_nonfinancial_has_no_unmapped_fields() -> None:
    f = standardize({}, Sector.COMMODITY, date(2024, 12, 31))
    assert f.unmapped_fields == frozenset()


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


def _dre(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"DRE": {"accounts": accounts}}


def test_standardize_derives_net_income_when_the_controllers_split_is_filed_blank() -> (
    None
):
    # CXSE3's real 2024 DFP (#78): the consolidated total is filed, both halves of
    # the split are left at 0. Read literally, a profitable insurer earns nothing.
    # The identity (controllers = total - minority) says the total IS the
    # controllers' share, which the DMPL independently confirms.
    f = standardize(
        _dre(
            [
                _acc("3.01", "Receita de Venda de Bens e/ou Serviços", "0"),
                _acc("3.11", "Lucro/Prejuízo Consolidado do Período", "3765184"),
                _acc("3.11.01", "Atribuído a Sócios da Empresa Controladora", "0"),
                _acc("3.11.02", "Atribuído a Sócios Não Controladores", "0"),
            ]
        ),
        Sector.INSURER,
        date(2024, 12, 31),
    )

    assert f.net_income == Decimal("3765184")


def test_standardize_reads_the_controllers_line_under_the_total() -> None:
    # BBAS3's real Q3 ITR (#78): the DRE carries the "Atribuído aos Sócios..." pair
    # TWICE — blank under 3.09, filed under 3.11. A whole-statement name search hits
    # the 3.09 zero first and reports the bank as earning nothing, so the search is
    # scoped to the total's own children. The genuine split must still beat the
    # total: 21,992,490 of a 24,031,310 consolidated result is the controllers'.
    f = standardize(
        _dre(
            [
                _acc("3.01", "Receitas de Intermediação Financeira", "201800451"),
                _acc("3.09.01", "Atribuído aos Sócios da Empresa Controladora", "0"),
                _acc("3.09.02", "Atribuído aos Sócios não Controladores", "0"),
                _acc(
                    "3.11",
                    "Lucro ou Prejuízo Líquido Consolidado do Período",
                    "24031310",
                ),
                _acc(
                    "3.11.01",
                    "Atribuído aos Sócios da Empresa Controladora",
                    "21992490",
                ),
                _acc("3.11.02", "Atribuído aos Sócios não Controladores", "2038820"),
            ]
        ),
        Sector.BANK,
        date(2024, 9, 30),
    )

    assert f.net_income == Decimal("21992490")


def test_standardize_keeps_a_controllers_share_that_is_genuinely_zero() -> None:
    # The one shape where a 0 on the controllers' line is real: the minority takes
    # the whole result. The identity yields 0 too, so the fallback cannot inflate it.
    f = standardize(
        _dre(
            [
                _acc("3.01", "Receita de Venda de Bens e/ou Serviços", "900"),
                _acc("3.11", "Lucro/Prejuízo Consolidado do Período", "200"),
                _acc("3.11.01", "Atribuído a Sócios da Empresa Controladora", "0"),
                _acc("3.11.02", "Atribuído a Sócios Não Controladores", "200"),
            ]
        ),
        Sector.COMMODITY,
        date(2024, 12, 31),
    )

    assert f.net_income == Decimal("0")


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


def _filed(
    ref: str,
    value: str,
    *,
    version: int,
    balance_type: str,
    ordem: str = "ULTIMO",
) -> dict[str, Any]:
    """One filing of the DRE as the post-ADR-0016 mirror stores it."""
    return {
        "payload": {
            "reference_date": ref,
            "document_type": "DFP",
            "version": version,
            "balance_type": balance_type,
            "ordem_exerc": ordem,
            "accounts": [_acc("3.01", "Receita", value)],
        },
        "module": "DRE",
        "fetched_at": datetime(2026, 7, 2, tzinfo=UTC),
    }


async def test_reader_selects_amendment_consolidated_and_current_period() -> None:
    # ADR 0016: the mirror hands the reader every filing and picks none of them.
    # The selection ingestion used to bake in now happens here — and only here.
    reader = MongoFundamentalsReader(
        _FakeCollection(
            [
                _filed("2024-12-31", "100", version=1, balance_type="consolidated"),
                _filed("2024-12-31", "999", version=2, balance_type="individual"),
                _filed("2024-12-31", "400", version=2, balance_type="consolidated"),
                # The comparative column of the same filing: the *prior* year's
                # figure, which must never be mistaken for this period's.
                _filed(
                    "2024-12-31",
                    "7",
                    version=2,
                    balance_type="consolidated",
                    ordem="PENULTIMO",
                ),
            ]
        )
    )

    annual = await reader.annual("PETR4")

    assert annual is not None
    assert annual.revenue == Decimal("400")  # v2 consolidated — not v1, ind, or prior


async def test_reader_uses_the_individual_statement_when_it_is_all_there_is() -> None:
    # SAPR11 files no consolidated statement at all — the parent-only one is the
    # filing, not a second-best.
    reader = MongoFundamentalsReader(
        _FakeCollection(
            [_filed("2024-12-31", "500", version=1, balance_type="individual")]
        )
    )

    annual = await reader.annual("SAPR11")

    assert annual is not None
    assert annual.revenue == Decimal("500")
