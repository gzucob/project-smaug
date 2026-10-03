"""Bank same-concept CVM selection, complete periods and average balances."""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from smaug.analysis.domain.bank_ratios import resolve_bank_ratios
from smaug.analysis.domain.calculator import compute
from smaug.analysis.domain.entities import VIEW_TTM, TickerAnalysis
from smaug.analysis.domain.financials import (
    AccountingRegime,
    MarketData,
    StandardizedFinancials,
)
from smaug.analysis.domain.indicators import NullReason
from smaug.analysis.domain.ttm import build_ttm_as_of
from smaug.analysis.infrastructure.mongo_fundamentals import standardize
from smaug.analysis.infrastructure.sql_repository import _to_entity, _to_row
from smaug.entrypoints.api import _to_response
from smaug.portfolio.domain.sectors import Sector
from smaug.portfolio.domain.taxonomy import Classification


def _account(code: str, name: str, quantity: str) -> dict[str, Any]:
    return {"code": code, "name": name, "quantity": quantity}


def _filing(
    end: date,
    start: date,
    *,
    loans: str = "1000",
    account_changes: dict[str, tuple[str, str]] | None = None,
    extra_accounts: tuple[dict[str, Any], ...] = (),
    extra_bpa: tuple[dict[str, Any], ...] = (),
) -> StandardizedFinancials:
    meta = {
        "cvm_code": "123",
        "currency": "BRL",
        "currency_size": 1,
        "balance_type": "consolidated",
        "reference_date": str(end),
        "period_end_date": str(end),
        "version": 2,
    }
    modules = {
        "DRE": {
            **meta,
            "period_start_date": str(start),
            "accounts": [
                _account("3.01", "Receitas de Intermediação Financeira", "100"),
                _account("3.01.01", "Receitas de Juros", "100"),
                _account("3.02", "Despesas de Intermediação Financeira", "-50"),
                _account("3.02.01", "Despesas de Juros", "-40"),
                _account(
                    "3.02.02",
                    "Provisão para perdas em empréstimos a clientes",
                    "-10",
                ),
                _account("3.03", "Resultado Bruto Intermediação Financeira", "50"),
                _account("3.04", "Outras Receitas e Despesas Operacionais", "-14"),
                _account("3.04.01", "Receitas de Serviços", "20"),
                _account("3.04.02", "Despesas de Pessoal", "-15"),
                _account("3.04.03", "Despesas Administrativas", "-10"),
                _account("3.04.04", "Despesas Tributárias", "-4"),
                _account("3.04.05", "Outras Despesas Operacionais", "-5"),
            ],
        },
        "BPA": {
            **meta,
            "accounts": [
                _account("1.02.04.04", "Operações de Crédito", loans),
                _account(
                    "1.02.04.05",
                    "Provisão para perdas associadas ao risco de crédito",
                    "-100",
                ),
                _account("1.09", "Ativos Remunerados", "2000"),
            ],
        },
    }
    for payload in modules.values():
        for account in payload["accounts"]:
            if account_changes and account["code"] in account_changes:
                account["name"], account["quantity"] = account_changes[account["code"]]
    modules["DRE"]["accounts"].extend(extra_accounts)
    modules["BPA"]["accounts"].extend(extra_bpa)
    return standardize(modules, Sector.BANK, end)


def test_complete_cvm_roots_resolve_all_three_bank_ratios() -> None:
    opening = _filing(date(2024, 12, 31), date(2024, 1, 1), loans="800")
    annual = _filing(date(2025, 12, 31), date(2025, 1, 1))
    f = resolve_bank_ratios(annual, [opening, annual])
    i = compute(f, None, MarketData())
    assert i.net_interest_margin == Decimal(60) / 2000
    assert i.cost_of_risk == Decimal(10) / 900
    assert i.efficiency_ratio == Decimal(34) / 80
    assert f.bank_regulatory_provenance is not None
    assert f.bank_regulatory_provenance.source == "CVM_DRE_BPA"
    assert any(
        e.field == "bank_gross_credit[2024-12-31]" for e in f.source_account_evidence
    )
    assert any(
        e.field == "bank_operating_income[2025-12-31]" and e.found
        for e in f.source_account_evidence
    )


