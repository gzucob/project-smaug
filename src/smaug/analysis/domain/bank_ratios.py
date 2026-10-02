"""Resolve bank ratios from complete CVM flows and paired balance snapshots."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from smaug.analysis.domain.financials import (
    AccountingRegime,
    BankRegulatoryProvenance,
    BankStatementInputs,
    SourceAccountEvidence,
    SourceAccountStatus,
    StandardizedFinancials,
)
from smaug.analysis.domain.indicators import NullReason

_INPUTS = {
    "bank_interest_result_annualized": "net_interest",
    "average_earning_assets": "earning_assets",
    "bank_efficiency_expenses": "operating_expenses",
    "bank_efficiency_income": "operating_income",
    "credit_loss_expense_annualized": "credit_loss",
    "average_credit_portfolio": "gross_credit",
}
_CONSUMERS = {
    "net_interest": "net_interest_margin",
    "earning_assets": "net_interest_margin",
    "operating_expenses": "efficiency_ratio",
    "operating_income": "efficiency_ratio",
    "credit_loss": "cost_of_risk",
    "gross_credit": "cost_of_risk",
}


def _compatible(a: BankStatementInputs, b: BankStatementInputs) -> bool:
    return bool(
        a.issuer
        and a.issuer == b.issuer
        and a.currency
        and a.currency == b.currency
        and a.dre_scope in {"individual", "consolidated"}
        and a.dre_scope == b.dre_scope
    )


def resolve_bank_ratios(
    financials: StandardizedFinancials,
    history: Sequence[StandardizedFinancials],
) -> StandardizedFinancials:
    """Resolve one declared flow window and exact opening/closing averages."""
    if financials.filed_regime is not AccountingRegime.BANK:
        return financials
    periods = [p for p in history if p.bank_statement_inputs is not None]
    if not periods:
        return financials  # Preserve other explicitly evidenced input producers.
    start, end = financials.period_start, financials.reference_date
    if start is None or start > end:
        return financials
    sources = {
        p.reference_date: p
        for p in periods
        if p.reference_date <= end and p.filed_regime is AccountingRegime.BANK
    }
    closing = sources.get(end)
    if closing is None or closing.bank_statement_inputs is None:
        return financials
    anchor = closing.bank_statement_inputs
    chosen: list[tuple[StandardizedFinancials, Decimal]] = []
    if anchor.period_start == start and anchor.period_end == end:
        chosen = [(closing, Decimal(1))]
    elif start.month != 1:
        # A calendar TTM from three complete cumulative disclosures.
        try:
            prior_end = end.replace(year=end.year - 1)
        except ValueError:
            prior_end = end.replace(year=end.year - 1, day=28)
        annual = sources.get(date(end.year - 1, 12, 31))
        prior = sources.get(prior_end)
        if annual is not None and prior is not None:
            a, p = annual.bank_statement_inputs, prior.bank_statement_inputs
            if (
                a is not None
                and p is not None
                and a.period_start == date(end.year - 1, 1, 1)
                and a.period_end == annual.reference_date
                and p.period_start == date(end.year - 1, 1, 1)
                and p.period_end == prior_end
                and anchor.period_start == date(end.year, 1, 1)
                and anchor.period_end == end
                and start == prior_end + timedelta(days=1)
            ):
                chosen = [
                    (annual, Decimal(1)),
                    (prior, Decimal(-1)),
                    (closing, Decimal(1)),
                ]
    if not chosen:
        # Isolated and cumulative DRE columns may coexist. Cover the requested
        # interval exactly, subtracting only a same-start cumulative prefix.
        cursor = end
        while cursor >= start:
            period = sources.get(cursor)
            flow_root = period.bank_statement_inputs if period is not None else None
            if (
                period is None
                or flow_root is None
                or flow_root.period_start is None
                or flow_root.period_end != cursor
                or flow_root.period_start > cursor
            ):
                chosen = []
                break
            chosen.append((period, Decimal(1)))
            if flow_root.period_start >= start:
                cursor = flow_root.period_start - timedelta(days=1)
            else:
                prefix = sources.get(start - timedelta(days=1))
                pr = prefix.bank_statement_inputs if prefix is not None else None
                if (
                    prefix is None
                    or pr is None
                    or pr.period_start != flow_root.period_start
                    or pr.period_end != start - timedelta(days=1)
                ):
                    chosen = []
                    break
                chosen.append((prefix, Decimal(-1)))
                break
    valid = (
        bool(chosen)
        and all(
            p.filed_regime is AccountingRegime.BANK
            for p in history
            if start <= p.reference_date <= end
        )
        and all(
            p.bank_statement_inputs is not None
            and _compatible(anchor, p.bank_statement_inputs)
            for p, _ in chosen
        )
    )
    values: dict[str, Decimal | None] = dict.fromkeys(_INPUTS)
    dependencies: dict[str, tuple[str, ...]] = {}
    formulas: dict[str, str] = {}
    evidence: list[SourceAccountEvidence] = []

    def retain(p: StandardizedFinancials, root: str) -> str:
        name = f"bank_{root}[{p.reference_date.isoformat()}]"
        raw = next(
            (e for e in p.source_account_evidence if e.field == f"bank_{root}"), None
        )
        if raw is not None and not any(e.field == name for e in evidence):
            evidence.append(replace(raw, field=name))
        return name

    for field, root in _INPUTS.items():
        if root in {"earning_assets", "gross_credit"}:
            opening = sources.get(start - timedelta(days=1))
            combined_credit = (
                root == "gross_credit"
                and anchor.credit_loss_perimeter == "customer_loans_and_leases"
            )
            stock_root = "gross_credit_with_leases" if combined_credit else root
            pair = [opening, closing]
            dependencies[field] = tuple(
                retain(p, stock_root) for p in pair if p is not None
            )
            if opening is None or opening.bank_statement_inputs is None:
                continue
            op = opening.bank_statement_inputs
            stocks = [getattr(op, stock_root), getattr(anchor, stock_root)]
            if (
                valid
                and _compatible(anchor, op)
                and op.bpa_scope == anchor.bpa_scope == anchor.dre_scope
                and op.bpa_issuer == anchor.bpa_issuer == anchor.issuer
                and op.bpa_currency == anchor.bpa_currency == anchor.currency
                and (
                    root != "gross_credit"
                    or combined_credit
                    or (
                        op.gross_credit_perimeter == anchor.gross_credit_perimeter
                        and (
                            values["credit_loss_expense_annualized"] is None
                            or anchor.credit_loss_perimeter
                            == anchor.gross_credit_perimeter
                        )
                    )
                )
                and (
                    root != "gross_credit"
                    or (
                        op.gross_credit_perimeter
                        in {"customer_loans", "customer_loans_and_leases"}
                        and anchor.gross_credit_perimeter
                        in {"customer_loans", "customer_loans_and_leases"}
                    )
                )
                and op.balance_end == opening.reference_date
                and anchor.balance_end == end
                and all(v is not None and v.is_finite() and v >= 0 for v in stocks)
            ):
                values[field] = sum(stocks, Decimal(0)) / 2
        else:
            dependencies[field] = tuple(retain(p, root) for p, _ in chosen)
            formulas[field] = (
                " + ".join(
                    f"({weight}) * {name}"
                    for (_, weight), name in zip(
                        chosen, dependencies[field], strict=True
                    )
                )
                or "unresolved exact flow window"
            )
            parts = [
                (getattr(p.bank_statement_inputs, root), weight) for p, weight in chosen
            ]
            if root == "credit_loss" and anchor.credit_loss_perimeter not in {
                "customer_loans",
                "customer_loans_and_leases",
            }:
                continue
            if root == "credit_loss" and any(
                p.bank_statement_inputs is None
                or p.bank_statement_inputs.credit_loss_perimeter
                != anchor.credit_loss_perimeter
                for p, _ in chosen
            ):
                continue
            if not valid or any(v is None or not v.is_finite() for v, _ in parts):
                continue
            value = sum(
                (v * weight for v, weight in parts if v is not None), Decimal(0)
            )
            if root in {"net_interest", "credit_loss"}:
                year_days = (date(end.year + 1, 1, 1) - date(end.year, 1, 1)).days
                # Annual and exact TTM flows already cover a complete year.
                factor = (
                    Decimal(1)
                    if end.month == start.month - 1
                    or (start.month == 1 and end.month == 12)
                    else Decimal(year_days) / Decimal((end - start).days + 1)
                )
                value *= factor
                formulas[field] = (
                    f"({formulas[field]}) * annualization_factor({factor})"
                )
            if root == "operating_expenses" and value < 0:
                continue
            values[field] = value
    available = frozenset(k for k, v in values.items() if v is not None)
    metadata_ok = bool(
        anchor.issuer
        and anchor.currency
        and anchor.dre_scope in {"individual", "consolidated"}
    )
    provenance = BankRegulatoryProvenance(
        source="CVM_DRE_BPA",
        period_start=start,
        period_end=end,
        perimeter=anchor.dre_scope,
        averaging_method="arithmetic_mean_exact_opening_and_closing",
        basis="filed_accounting_complete_perimeter_actual_days",
        available_inputs=available,
        missing_inputs=frozenset(_INPUTS) - available,
        incompatible_inputs=frozenset() if metadata_ok else frozenset(_INPUTS),
    )
    for field, root in _INPUTS.items():
        evidence.append(
            SourceAccountEvidence(
                field=field,
                statement="CVM_DERIVED",
                status=SourceAccountStatus.DERIVED,
                expected=(
                    f"period_start={start}",
                    f"period_end={end}",
                    f"issuer={anchor.issuer}",
                    f"scope={anchor.dre_scope}",
                    f"currency={anchor.currency}",
                    f"credit_perimeter={anchor.credit_loss_perimeter}",
                    "annualization=actual_calendar_days; annual/TTM factor=1",
                    provenance.averaging_method or "",
                ),
                formula=(
                    "(opening + closing) / 2"
                    if root in {"earning_assets", "gross_credit"}
                    else formulas.get(field, "unresolved exact flow window")
                ),
                dependencies=dependencies.get(field, ()),
                blocker=None
                if values[field] is not None
                else NullReason.SOURCE_ACCOUNT_ABSENT,
                consumer_indicators=(_CONSUMERS[root],),
            )
        )
    replaced = {e.field for e in evidence}
    return replace(
        financials,
        bank_interest_result_annualized=values["bank_interest_result_annualized"],
        average_earning_assets=values["average_earning_assets"],
        bank_efficiency_expenses=values["bank_efficiency_expenses"],
        bank_efficiency_income=values["bank_efficiency_income"],
        credit_loss_expense_annualized=values["credit_loss_expense_annualized"],
        average_credit_portfolio=values["average_credit_portfolio"],
        bank_regulatory_provenance=provenance,
        bank_ratio_null_reason=None,
        source_account_evidence=tuple(
            e for e in financials.source_account_evidence if e.field not in replaced
        )
        + tuple(evidence),
    )
