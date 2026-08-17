"""CVM account mapping -> StandardizedFinancials (pure, no Mongo)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from smaug.analysis.domain.financials import (
    AccountingRegime,
    DebtBlocker,
    DebtIdentityStatus,
    DebtInstrument,
    DebtLineClassification,
    DebtLineRole,
    IssuerIdentity,
    RegimeSource,
)
from smaug.analysis.domain.indicators import NullReason
from smaug.analysis.infrastructure.mongo_fundamentals import (
    MongoFundamentalsReader,
    standardize,
)
from smaug.portfolio.domain.sectors import Sector
from smaug.portfolio.domain.share_classes import PerShareClass, UnitComponent
from tests.fakes import fake_sector_resolver


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
                # CPC 06 liability disclosed outside the fixed debt parents.
                _acc("2.02.02.02.07", "Passivo de Arrendamento", "25"),
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
    assert f.cash_equivalents == Decimal("100")
    assert f.current_assets == Decimal("400")
    assert f.current_liabilities == Decimal("200")
    assert f.total_debt == Decimal("225")  # 50 + 150 + explicit lease liability
    assert f.debt_coverage_null_reason is None
    assert f.debt_evidence is not None
    assert f.debt_evidence.identity_status is DebtIdentityStatus.UNKNOWN
    assert [line.code for line in f.debt_evidence.used_lines] == [
        "2.01.04",
        "2.02.01",
        "2.02.02.02.07",
    ]
    assert f.debt_evidence.used_lines[-1].role is DebtLineRole.INCLUDED_INSTRUMENT
    assert f.debt_evidence.included_instruments == (
        "2.01.04",
        "2.02.01",
        "2.02.02.02.07",
    )
    assert f.debt_evidence.primary_blocker is None
    assert f.debt_evidence.secondary_blockers == ()
    assert f.cfo == Decimal("500")  # operating cash flow (6.01)
    assert f.capex == Decimal("190")  # 150 + 40 PP&E/intangible outflows only


def test_standardize_builds_a_named_bpp_perimeter_without_double_counting_details() -> (
    None
):
    # The BPP hierarchy is the primary evidence. Parent aggregates are selected
    # once, their child details are recorded as excluded, and named instruments
    # outside those parents are classified independently.
    by_module = {
        "BPP": {
            "accounts": [
                _acc("2.01.04", "Empréstimos e Financiamentos", "100"),
                _acc("2.01.04.01", "Empréstimos bancários", "100"),
                _acc("2.02.01", "Empréstimos e Financiamentos", "200"),
                _acc("2.02.03", "Debêntures", "40"),
                _acc("2.02.03.01", "Debêntures - Série A", "40"),
                _acc("2.02.04", "Passivo de Arrendamento", "10"),
                _acc("2.02.05", "Mútuos", "20"),
                _acc("2.02.06", "Securitização de Recebíveis", "30"),
                _acc("2.02.07", "Financiamento de aquisição via CCI", "50"),
                _acc("2.02.08", "Passivos financeiros", "0"),
                _acc("2.02.09", "Passivos de Contratos de Seguros", "70"),
                _acc("2.02.10", "Provisões Técnicas de Seguros", "80"),
                _acc("2.02.11", "Obrigações com planos de previdência", "90"),
                _acc("2.02.12", "Capitalização", "100"),
                _acc("2.02.13", "Derivativos", "110"),
            ]
        },
        "DRE": {
            "accounts": [_acc("3.01", "Receita de Venda de Bens e/ou Serviços", "100")]
        },
    }

    f = standardize(by_module, Sector.INDUSTRY, date(2025, 12, 31))

    assert f.total_debt == Decimal("450")
    assert f.debt_evidence is not None
    evidence = f.debt_evidence
    assert evidence.regime is AccountingRegime.CORPORATE
    lines = {
        line.code: line for line in (*evidence.used_lines, *evidence.excluded_lines)
    }
    assert lines["2.02.03"].instrument is DebtInstrument.DEBENTURES_SUBORDINATED
    assert lines["2.02.04"].instrument is DebtInstrument.LEASES
    assert lines["2.02.05"].instrument is DebtInstrument.MUTUOS
    assert lines["2.02.06"].instrument is DebtInstrument.SECURITIZATION_RECEIVABLES
    assert lines["2.02.07"].instrument is DebtInstrument.ACQUISITION_DEBT_CCI
    assert lines["2.02.08"].classification is DebtLineClassification.AMBIGUOUS
    assert lines["2.02.08"].reason is DebtBlocker.AMBIGUOUS_FINANCIAL_LIABILITY
    for code in ("2.02.09", "2.02.10", "2.02.11", "2.02.12", "2.02.13"):
        assert lines[code].classification is DebtLineClassification.EXCLUDED
        assert lines[code].reason is DebtBlocker.NON_DEBT_LIABILITY
    for code in ("2.01.04.01", "2.02.03.01"):
        assert lines[code].classification is DebtLineClassification.EXCLUDED
        assert lines[code].reason is DebtBlocker.CHILD_DETAIL_DOUBLE_COUNT
    assert evidence.included_instruments == (
        "2.01.04",
        "2.02.01",
        "2.02.03",
        "2.02.04",
        "2.02.05",
        "2.02.06",
        "2.02.07",
    )


@pytest.mark.parametrize(
    ("ticker", "instrument_name", "instrument", "amount"),
    [
        ("WEG3", "Debêntures", DebtInstrument.DEBENTURES_SUBORDINATED, "30"),
        ("VALE3", "Mútuos", DebtInstrument.MUTUOS, "40"),
        (
            "PETR4",
            "Securitização de Recebíveis",
            DebtInstrument.SECURITIZATION_RECEIVABLES,
            "50",
        ),
    ],
)
def test_explicit_perimeter_samples_use_primary_bpp_evidence(
    ticker: str,
    instrument_name: str,
    instrument: DebtInstrument,
    amount: str,
) -> None:
    by_module = {
        "BPP": {
            "accounts": [
                _acc("2.01.04", "Empréstimos e Financiamentos", "100"),
                _acc("2.02.01", "Empréstimos e Financiamentos", "200"),
                _acc("2.02.03", instrument_name, amount),
            ]
        },
        "DRE": {
            "accounts": [_acc("3.01", "Receita de Venda de Bens e/ou Serviços", "100")]
        },
    }

    f = standardize(by_module, Sector.INDUSTRY, date(2025, 12, 31))

    assert f.total_debt == Decimal("300") + Decimal(amount)
    assert f.debt_evidence is not None
    selected = {line.code: line for line in f.debt_evidence.used_lines}
    assert selected["2.02.03"].instrument is instrument
    assert selected["2.02.03"].classification is DebtLineClassification.INCLUDED
    assert ticker in ("WEG3", "VALE3", "PETR4")


def test_standardize_sums_the_plural_and_split_dep_amort_lines() -> None:
    # The real shapes that made EBITDA null or undercounted (#114): LREN3/VIVT3/
    # SAPR11 file the plural "Depreciações e amortizações" (no singular substring
    # to match), and HAPV3/TAEE11 split the right-of-use charge into sibling
    # lines — HAPV3's 2025 DFP has no combined line at all. KLBN11 files
    # depletion separately. All must be summed; the financing section's
    # "Amortização de empréstimos" (6.03) and a bank-shaped "custo amortizado"
    # line must not be.
    by_module = {
        "DRE": {
            "accounts": [
                _acc("3.01", "Receita de Venda de Bens e/ou Serviços", "900"),
                _acc("3.05", "Resultado Antes do Resultado Financeiro", "200"),
            ]
        },
        "DFC": {
            "accounts": [
                _acc("6.01", "Caixa Líquido Atividades Operacionais", "500"),
                _acc("6.01.01.02", "Depreciações e Amortizações", "80"),
                _acc("6.01.01.03", "Amortização de direito de uso", "15"),
                _acc("6.01.01.16", "Depreciação de direito de uso", "5"),
                _acc("6.01.01.17", "Exaustão dos ativos biológicos", "10"),
                # Not D&A: a repayment, and an amortized-cost financial line.
                _acc("6.01.01.20", "Ativos financeiros ao custo amortizado", "999"),
                _acc("6.03.02", "Amortizações de Financiamentos", "-305"),
            ]
        },
    }

    f = standardize(by_module, Sector.COMMODITY, date(2024, 12, 31))

    assert f.dep_amort == Decimal("110")  # 80 + 15 + 5 + 10
    assert f.ebitda == Decimal("310")  # ebit 200 + D&A 110


def test_standardize_does_not_double_count_a_dep_amort_breakdown() -> None:
    # A parent D&A line and its own sub-lines: only the parent is summed.
    by_module = {
        "DFC": {
            "accounts": [
                _acc("6.01.01.02", "Depreciações e amortizações", "100"),
                _acc("6.01.01.02.01", "Depreciação de imobilizado", "70"),
                _acc("6.01.01.02.02", "Amortização de intangível", "30"),
            ]
        },
    }

    f = standardize(by_module, Sector.COMMODITY, date(2024, 12, 31))

    assert f.dep_amort == Decimal("100")


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


def test_standardize_reads_cpc41_by_label_without_currency_scaling() -> None:
    # Real CVM shape: the DRE is in thousands, while 3.99 is already reais per
    # share. Code position is not class identity — the leaf label is.
    by_module = {
        "DRE": {
            "currency_size": 1000,
            "accounts": [
                _acc("3.99.01.01", "ON", "1.5500000000"),
                _acc("3.99.01.02", "PN", "1.7100000000"),
                _acc("3.99.02.01", "ON", "1.5000000000"),
                _acc("3.99.02.02", "PN", "1.6500000000"),
            ],
        }
    }

    ordinary = standardize(
        by_module,
        Sector.BANK,
        date(2024, 12, 31),
        per_share_components=(UnitComponent(1, PerShareClass.ORDINARY),),
    )
    preferred = standardize(
        by_module,
        Sector.BANK,
        date(2024, 12, 31),
        per_share_components=(UnitComponent(1, PerShareClass.PREFERRED),),
    )

    assert ordinary.eps_basic == Decimal("1.5500000000")
    assert ordinary.eps_diluted == Decimal("1.5000000000")
    assert preferred.eps_basic == Decimal("1.7100000000")
    assert preferred.eps_diluted == Decimal("1.6500000000")


def test_standardize_reconciles_equal_class_cpc41_results_for_ttm() -> None:
    filing = {
        "DRE": {
            "accounts": [
                _acc("3.01", "Receita de Venda de Bens e/ou Serviços", "900"),
                _acc("3.11", "Lucro/Prejuízo Consolidado do Período", "120"),
                _acc("3.99.01.01", "ON", "1.00"),
                _acc("3.99.01.02", "PN", "1.00"),
                _acc("3.99.02.01", "ON", "1.00"),
                _acc("3.99.02.02", "PN", "1.00"),
            ]
        }
    }

    result = standardize(
        filing,
        Sector.UTILITY,
        date(2024, 12, 31),
        per_share_components=(UnitComponent(1, PerShareClass.ORDINARY),),
        per_share_classes=(PerShareClass.ORDINARY, PerShareClass.PREFERRED),
    )

    assert result.net_income == Decimal(120)
    assert result.eps_basic == Decimal("1.00")
    assert result.cpc41 is not None
    assert result.cpc41.basic_base_eps == Decimal("1.00")
    assert result.cpc41.diluted_base_eps == Decimal("1.00")
    assert result.cpc41.security_multiplier == Decimal(1)


def test_standardize_refuses_ttm_reconciliation_for_unequal_class_rights() -> None:
    filing = {
        "DRE": {
            "accounts": [
                _acc("3.11", "Lucro/Prejuízo Consolidado do Período", "120"),
                _acc("3.99.01.01", "ON", "1.00"),
                _acc("3.99.01.02", "PN", "1.20"),
                _acc("3.99.02.01", "ON", "1.00"),
                _acc("3.99.02.02", "PN", "1.20"),
            ]
        }
    }

    result = standardize(
        filing,
        Sector.UTILITY,
        date(2024, 12, 31),
        per_share_components=(UnitComponent(1, PerShareClass.ORDINARY),),
        per_share_classes=(PerShareClass.ORDINARY, PerShareClass.PREFERRED),
    )

    assert result.eps_basic == Decimal("1.00")
    assert result.cpc41 is None


def test_standardize_nulls_diluted_with_unobservable_potential_shares() -> None:
    filing = {
        "DRE": {
            "accounts": [
                _acc("3.11", "Lucro/Prejuízo Consolidado do Período", "120"),
                _acc("3.99.01.01", "ON", "1.00"),
                _acc("3.99.01.02", "PN", "1.00"),
                _acc("3.99.02.01", "ON", "0.90"),
                _acc("3.99.02.02", "PN", "0.90"),
            ]
        }
    }

    result = standardize(
        filing,
        Sector.UTILITY,
        date(2024, 12, 31),
        per_share_components=(UnitComponent(1, PerShareClass.ORDINARY),),
        per_share_classes=(PerShareClass.ORDINARY, PerShareClass.PREFERRED),
    )

    assert result.cpc41 is not None
    assert result.cpc41.basic_base_eps == Decimal("1.00")
    assert result.cpc41.diluted_base_eps is None


def test_standardize_composes_a_unit_from_unequal_class_rights() -> None:
    # SAPR11-like bundle: 1 ON + 4 PN. The per-unit result is the economic sum,
    # not group profit divided by a closing count of underlying shares.
    filing = {
        "DRE": {
            "accounts": [
                _acc("3.99.01.02", "PN", "1.10"),
                _acc("3.99.01.01", "ON", "1.00"),
                _acc("3.99.02.02", "PN", "1.05"),
                _acc("3.99.02.01", "ON", "0.98"),
            ]
        }
    }
    components = (
        UnitComponent(1, PerShareClass.ORDINARY),
        UnitComponent(4, PerShareClass.PREFERRED),
    )

    result = standardize(
        filing,
        Sector.UTILITY,
        date(2024, 12, 31),
        per_share_components=components,
    )

    assert result.eps_basic == Decimal("5.40")
    assert result.eps_diluted == Decimal("5.18")


def test_standardize_rejects_an_ambiguous_or_missing_class_disclosure() -> None:
    ambiguous = {
        "DRE": {
            "accounts": [
                _acc("3.99.01.01", "ON", "1.00"),
                _acc("3.99.01.02", "ON", "0.00"),
            ]
        }
    }
    target = (UnitComponent(1, PerShareClass.ORDINARY),)

    result = standardize(
        ambiguous,
        Sector.COMMODITY,
        date(2024, 12, 31),
        per_share_components=target,
    )

    assert result.eps_basic is None
    assert result.eps_basic_null_reason is NullReason.MISSING_ECONOMIC_RIGHTS
    assert result.eps_diluted is None
    assert result.eps_diluted_null_reason is NullReason.MISSING_CPC41_DISCLOSURE


def test_standardize_bank_reads_its_own_chart_of_accounts() -> None:
    # The codes and labels below are the real ones in the raw mirror, from the
    # bank chart of accounts. The loan-loss provision is deducted *inside* 3.02
    # (so 3.03 is already net of it), and the two banks even disagree on its code
    # — 3.02.05 for BBAS3, 3.02.04 for BBDC4 — which is why every line here is
    # read by label, scoped to its parent (#27).
    #
    # A bank's balance sheet has no current/non-current split and no borrowings
    # line, and its cash sits at 1.01 whole — there is no 1.01.01/1.01.02 to sum.
    by_module = {
        "BPA": {
            "accounts": [
                _acc("1", "Ativo Total", "5000"),
                _acc("1.01", "Caixa e Equivalentes de Caixa", "300"),
                _acc("1.02", "Ativos Financeiros", "4000"),
                _acc("1.02.04", "Ativos Financeiros ao Custo Amortizado", "3000"),
                _acc("1.02.04.04", "Operações de Crédito", "2200"),
                _acc("1.02.04.05", "Provisão para Perdas Esperadas", "-200"),
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
                _acc("3.01.01", "Operações de Crédito", "250"),
                _acc("3.02", "Despesas de Intermediação Financeira", "-260"),
                _acc("3.02.01", "Operações de Captação no Mercado", "-190"),
                _acc("3.02.05", "Provisão p/ Créditos de Liquidação Duvidosa", "-70"),
                _acc("3.03", "Resultado Bruto de Intermediação Financeira", "140"),
                _acc("3.04.01", "Despesa de Provisão para Perda Esperada", "0"),
                _acc("3.04.02", "Receitas de Prestação de Serviços", "45"),
                _acc("3.04.03", "Despesas com Pessoal", "-30"),
                _acc("3.04.04", "Outras Despesas de Administrativas", "-20"),
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
    assert f.cash_equivalents == Decimal("300")  # bank's CPC 03 total is 1.01
    assert f.current_financial_investments is None
    assert f.gross_profit == Decimal("140")  # filed 3.03 intermediation result
    assert f.ebit is None  # 3.05 is pre-tax profit, never EBIT (ADR 0058)
    assert f.cfo == Decimal("500")
    assert f.capex == Decimal("190")  # 150 + 40
    # The bank-specific CVM lines remain signed as filed. The provision is the one
    # inside 3.02 — not the empty 3.04.01 the other chart shape uses — but ADR 0058
    # no longer treats these partial lines as regulatory-ratio inputs.
    assert f.loan_loss_provision == Decimal("-70")
    assert f.fee_income == Decimal("45")
    assert f.personnel_expense == Decimal("-30")
    assert f.admin_expense == Decimal("-20")
    assert f.loan_book == Decimal("2000")  # 2200 gross, less its own 200 provision
    assert f.bank_ratio_null_reason is NullReason.MISSING_REGULATORY_DISCLOSURE
    # Unbuildable from a bank's schema — never read, never guessed. 2.02 above is
    # the bank's funding (deposits), and must not be mistaken for debt.
    assert f.total_debt is None
    assert f.current_assets is None
    assert f.current_liabilities is None
    # D&A and a current-only investments slice cannot be built from this schema.
    assert f.unmapped_fields == frozenset(
        {"dep_amort", "ebitda", "current_financial_investments"}
    )


def _investing(*lines: dict[str, Any]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """A minimal filing carrying only an investing section, for the capex tests."""
    return {
        "DFC": {
            "accounts": [
                _acc("6.01", "Caixa Líquido Atividades Operacionais", "500"),
                *lines,
            ]
        }
    }


def test_standardize_reads_a_capex_line_filed_as_zero_as_zero() -> None:
    # #159: BBSE3 files "Aquisição de imobilizado" at 0 — it is a holding of
    # insurers and owns almost no PP&E. Reading a published zero as an absent
    # account took free cash flow to null for two years in which the answer is
    # simply that it invested nothing.
    by_module = _investing(_acc("6.02.09", "Aquisição de imobilizado", "0"))

    f = standardize(by_module, Sector.INSURER, date(2021, 12, 31))

    assert f.capex == Decimal("0")  # a filed zero is a value, not an absence
    assert f.cfo == Decimal("500")


def test_standardize_leaves_capex_null_when_only_a_disposal_is_filed() -> None:
    # The other half of the same guard, and why it is `<= 0` rather than a bare
    # label match: a filer publishing only "alienação de imobilizado" has not told
    # us its acquisitions were zero, it has told us nothing about them. Answering
    # 0 here would invent a fact and hand FCF a number it has not earned.
    by_module = _investing(_acc("6.02.03", "Alienação de Imobilizado", "30"))

    f = standardize(by_module, Sector.COMMODITY, date(2021, 12, 31))

    assert f.capex is None


def test_standardize_skips_a_dep_amort_needle_that_only_lives_in_a_bracket() -> None:
    # #160: CXSE3 files "Outros ajustes (Depreciação/Tributos Retidos)" — an
    # other-adjustments line whose bracket happens to name depreciation, mixed
    # with withheld taxes and impossible to separate. Reading it as D&A published
    # an EBITDA that was really EBIT plus a few million of miscellany.
    by_module = {
        "DRE": {
            "accounts": [
                _acc("3.01", "Receita de Venda de Bens e/ou Serviços", "1000"),
                _acc("3.05", "Resultado Antes do Resultado Financeiro", "300"),
            ]
        },
        "DFC": {
            "accounts": [
                _acc("6.01", "Caixa Líquido Atividades Operacionais", "500"),
                _acc(
                    "6.01.01.03", "Outros ajustes (Depreciação/Tributos Retidos)", "9"
                ),
            ]
        },
    }

    f = standardize(by_module, Sector.INSURER, date(2021, 12, 31))

    assert f.dep_amort is None  # the line is not D&A, it merely mentions it
    assert f.ebitda is None  # so there is no add-back, and no EBITDA to publish
    assert f.ebit == Decimal("300")  # EBIT is unaffected — it comes from the DRE


def test_standardize_keeps_a_dep_amort_line_whose_bracket_is_only_a_note() -> None:
    # The other side of the rule: stripping brackets must not cost a real line.
    # A needle outside them is what the line *is*, whatever the bracket adds.
    by_module = {
        "DFC": {
            "accounts": [
                _acc("6.01", "Caixa Líquido Atividades Operacionais", "500"),
                _acc("6.01.01.04", "Depreciação e amortização (nota 12)", "80"),
            ]
        }
    }

    f = standardize(by_module, Sector.COMMODITY, date(2021, 12, 31))

    assert f.dep_amort == Decimal("80")


def _legacy_bank_dre(bottom_line: str) -> dict[str, Any]:
    """The pre-2020 bank DRE, whose bottom line is 3.13 — and whose 3.11 is not.

    Every code and label here is the real one, from the two banks' 2015–2019 DFPs.
    """
    return {
        "accounts": [
            _acc("3.01", "Receitas da Intermediação Financeira", "400"),
            _acc("3.02", "Despesas da Intermediação Financeira", "-260"),
            _acc("3.02.05", "Provisão p/ Créditos de Liquidação Duvidosa", "-70"),
            _acc("3.03", "Resultado Bruto Intermediação Financeira", "140"),
            _acc("3.04.01", "Receitas de Prestação de Serviços", "45"),
            _acc("3.04.02", "Despesas de Pessoal", "-30"),
            _acc("3.04.03", "Outras Despesas Administrativas", "-20"),
            _acc("3.05", "Resultado Operacional", "60"),
            # The decoy: in this chart 3.11 is not the bottom line at all, and it
            # is filed zero. Reading the result by code publishes a bank that
            # earned nothing (#78, #155).
            _acc("3.11", "Reversão dos Juros sobre Capital Próprio", "0"),
            _acc("3.13", bottom_line, "90"),
        ]
    }


def test_standardize_bank_reads_the_2018_chart_of_accounts() -> None:
    # #155: up to 2019 the bank bottom line is 3.13 "Lucro/Prejuízo do Período",
    # and in 2018–2019 the credit portfolio sits under 1.02.03 — one level off
    # from the 1.02.04 the modern chart uses — under a name that already says it
    # is net of its provision. Its sibling 1.02.03.02 is interbank lending, whose
    # label carries the same "líquidos" wording: matching it would put R$105 bn of
    # bank-to-bank credit into the customer book.
    by_module = {
        "BPA": {
            "accounts": [
                _acc("1", "Ativo Total", "5000"),
                _acc("1.01", "Caixa e Equivalentes de Caixa", "300"),
                _acc("1.02", "Ativos Financeiros", "4000"),
                _acc(
                    "1.02.03",
                    "Ativos Financeiros Avaliados ao Custo Amortizado",
                    "3000",
                ),
                _acc("1.02.03.01", "Títulos e valores mobiliários líquidos", "500"),
                _acc(
                    "1.02.03.02",
                    "Empréstimos a Instituições financeiras líquidos",
                    "700",
                ),
                _acc("1.02.03.04", "Empréstimos a clientes líquidos", "2000"),
            ]
        },
        "BPP": {"accounts": [_acc("2.07", "Patrimônio Líquido Consolidado", "800")]},
        "DRE": _legacy_bank_dre("Lucro/Prejuízo do Período"),
    }

    f = standardize(by_module, Sector.BANK, date(2018, 12, 31))

    assert f.filed_regime is AccountingRegime.BANK
    assert f.net_income == Decimal("90")  # 3.13, not the zero at 3.11
    assert f.net_income_total == Decimal("90")
    assert f.loan_book == Decimal("2000")  # already net; the interbank line is not it
    # The rest of the bank set reads the same in this chart as in the modern one.
    assert f.gross_profit == Decimal("140")
    assert f.loan_loss_provision == Decimal("-70")
    assert f.fee_income == Decimal("45")


def test_standardize_bank_reads_the_2015_chart_of_accounts() -> None:
    # #155, one generation further back: up to 2017 the portfolio sits under 1.03
    # "Empréstimos e Recebíveis", and here the sibling trap is explicit — the
    # interbank line is literally named "Líquidos de Provisão", so a search for a
    # provision to subtract under this parent would take R$20 bn of interbank
    # lending off the book. Nothing is subtracted: the line is already net.
    by_module = {
        "BPA": {
            "accounts": [
                _acc("1", "Ativo Total", "5000"),
                _acc("1.01", "Caixa e Equivalentes de Caixa", "300"),
                _acc("1.02", "Aplicações Financeiras", "1000"),
                _acc("1.03", "Empréstimos e Recebíveis", "3000"),
                _acc(
                    "1.03.01",
                    "Empréstimos a Instituições Financeiras Líquidos de Provisão",
                    "700",
                ),
                _acc("1.03.02", "Aplicações em Operações Compromissadas", "300"),
                _acc(
                    "1.03.03",
                    "Empréstimos a Clientes Líquidos de Provisão",
                    "2000",
                ),
                _acc("1.03.04", "Depósitos Compulsórios em Bancos Centrais", "600"),
            ]
        },
        "BPP": {"accounts": [_acc("2.07", "Patrimônio Líquido Consolidado", "800")]},
        "DRE": _legacy_bank_dre("Lucro/Prejuízo do Período"),
    }

    f = standardize(by_module, Sector.BANK, date(2015, 12, 31))

    assert f.net_income == Decimal("90")
    assert f.loan_book == Decimal("2000")


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
    assert f.debt_coverage_null_reason is NullReason.INCOMPLETE_DEBT_COVERAGE
    assert f.cash_equivalents == Decimal("1200")
    assert f.current_financial_investments == Decimal("300")
    assert f.current_assets == Decimal("4000")  # the split a bank does not file
    assert f.current_liabilities == Decimal("2000")
    # IFRS 17's current chart exposes only broad service/reinsurance aggregates.
    # They are not aliases for the components required by the underwriting ratios.
    assert f.earned_premium is None
    assert f.claims_incurred is None
    assert f.acquisition_costs is None
    assert f.insurance_admin_expenses is None


def test_standardize_irbr3_2022_underwriting_components() -> None:
    # Official CVM DFP 2022, IRB Brasil Resseguros (CD_CVM 024180), consolidated
    # current exercise. The pre-IFRS-17 chart separates the exact inputs used by
    # the loss and combined ratios; both insurance and reinsurance branches are
    # supported, and expenses remain negative as filed.
    by_module = {
        "DRE": {
            "currency_size": 1000,
            "accounts": [
                _acc("3.01", "Receitas das Atividades Seguradoras", "7047042"),
                _acc("3.01.02.01", "Prêmios de Resseguros Ganhos", "7021200"),
                _acc("3.02.02.01", "Sinistros Retidos de Resseguros", "-6911514"),
                _acc(
                    "3.02.02.02",
                    "Despesas de Comercialização de Resseguros",
                    "-255606",
                ),
                _acc("3.04", "Despesas Administrativas", "-421237"),
                _acc("3.07", "Resultado Antes do Resultado Financeiro", "100"),
                _acc("3.13", "Lucro/Prejuízo Consolidado do Período", "50"),
            ],
        }
    }

    f = standardize(by_module, Sector.INSURER, date(2022, 12, 31))

    assert f.earned_premium == Decimal("7021200000")
    assert f.claims_incurred == Decimal("-6911514000")
    assert f.acquisition_costs == Decimal("-255606000")
    assert f.insurance_admin_expenses == Decimal("-421237000")


def test_standardize_irbr3_maps_complete_explicit_debt_perimeter() -> None:
    by_module = {
        "BPP": {
            "accounts": [
                _acc("2.01", "Passivo Circulante", "500"),
                _acc("2.01.08", "Empréstimos e Financiamentos", "100"),
                _acc("2.01.02.01", "Passivos de Contratos de Seguros", "9000"),
                _acc("2.02", "Passivo Não Circulante", "600"),
                _acc("2.02.09", "Empréstimos e Financiamentos", "200"),
                _acc("2.02.10", "Passivo de Arrendamento", "25"),
                _acc("2.02.11", "Obrigações Subordinadas", "75"),
                _acc("2.02.12", "Capitalização", "7000"),
                # A debt word inside the equity section is not a liability.
                _acc("2.03.02.07", "Debêntures Convertidas em Ações", "5000"),
            ]
        },
        "DRE": {
            "accounts": [_acc("3.01", "Receitas das Atividades Seguradoras", "600")]
        },
    }

    f = standardize(by_module, Sector.INSURER, date(2024, 12, 31))

    # Insurance-contract and capitalization liabilities are product obligations,
    # not financing debt. Explicit leases and subordinated funding are debt.
    assert f.total_debt == Decimal("400")
    assert f.debt_coverage_null_reason is None
    assert f.debt_evidence is not None
    assert {line.instrument for line in f.debt_evidence.used_lines} == {
        DebtInstrument.LOANS_FINANCING,
        DebtInstrument.LEASES,
        DebtInstrument.DEBENTURES_SUBORDINATED,
    }


def test_standardize_insurer_accepts_zero_only_when_both_maturities_file_zero() -> None:
    by_module = {
        "BPP": {
            "accounts": [
                _acc("2.01.08", "Empréstimos e Financiamentos", "0"),
                _acc("2.01.02.01", "Passivos de Contratos de Seguros", "9000"),
                _acc("2.02.09", "Empréstimos e Financiamentos", "0"),
                _acc("2.02.12", "Capitalização", "7000"),
            ]
        },
        "DRE": {
            "accounts": [_acc("3.01", "Receitas das Atividades Seguradoras", "600")]
        },
    }

    f = standardize(by_module, Sector.INSURER, date(2024, 12, 31))

    assert f.total_debt == 0
    assert f.debt_coverage_null_reason is None


def test_standardize_bbse3_holding_excludes_insurance_product_liabilities() -> None:
    # BBSE3 is an insurer by activity but its holding filing opens with the
    # corporate revenue line. Product, reserve and capitalization liabilities are
    # not financing debt even when both debt aggregates are explicitly zero.
    by_module = {
        "BPP": {
            "accounts": [
                _acc("2.01.04", "Empréstimos e Financiamentos", "0"),
                _acc("2.02.01", "Empréstimos e Financiamentos", "0"),
                _acc("2.02.02", "Passivos de Contratos de Seguros", "900"),
                _acc("2.02.03", "Provisões Técnicas", "800"),
                _acc("2.02.04", "Capitalização", "700"),
            ]
        },
        "DRE": {
            "accounts": [_acc("3.01", "Receita de Venda de Bens e/ou Serviços", "100")]
        },
    }

    f = standardize(by_module, Sector.INSURER, date(2025, 12, 31))

    assert f.filed_regime is AccountingRegime.CORPORATE
    assert f.total_debt == 0
    assert f.debt_evidence is not None
    assert {line.instrument for line in f.debt_evidence.excluded_lines} == {
        DebtInstrument.INSURANCE_CONTRACT,
        DebtInstrument.TECHNICAL_RESERVE,
        DebtInstrument.CAPITALIZATION,
    }


def test_standardize_pssa3_generic_financial_liabilities_are_incomplete_debt() -> None:
    # PSSA3's real shape: it files the corporate DRE and zero fixed borrowing
    # parents, but a material generic "Passivos financeiros" balance and a lease
    # liability sit under Outras Obrigações. The generic balance is not proof of
    # either debt or non-debt, so zero cannot be published.
    by_module = {
        "BPP": {
            "accounts": [
                _acc("2.01.04", "Empréstimos e Financiamentos", "0"),
                _acc("2.01.05.02.06", "Passivos financeiros", "15630"),
                _acc("2.01.05.02.09", "Passivo de Arrendamento", "20"),
                _acc("2.02.01", "Empréstimos e Financiamentos", "0"),
                _acc("2.02.02.02.04", "Passivos financeiros", "5600"),
                _acc("2.02.02.02.07", "Passivo de Arrendamento", "110"),
            ]
        },
        "DRE": {
            "accounts": [
                _acc("3.01", "Receita de Venda de Bens e/ou Serviços", "40000")
            ]
        },
    }

    f = standardize(by_module, Sector.INSURER, date(2025, 12, 31))

    assert f.filed_regime is AccountingRegime.CORPORATE
    assert f.total_debt is None
    assert f.debt_coverage_null_reason is NullReason.INCOMPLETE_DEBT_COVERAGE
    assert f.debt_evidence is not None
    assert f.debt_evidence.primary_blocker is DebtBlocker.INCOMPLETE_DEBT_COVERAGE
    assert f.debt_evidence.secondary_blockers == (
        DebtBlocker.AMBIGUOUS_FINANCIAL_LIABILITY,
        DebtBlocker.AMBIGUOUS_FINANCIAL_LIABILITY,
    )
    assert [line.code for line in f.debt_evidence.excluded_lines] == [
        "2.01.05.02.06",
        "2.02.02.02.04",
    ]
    assert [line.code for line in f.debt_evidence.used_lines] == [
        "2.01.04",
        "2.02.01",
        "2.01.05.02.09",
        "2.02.02.02.07",
    ]


def test_standardize_names_missing_debt_aggregate_as_a_secondary_blocker() -> None:
    f = standardize(
        {
            "BPP": {
                "accounts": [
                    _acc("2.01.04", "Empréstimos e Financiamentos", "10"),
                ]
            },
            "DRE": {
                "accounts": [
                    _acc("3.01", "Receita de Venda de Bens e/ou Serviços", "100")
                ]
            },
        },
        Sector.COMMODITY,
        date(2025, 12, 31),
    )

    assert f.debt_coverage_null_reason is NullReason.INCOMPLETE_DEBT_COVERAGE
    assert f.debt_evidence is not None
    assert f.debt_evidence.primary_blocker is DebtBlocker.INCOMPLETE_DEBT_COVERAGE
    assert f.debt_evidence.secondary_blockers == (
        DebtBlocker.MISSING_NON_CURRENT_AGGREGATE,
    )
    assert [line.code for line in f.debt_evidence.used_lines] == ["2.01.04"]
    assert f.debt_evidence.included_instruments == ("2.01.04",)


def test_standardize_marks_bank_debt_decision_inapplicable() -> None:
    f = standardize(
        {
            "DRE": {
                "accounts": [
                    _acc("3.01", "Receitas de Intermediação Financeira", "100")
                ]
            }
        },
        Sector.BANK,
        date(2025, 12, 31),
    )

    assert f.total_debt is None
    assert f.debt_coverage_null_reason is None
    assert f.debt_evidence is not None
    assert f.debt_evidence.primary_blocker is DebtBlocker.INAPPLICABLE_REGIME
    assert f.debt_evidence.secondary_blockers == ()


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
        assert f.regime_source is RegimeSource.FILED

    # No DRE, or an unknown opening line -> undetected, never guessed.
    fallback = standardize({}, Sector.COMMODITY, date(2024, 12, 31))
    assert fallback.filed_regime is None
    assert fallback.regime_source is RegimeSource.SECTOR_FALLBACK
    unknown = {"DRE": {"accounts": [_acc("3.01", "Alguma Outra Coisa", "1")]}}
    unknown_fallback = standardize(unknown, Sector.COMMODITY, date(2024, 12, 31))
    assert unknown_fallback.filed_regime is None
    assert unknown_fallback.regime_source is RegimeSource.SECTOR_FALLBACK


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
    assert f.total_debt is None
    assert f.debt_coverage_null_reason is NullReason.INCOMPLETE_DEBT_COVERAGE
    assert f.unmapped_fields == frozenset()


def test_standardize_nonfinancial_has_no_unmapped_fields() -> None:
    f = standardize({}, Sector.COMMODITY, date(2024, 12, 31))
    assert f.unmapped_fields == frozenset()


def test_standardize_separates_cpc03_cash_from_current_investments() -> None:
    # A normal company exposes the split as consolidated total + a minority
    # sub-line; controllers = total - minority. Cash equivalents and broader
    # current investments stay separate, and dividends come from the DFC
    # financing outflows (minority line excluded).
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
    # Both slices travel together (ADR 0026): the totals as filed, minority in.
    assert f.equity_total == Decimal("1000")
    assert f.net_income_total == Decimal("200")
    assert f.cash_equivalents == Decimal("300")  # CVM 1.01.01 / CPC 03
    assert f.current_financial_investments == Decimal("200")  # CVM 1.01.02
    assert f.dividends_paid == Decimal("50")  # abs, minority line excluded
    assert f.dfc_period_start == date(2025, 1, 1)


def test_standardize_minority_search_skips_a_grandchild_reserve_line() -> None:
    # TOTS3's real BPP (#118): the capital reserves file "2.03.02.09 — Prêmio na
    # Compra de Participação de Não Controladores" (a negative reserve) BEFORE the
    # real minority block 2.03.09. A descendant-scoped name search read that
    # reserve as the minority interest, so controllers = total − (−24323) came out
    # LARGER than the consolidated total. Only direct children of the total may
    # carry the split.
    by_module = {
        "BPP": {
            "accounts": [
                _acc("2.03", "Patrimônio Líquido Consolidado", "4987121"),
                _acc(
                    "2.03.02.09",
                    "Prêmio na Compra de Participação de Não Controladores",
                    "-24323",
                ),
                _acc(
                    "2.03.09",
                    "Participação dos Acionistas Não Controladores",
                    "305769",
                ),
            ]
        },
    }

    f = standardize(by_module, Sector.INDUSTRY, date(2024, 12, 31))

    assert f.equity == Decimal("4681352")  # 4987121 - 305769, never - (-24323)


def _dre(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"DRE": {"accounts": accounts}}


def _macc(code: str, name: str, column: str | None, qty: str) -> dict[str, Any]:
    """A DMPL matrix cell: one account row under one equity column."""
    cell = _acc(code, name, qty)
    cell["column"] = column
    return cell


def test_standardize_reads_declared_dividends_from_the_dmpl() -> None:
    # #104: the declared basis. Shaped on BBDC4's real 2024 DMPL, whose column
    # HEADERS are shifted (the controllers' total sits under "Participação dos
    # Não Controladores" and the consolidated total under an unnamed column), so
    # the row's figure must be read structurally — the largest absolute cell —
    # never by the column's name. Positive rows ("dividendos prescritos", a
    # return to equity) and the treasury rows stay out.
    by_module = {
        "DMPL": {
            "period_start_date": "2024-01-01",
            "accounts": [
                _macc("5.04.06", "Dividendos", "Lucros ou Prejuízos Acumulados", "0"),
                _macc(
                    "5.04.07",
                    "Juros sobre Capital Próprio",
                    "Outros Resultados Abrangentes",  # shifted: really controllers'
                    "-11283288",
                ),
                _macc(
                    "5.04.07",
                    "Juros sobre Capital Próprio",
                    "Patrimônio Líquido Consolidado",  # shifted: really minority
                    "-435571",
                ),
                _macc(
                    "5.04.07",
                    "Juros sobre Capital Próprio",
                    None,  # shifted: really the consolidated total
                    "-11718859",
                ),
                # A prescribed dividend RETURNS to equity — never netted in.
                _macc(
                    "5.04.08",
                    "Dividendos Prescritos",
                    "Lucros ou Prejuízos Acumulados",
                    "120000",
                ),
                # Treasury transactions live under 5.04 too — not a declaration.
                _macc(
                    "5.04.04",
                    "Ações em Tesouraria Adquiridas",
                    "Reservas de Capital, Opções Outorgadas e Ações em Tesouraria",
                    "-224377",
                ),
            ],
        },
    }

    f = standardize(by_module, Sector.BANK, date(2024, 12, 31))

    assert f.dividends_declared == Decimal("11718859")  # the row's largest cell
    assert f.dmpl_period_start == date(2024, 1, 1)


def test_standardize_sums_the_dividend_and_jcp_declaration_rows() -> None:
    # A filer that declares both: the two 5.04 rows sum (VIVT3's shape).
    by_module = {
        "DMPL": {
            "accounts": [
                _macc("5.04.06", "Dividendos", "Patrimônio Líquido", "-1500000"),
                _macc(
                    "5.04.06",
                    "Dividendos",
                    "Lucros ou Prejuízos Acumulados",
                    "-1500000",
                ),
                _macc(
                    "5.04.07",
                    "Juros sobre Capital Próprio",
                    "Patrimônio Líquido",
                    "-3105000",
                ),
            ],
        },
    }

    f = standardize(by_module, Sector.INDUSTRY, date(2024, 12, 31))

    assert f.dividends_declared == Decimal("4605000")  # 1.5m + 3.105m, positive


def test_standardize_declared_is_zero_when_the_dmpl_declares_nothing() -> None:
    # A filed DMPL with no 5.04 dividend/JCP row is an economic ZERO — the
    # company declared nothing in the period. Reading it as null would void
    # every TTM window containing one quiet quarter. Null is reserved for the
    # DMPL itself being absent from the mirror.
    filed_quiet = {
        "DMPL": {
            "accounts": [
                _macc("5.01", "Saldos Iniciais", "Patrimônio Líquido", "1000"),
            ]
        },
    }
    no_dmpl: dict[str, Any] = {}

    quiet = standardize(filed_quiet, Sector.INDUSTRY, date(2024, 12, 31))
    absent = standardize(no_dmpl, Sector.INDUSTRY, date(2024, 12, 31))

    assert quiet.dividends_declared == Decimal("0")
    assert absent.dividends_declared is None


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
        ),
        sector_resolver=fake_sector_resolver,
    )

    history = await reader.history("PETR4")
    annual = await reader.annual("PETR4")

    assert [f.reference_date for f in history] == [date(2024, 9, 30)]
    assert history[0].revenue == Decimal("100")
    assert history[0].period_start == date(2024, 7, 1)  # read from the payload
    assert annual is not None
    assert annual.reference_date == date(2024, 12, 31)
    assert annual.revenue == Decimal("400")


async def test_reader_marks_resolved_debt_identity() -> None:
    reader = MongoFundamentalsReader(
        _FakeCollection(
            [
                _doc(
                    "BPP",
                    "2024-12-31",
                    [
                        _acc("2.01.04", "Empréstimos e Financiamentos", "10"),
                        _acc("2.02.01", "Empréstimos e Financiamentos", "20"),
                    ],
                    document_type="DFP",
                ),
                _doc(
                    "DRE",
                    "2024-12-31",
                    [_acc("3.01", "Receita de Venda de Bens e/ou Serviços", "100")],
                    document_type="DFP",
                ),
            ]
        ),
        sector_resolver=fake_sector_resolver,
        issuer_resolver=lambda _ticker: IssuerIdentity(
            cd_cvm="1234", cnpj="12.345.678/0001-90", issuer_name="ACME S.A."
        ),
    )

    annual = await reader.annual("PETR4")

    assert annual is not None
    assert annual.cd_cvm == "1234"
    assert annual.cnpj == "12.345.678/0001-90"
    assert annual.issuer_name == "ACME S.A."
    assert annual.debt_evidence is not None
    assert annual.debt_evidence.identity_status is DebtIdentityStatus.RESOLVED


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
        ),
        sector_resolver=fake_sector_resolver,
    )

    annual = await reader.annual("PETR4")

    assert annual is not None
    assert annual.revenue == Decimal("400")  # v2 consolidated — not v1, ind, or prior


def _bank_filing(
    module: str,
    balance_type: str,
    accounts: list[dict[str, Any]],
    *,
    version: int = 1,
) -> dict[str, Any]:
    return {
        "payload": {
            "reference_date": "2024-12-31",
            "document_type": "DFP",
            "version": version,
            "balance_type": balance_type,
            "ordem_exerc": "ULTIMO",
            "accounts": accounts,
        },
        "module": module,
        "fetched_at": datetime(2026, 7, 2, tzinfo=UTC),
    }


async def test_a_banks_income_and_cpc41_result_use_the_consolidated_filing() -> None:
    # ADR 0054, shaped on BBAS3's real 2024 DFP. The parent BACEN result differs,
    # but consolidated analysis must keep the controller numerator and CPC 41
    # class result on the same consolidated lineage.
    reader = MongoFundamentalsReader(
        _FakeCollection(
            [
                _bank_filing(
                    "DRE",
                    "consolidated",
                    [
                        _acc("3.01", "Receitas de Intermediação Financeira", "273500"),
                        _acc(
                            "3.11",
                            "Lucro ou Prejuízo Líquido Consolidado do Período",
                            "29200",
                        ),
                        _acc(
                            "3.11.01",
                            "Atribuído aos Sócios da Empresa Controladora",
                            "26400",
                        ),
                        _acc(
                            "3.11.02", "Atribuído aos Sócios não Controladores", "2800"
                        ),
                        _acc("3.99.01.01", "ON", "4.6200000000"),
                        _acc("3.99.02.01", "ON", "4.6000000000"),
                    ],
                ),
                _bank_filing(
                    "DRE",
                    "individual",
                    [
                        _acc("3.01", "Receitas de Intermediação Financeira", "278400"),
                        # Above the employees' profit share — not the bottom line.
                        _acc(
                            "3.07",
                            "Lucro ou Prejuízo das Operações Continuadas",
                            "39800",
                        ),
                        _acc(
                            "3.10", "Participações nos Lucros e Contribuições", "-4500"
                        ),
                        _acc("3.11", "Lucro ou Prejuízo Líquido do Período", "35300"),
                    ],
                    version=2,
                ),
                _bank_filing(
                    "BPA", "consolidated", [_acc("1", "Ativo Total", "2398700")]
                ),
                _bank_filing(
                    "BPA", "individual", [_acc("1", "Ativo Total", "1694000")]
                ),
            ]
        ),
        sector_resolver=fake_sector_resolver,
        per_share_resolver=lambda _ticker: (UnitComponent(1, PerShareClass.ORDINARY),),
        per_share_classes_resolver=lambda _ticker: (PerShareClass.ORDINARY,),
    )

    annual = await reader.annual("BBAS3")

    assert annual is not None
    assert annual.net_income == Decimal("26400")
    assert annual.revenue == Decimal("273500")
    assert annual.eps_basic == Decimal("4.6200000000")
    assert annual.eps_diluted == Decimal("4.6000000000")
    assert annual.cpc41 is not None
    assert annual.cpc41.basic_base_eps == Decimal("4.6200000000")
    assert annual.cpc41.diluted_base_eps is None
    assert annual.total_assets == Decimal("2398700")


async def test_a_later_comparative_restates_cpc41_after_a_bonus() -> None:
    # BBAS3-like 2:1 bonus: the 2023 DFP originally filed 10.50 per share; the
    # 2024 DFP presents 2023 again at 5.25. CPC 41 makes the later comparative
    # authoritative for LPA only. The 2023 profit remains its own filing.
    current = _bank_filing(
        "DRE",
        "consolidated",
        [
            _acc("3.01", "Receitas de Intermediação Financeira", "1000"),
            _acc(
                "3.11",
                "Lucro ou Prejuízo Líquido Consolidado do Período",
                "600",
            ),
            _acc(
                "3.11.01",
                "Atribuído aos Sócios da Empresa Controladora",
                "525",
            ),
            _acc("3.11.02", "Atribuído aos Sócios não Controladores", "75"),
            _acc("3.99.01.01", "ON", "10.50"),
            _acc("3.99.02.01", "ON", "10.50"),
        ],
    )
    current["payload"].update(
        {
            "reference_date": "2023-12-31",
            "period_end_date": "2023-12-31",
        }
    )
    comparative = _bank_filing(
        "DRE",
        "consolidated",
        [
            _acc("3.99.01.01", "ON", "5.25"),
            _acc("3.99.02.01", "ON", "5.25"),
        ],
    )
    comparative["payload"].update(
        {
            "reference_date": "2024-12-31",
            "period_end_date": "2023-12-31",
            "ordem_exerc": "PENULTIMO",
        }
    )
    reader = MongoFundamentalsReader(
        _FakeCollection([current, comparative]),
        sector_resolver=fake_sector_resolver,
        per_share_resolver=lambda _ticker: (UnitComponent(1, PerShareClass.ORDINARY),),
    )

    annual = await reader.annual("BBAS3")

    assert annual is not None
    assert annual.net_income == Decimal("525")
    assert annual.eps_basic == Decimal("5.25")
    assert annual.eps_diluted == Decimal("5.25")


async def test_an_empty_comparative_does_not_erase_a_filed_cpc41_result() -> None:
    current = _bank_filing(
        "DRE",
        "consolidated",
        [
            _acc("3.01", "Receitas de Intermediação Financeira", "1000"),
            _acc("3.99.01.01", "ON", "4.62"),
            _acc("3.99.02.01", "ON", "4.62"),
        ],
    )
    current["payload"].update(
        {"reference_date": "2024-12-31", "period_end_date": "2024-12-31"}
    )
    empty_comparative = _bank_filing(
        "DRE",
        "consolidated",
        [
            _acc("3.99.01", "Lucro Básico por Ação", "0"),
            _acc("3.99.02", "Lucro Diluído por Ação", "0"),
        ],
    )
    empty_comparative["payload"].update(
        {
            "reference_date": "2025-12-31",
            "period_end_date": "2024-12-31",
            "ordem_exerc": "PENULTIMO",
        }
    )
    reader = MongoFundamentalsReader(
        _FakeCollection([current, empty_comparative]),
        sector_resolver=fake_sector_resolver,
        per_share_resolver=lambda _ticker: (UnitComponent(1, PerShareClass.ORDINARY),),
    )

    annual = await reader.annual("BBAS3")

    assert annual is not None
    assert annual.eps_basic == Decimal("4.62")
    assert annual.eps_diluted == Decimal("4.62")


def _column(ref: str, start: str, value: str) -> dict[str, Any]:
    """One of an ITR's two income-statement columns, as the mirror now stores them."""
    return {
        "payload": {
            "reference_date": ref,
            "document_type": "ITR",
            "version": 1,
            "balance_type": "consolidated",
            "ordem_exerc": "ULTIMO",
            "period_start_date": start,
            "accounts": [_acc("3.01", "Receita", value)],
        },
        "module": "DRE",
        "fetched_at": datetime(2026, 7, 2, tzinfo=UTC),
    }