def test_missing_opening_stock_keeps_flow_only_efficiency() -> None:
    annual = _filing(date(2025, 12, 31), date(2025, 1, 1))
    f = resolve_bank_ratios(annual, [annual])
    i = compute(f, None, MarketData())
    assert i.net_interest_margin is None
    assert i.cost_of_risk is None
    assert i.efficiency_ratio == Decimal(34) / 80


def test_cumulative_ttm_uses_paired_flows_and_exact_stock_endpoints() -> None:
    opening = _filing(date(2025, 6, 30), date(2025, 1, 1), loans="800")
    annual = _filing(date(2025, 12, 31), date(2025, 1, 1))
    current = _filing(date(2026, 6, 30), date(2026, 1, 1), loans="1200")
    ttm = replace(current, period_start=date(2025, 7, 1))
    f = resolve_bank_ratios(ttm, [opening, annual, current])
    assert f.average_credit_portfolio == 1000
    assert f.credit_loss_expense_annualized == 10
    assert f.bank_efficiency_expenses == 34
    source = next(
        e
        for e in f.source_account_evidence
        if e.field == "credit_loss_expense_annualized"
    )
    assert source.dependencies == (
        "bank_credit_loss[2025-12-31]",
        "bank_credit_loss[2025-06-30]",
        "bank_credit_loss[2026-06-30]",
    )


@pytest.mark.parametrize(
    "change",
    [
        {"issuer": "other"},
        {"currency": "USD"},
        {"dre_scope": "individual"},
        {"period_start": date(2025, 2, 1)},
        {"period_end": date(2025, 5, 31)},
    ],
)
def test_incompatible_cumulative_input_cannot_fill_ttm(change: dict[str, Any]) -> None:
    prior = _filing(date(2025, 6, 30), date(2025, 1, 1))
    assert prior.bank_statement_inputs is not None
    prior = replace(
        prior, bank_statement_inputs=replace(prior.bank_statement_inputs, **change)
    )
    annual = _filing(date(2025, 12, 31), date(2025, 1, 1))
    current = _filing(date(2026, 6, 30), date(2026, 1, 1))
    f = resolve_bank_ratios(
        replace(current, period_start=date(2025, 7, 1)), [prior, annual, current]
    )
    assert f.credit_loss_expense_annualized is None
    assert f.bank_efficiency_income is None
    assert compute(f, None, MarketData()).efficiency_ratio is None


@pytest.mark.parametrize(
    "change",
    [
        {"bpa_scope": "individual"},
        {"bpa_issuer": "other"},
        {"bpa_currency": "USD"},
        {"balance_end": date(2024, 12, 30)},
        {"gross_credit": None},
        {"gross_credit": Decimal("NaN")},
    ],
)
def test_invalid_stock_pair_does_not_replace_average_with_closing(
    change: dict[str, Any],
) -> None:
    prior = _filing(date(2024, 12, 31), date(2024, 1, 1))
    assert prior.bank_statement_inputs is not None
    prior = replace(
        prior, bank_statement_inputs=replace(prior.bank_statement_inputs, **change)
    )
    annual = _filing(date(2025, 12, 31), date(2025, 1, 1))
    f = resolve_bank_ratios(annual, [prior, annual])
    assert f.average_credit_portfolio is None
    assert f.bank_efficiency_income == 80


def test_signed_credit_loss_reversal_and_explicit_zero_survive() -> None:
    prior = _filing(date(2024, 12, 31), date(2024, 1, 1))
    annual = _filing(date(2025, 12, 31), date(2025, 1, 1))
    assert annual.bank_statement_inputs is not None
    for value in (Decimal(0), Decimal(-5)):
        changed = replace(
            annual,
            bank_statement_inputs=replace(
                annual.bank_statement_inputs, credit_loss=value
            ),
        )
        f = resolve_bank_ratios(changed, [prior, changed])
        assert compute(f, None, MarketData()).cost_of_risk == value / 1000