@pytest.mark.parametrize("reverse", [False, True])
async def test_reader_takes_the_accumulated_column_of_an_itr(reverse: bool) -> None:
    # An ITR files its income statement in two columns for the same reference date:
    # accumulated from 01-Jan (9 months here) and the isolated quarter (3 months).
    # Before #83 they were merged into one payload and the reader just took whichever
    # row the CSV listed first — so CVM's row order silently decided whether a
    # 3-month figure was reported against a 9-month span. The accumulated column is
    # now chosen explicitly, and the document order must not matter.
    docs = [
        _column("2024-09-30", "2024-01-01", "900"),  # accumulated
        _column("2024-09-30", "2024-07-01", "300"),  # isolated quarter
    ]
    reader = MongoFundamentalsReader(
        _FakeCollection(list(reversed(docs)) if reverse else docs),
        sector_resolver=fake_sector_resolver,
    )

    (quarter,) = await reader.history("PETR4")

    assert quarter.revenue == Decimal("900")
    assert quarter.period_start == date(2024, 1, 1)  # the span the value belongs to


def _dmpl_filing(balance_type: str, jcp: str) -> dict[str, Any]:
    return {
        "payload": {
            "reference_date": "2024-12-31",
            "document_type": "DFP",
            "version": 1,
            "balance_type": balance_type,
            "ordem_exerc": "ULTIMO",
            "accounts": [
                {
                    "code": "5.04.07",
                    "name": "Juros sobre Capital Próprio",
                    "column": "Patrimônio Líquido",
                    "quantity": jcp,
                }
            ],
        },
        "module": "DMPL",
        "fetched_at": datetime(2026, 7, 2, tzinfo=UTC),
    }


async def test_the_declared_dividends_come_from_the_parent_dmpl() -> None:
    # #104: the parent's declaration is what the listed shareholders receive, and
    # the parent DMPL has no minority column to shift — so it beats the
    # consolidated statement that ordinarily outranks it.
    reader = MongoFundamentalsReader(
        _FakeCollection(
            [
                _dmpl_filing("consolidated", "-11718859"),  # minority included
                _dmpl_filing("individual", "-11283288"),  # the parent's charge
            ]
        ),
        sector_resolver=fake_sector_resolver,
    )

    annual = await reader.annual("BBDC4")

    assert annual is not None
    assert annual.dividends_declared == Decimal("11283288")


async def test_reader_uses_the_individual_statement_when_it_is_all_there_is() -> None:
    # SAPR11 files no consolidated statement at all — the parent-only one is the
    # filing, not a second-best.
    reader = MongoFundamentalsReader(
        _FakeCollection(
            [_filed("2024-12-31", "500", version=1, balance_type="individual")]
        ),
        sector_resolver=fake_sector_resolver,
    )

    annual = await reader.annual("SAPR11")

    assert annual is not None
    assert annual.revenue == Decimal("500")