def test_corporate_filed_regime_remains_not_applicable_even_in_bank_sector() -> None:
    f = StandardizedFinancials(
        reference_date=date(2025, 12, 31),
        sector=Sector.BANK,
        filed_regime=AccountingRegime.CORPORATE,
    )
    i = compute(resolve_bank_ratios(f, []), None, MarketData())
    for name in ("net_interest_margin", "cost_of_risk", "efficiency_ratio"):
        assert i.null_reasons[name] is NullReason.INAPPLICABLE_REGIME


def test_isolated_quarters_form_an_exact_window_without_annualization_twice() -> None:
    ends = [
        date(2024, 12, 31),
        date(2025, 3, 31),
        date(2025, 6, 30),
        date(2025, 9, 30),
        date(2025, 12, 31),
    ]
    starts = [
        date(2024, 10, 1),
        date(2025, 1, 1),
        date(2025, 4, 1),
        date(2025, 7, 1),
        date(2025, 10, 1),
    ]
    periods = [_filing(end, start) for end, start in zip(ends, starts, strict=True)]
    f = resolve_bank_ratios(
        replace(periods[-1], period_start=date(2025, 1, 1)), periods
    )
    assert f.credit_loss_expense_annualized == 40
    assert f.average_credit_portfolio == 1000
    assert f.bank_efficiency_expenses == 136


def test_current_cvm_named_null_describes_missing_account_evidence() -> None:
    annual = _filing(date(2025, 12, 31), date(2025, 1, 1))
    f = resolve_bank_ratios(annual, [annual])
    assert (
        compute(f, None, MarketData()).null_reasons["cost_of_risk"]
        is NullReason.SOURCE_ACCOUNT_ABSENT
    )


@pytest.mark.parametrize("quantity", ["not-readable", "NaN", "Infinity", "-9"])
def test_incomplete_or_conflicting_efficiency_component_is_not_a_partial_sum(
    quantity: str,
) -> None:
    f = _filing(
        date(2025, 12, 31),
        date(2025, 1, 1),
        account_changes={"3.04.03": ("Despesas Administrativas", quantity)},
    )
    assert f.bank_statement_inputs is not None
    assert f.bank_statement_inputs.operating_income is None
    assert f.bank_statement_inputs.operating_expenses is None


def test_parent_detail_expenses_count_once() -> None:
    f = _filing(
        date(2025, 12, 31),
        date(2025, 1, 1),
        extra_accounts=(_account("3.04.03.01", "Despesas de manutenção", "-10"),),
    )
    assert f.bank_statement_inputs is not None
    assert f.bank_statement_inputs.operating_expenses == 34


def test_reconciled_embedded_credit_loss_is_removed_from_noninterest_expenses() -> None:
    f = _filing(
        date(2025, 12, 31),
        date(2025, 1, 1),
        extra_accounts=(
            _account("3.04.05.01", "Provisão para perdas em empréstimos", "-5"),
        ),
    )
    assert f.bank_statement_inputs is not None
    assert f.bank_statement_inputs.operating_expenses == 29


@pytest.mark.parametrize(
    ("name", "quantity"),
    [
        ("Provisão para perdas associadas ao risco de crédito", "0"),
        ("Provisão para perdas associadas ao risco de crédito", "unreadable"),
    ],
)
def test_zero_or_unknown_allowance_does_not_prove_gross_book(
    name: str, quantity: str
) -> None:
    f = _filing(
        date(2025, 12, 31),
        date(2025, 1, 1),
        account_changes={"1.02.04.05": (name, quantity)},
    )
    assert f.bank_statement_inputs is not None
    assert f.bank_statement_inputs.gross_credit is None


def test_explicit_gross_book_is_evidence_even_with_zero_allowance() -> None:
    f = _filing(
        date(2025, 12, 31),
        date(2025, 1, 1),
        account_changes={
            "1.02.04.04": ("Carteira de Crédito Bruta", "1000"),
            "1.02.04.05": ("Provisão para perdas associadas ao risco de crédito", "0"),
        },
    )
    assert f.bank_statement_inputs is not None
    assert f.bank_statement_inputs.gross_credit == 1000


def test_generic_financial_assets_and_broad_loss_are_not_ratio_proxies() -> None:
    f = _filing(
        date(2025, 12, 31),
        date(2025, 1, 1),
        account_changes={
            "1.09": ("Ativos Financeiros", "2000"),
            "3.02.02": ("Perdas esperadas para ativos financeiros", "-10"),
        },
    )
    assert f.bank_statement_inputs is not None
    assert f.bank_statement_inputs.earning_assets is None
    assert f.bank_statement_inputs.credit_loss is None
    assert f.bank_statement_inputs.operating_income == 80


def test_conflicting_duplicate_keeps_named_null_and_raw_lineage() -> None:
    f = _filing(
        date(2025, 12, 31),
        date(2025, 1, 1),
        extra_accounts=(_account("3.04.03", "Despesas Administrativas", "-20"),),
    )
    assert f.bank_statement_inputs is not None
    assert f.bank_statement_inputs.operating_expenses is None
    evidence = next(
        e for e in f.source_account_evidence if e.field == "bank_operating_expenses"
    )
    assert len([r for r in evidence.found if r.code == "3.04.03"]) == 2


def test_selected_cvm_window_round_trips_through_sql_and_api() -> None:
    annuals = [
        _filing(date(2024, 12, 31), date(2024, 1, 1)),
        _filing(date(2025, 12, 31), date(2025, 1, 1)),
    ]
    quarters = [
        _filing(end, date(end.year, 1, 1))
        for end in (
            date(2025, 3, 31),
            date(2025, 6, 30),
            date(2025, 9, 30),
            date(2026, 3, 31),
            date(2026, 6, 30),
        )
    ]
    f = build_ttm_as_of(quarters, annuals, date(2026, 6, 30))
    assert f is not None
    assert f.bank_efficiency_expenses == 34
    indicators = compute(f, None, MarketData())
    analysis = TickerAnalysis(
        ticker="BANK3",
        classification=Classification("Banco", "Banco", None),
        reference_date=f.reference_date,
        computed_at=datetime(2026, 10, 1, tzinfo=UTC),
        view=VIEW_TTM,
        indicators=indicators,
    )
    restored = _to_entity(_to_row(analysis))
    assert restored.indicators.efficiency_ratio == indicators.efficiency_ratio
    assert (
        restored.indicators.bank_regulatory_provenance
        == indicators.bank_regulatory_provenance
    )
    assert (
        restored.indicators.source_account_evidence
        == indicators.source_account_evidence
    )
    response = _to_response(restored)
    assert response.indicators.bank_regulatory_provenance is not None
    assert response.indicators.bank_regulatory_provenance.period_start == date(
        2025, 7, 1
    )
    assert any(
        e.field == "bank_operating_income[2025-06-30]" and e.found
        for e in response.indicators.source_account_evidence
    )


def test_filed_regime_change_inside_ttm_prevents_recovery() -> None:
    prior = _filing(date(2025, 6, 30), date(2025, 1, 1))
    annual = _filing(date(2025, 12, 31), date(2025, 1, 1))
    current = _filing(date(2026, 6, 30), date(2026, 1, 1))
    changed = replace(
        _filing(date(2026, 3, 31), date(2026, 1, 1)),
        filed_regime=AccountingRegime.CORPORATE,
    )
    f = resolve_bank_ratios(
        replace(current, period_start=date(2025, 7, 1)),
        [prior, annual, changed, current],
    )
    assert f.bank_efficiency_income is None


def test_partial_year_annualizes_only_flows_on_actual_days() -> None:
    prior = _filing(date(2024, 12, 31), date(2024, 1, 1))
    q1 = _filing(date(2025, 3, 31), date(2025, 1, 1))
    f = resolve_bank_ratios(q1, [prior, q1])
    assert f.credit_loss_expense_annualized == Decimal(10) * Decimal(365) / Decimal(90)
    assert f.average_credit_portfolio == 1000
    assert f.bank_efficiency_expenses == 34


def test_zero_denominator_is_mathematical_null_not_missing_evidence() -> None:
    annual = _filing(date(2025, 12, 31), date(2025, 1, 1))
    assert annual.bank_statement_inputs is not None
    annual = replace(
        annual,
        bank_statement_inputs=replace(
            annual.bank_statement_inputs, operating_income=Decimal(0)
        ),
    )
    i = compute(resolve_bank_ratios(annual, [annual]), None, MarketData())
    assert i.efficiency_ratio is None
    assert i.null_reasons["efficiency_ratio"] is NullReason.ZERO_DENOMINATOR


def test_unreconciled_embedded_credit_loss_keeps_efficiency_null() -> None:
    f = _filing(
        date(2025, 12, 31),
        date(2025, 1, 1),
        extra_accounts=(
            _account("3.04.05.01", "Provisão para perdas em empréstimos", "-4"),
        ),
    )
    assert f.bank_statement_inputs is not None
    assert f.bank_statement_inputs.operating_expenses is None


def test_flow_only_efficiency_does_not_require_balance_evidence() -> None:
    f = _filing(date(2025, 12, 31), date(2025, 1, 1))
    assert f.bank_statement_inputs is not None
    f = replace(
        f,
        bank_statement_inputs=replace(
            f.bank_statement_inputs,
            bpa_issuer=None,
            bpa_currency=None,
            bpa_scope=None,
            balance_end=None,
        ),
    )
    selected = resolve_bank_ratios(f, [f])
    assert compute(selected, None, MarketData()).efficiency_ratio == Decimal(34) / 80
    assert selected.average_earning_assets is None


def test_reconciled_customer_loss_and_leases_use_matching_gross_portfolio() -> None:
    def filing(year: int, *, lease_provision: str = "-10") -> StandardizedFinancials:
        return _filing(
            date(year, 12, 31),
            date(year, 1, 1),
            account_changes={
                "3.02.02": (
                    "Provisão para perdas esperadas para risco de crédito",
                    "-10",
                )
            },
            extra_accounts=(
                _account(
                    "3.02.02.01", "Perdas em operações de crédito e arrendamento", "-8"
                ),
                _account("3.02.02.02", "Perdas em outros ativos financeiros", "-2"),
            ),
            extra_bpa=(
                _account("1.02.04.06", "Operações de Arrendamento", "100"),
                _account(
                    "1.02.04.07",
                    "Provisão para perdas em arrendamento",
                    lease_provision,
                ),
            ),
        )

    prior, annual = filing(2024), filing(2025)
    f = resolve_bank_ratios(annual, [prior, annual])
    assert f.credit_loss_expense_annualized == 8
    assert f.average_credit_portfolio == 1100
    assert compute(f, None, MarketData()).cost_of_risk == Decimal(8) / 1100
    bad = filing(2025, lease_provision="0")
    f = resolve_bank_ratios(bad, [prior, bad])
    assert f.average_credit_portfolio is None
    assert compute(f, None, MarketData()).cost_of_risk is None


def test_credit_loss_perimeter_change_cannot_mix_loans_and_leases_in_ttm() -> None:
    prior = _filing(date(2025, 6, 30), date(2025, 1, 1))
    assert prior.bank_statement_inputs is not None
    prior = replace(
        prior,
        bank_statement_inputs=replace(
            prior.bank_statement_inputs,
            credit_loss_perimeter="customer_loans_and_leases",
        ),
    )
    annual = _filing(date(2025, 12, 31), date(2025, 1, 1))
    current = _filing(date(2026, 6, 30), date(2026, 1, 1))
    f = resolve_bank_ratios(
        replace(current, period_start=date(2025, 7, 1)), [prior, annual, current]
    )
    assert f.credit_loss_expense_annualized is None
    assert f.bank_efficiency_income == 80
