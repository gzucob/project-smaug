"""PostgreSQL implementation of ``AnalysisRepository`` (async SQLAlchemy)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, NamedTuple, cast

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from smaug.analysis.domain.entities import (
    VIEW_CLOSED_YEAR,
    VIEW_TTM,
    AnalysisView,
    PruneResult,
    TickerAnalysis,
)
from smaug.analysis.domain.financials import (
    AccountingRegime,
    BankRegulatoryProvenance,
    CapitalActionEvidence,
    CapitalComposition,
    ClassMarketValue,
    DebtBlocker,
    DebtCoverageEvidence,
    DebtEvidenceSnapshot,
    DebtIdentityStatus,
    DebtInstrument,
    DebtLineEvidence,
    DebtLineRole,
    RegimeSource,
    ShareCountProvenance,
    ShareCounts,
    SourceAccountEvidence,
    SourceAccountRef,
    SourceAccountStatus,
)
from smaug.analysis.domain.indicators import Indicators, NullReason
from smaug.analysis.domain.ports import AnalysisStorageScope
from smaug.analysis.infrastructure.sqlalchemy_models import TickerAnalysisRow
from smaug.portfolio.domain.share_classes import (
    EconomicRightsStatus,
    PerShareClass,
    ShareClassMapping,
    ShareClassMappingStatus,
    ShareKind,
    TickerCodeEvidence,
)
from smaug.portfolio.domain.taxonomy import Classification


def _debt_evidence_to_json(
    evidence: DebtCoverageEvidence | None,
) -> dict[str, Any] | None:
    if evidence is None:
        return None

    def line_to_json(line: DebtLineEvidence) -> dict[str, Any]:
        return {
            "code": line.code,
            "name": line.name,
            "value": None if line.value is None else str(line.value),
            "role": line.role.value,
            "reason": None if line.reason is None else line.reason.value,
            "instrument": line.instrument.value,
            "classification": line.classification.value,
        }

    return {
        "regime": evidence.regime.value,
        "regime_source": evidence.regime_source.value,
        "identity_status": evidence.identity_status.value,
        "used_lines": [line_to_json(line) for line in evidence.used_lines],
        "excluded_lines": [line_to_json(line) for line in evidence.excluded_lines],
        "included_instruments": list(evidence.included_instruments),
        "primary_blocker": (
            None if evidence.primary_blocker is None else evidence.primary_blocker.value
        ),
        "secondary_blockers": [
            blocker.value for blocker in evidence.secondary_blockers
        ],
    }


def _debt_evidence_from_json(value: object) -> DebtCoverageEvidence | None:
    if not isinstance(value, Mapping):
        return None
    try:
        regime = AccountingRegime(str(value["regime"]))
        regime_source = RegimeSource(str(value["regime_source"]))
    except (KeyError, ValueError, TypeError):
        return None

    def blocker(raw: object) -> DebtBlocker:
        try:
            return DebtBlocker(str(raw))
        except (ValueError, TypeError):
            return DebtBlocker.UNKNOWN

    def instrument(raw: object) -> DebtInstrument:
        try:
            return DebtInstrument(str(raw))
        except (ValueError, TypeError):
            return DebtInstrument.OTHER

    def line(raw: object) -> DebtLineEvidence | None:
        if not isinstance(raw, Mapping):
            return None
        try:
            raw_value = raw.get("value")
            parsed_value = None if raw_value is None else Decimal(str(raw_value))
            return DebtLineEvidence(
                code=str(raw.get("code", "")),
                name=str(raw.get("name", "")),
                value=parsed_value,
                role=DebtLineRole(str(raw.get("role", ""))),
                reason=(
                    None if raw.get("reason") is None else blocker(raw.get("reason"))
                ),
                instrument=instrument(raw.get("instrument")),
            )
        except (InvalidOperation, ValueError, TypeError):
            return None

    used_lines = tuple(
        parsed
        for raw in value.get("used_lines", [])
        if (parsed := line(raw)) is not None
    )
    excluded_lines = tuple(
        parsed
        for raw in value.get("excluded_lines", [])
        if (parsed := line(raw)) is not None
    )
    instruments = value.get("included_instruments", [])
    included_instruments = (
        tuple(str(item) for item in instruments)
        if isinstance(instruments, (list, tuple))
        else ()
    )
    raw_secondary = value.get("secondary_blockers", [])
    secondary_blockers = (
        tuple(blocker(item) for item in raw_secondary)
        if isinstance(raw_secondary, (list, tuple))
        else ()
    )
    raw_identity = value.get("identity_status")
    try:
        identity_status = (
            DebtIdentityStatus(str(raw_identity))
            if raw_identity is not None
            else DebtIdentityStatus.UNKNOWN
        )
    except (ValueError, TypeError):
        identity_status = DebtIdentityStatus.UNKNOWN
    raw_primary = value.get("primary_blocker")
    return DebtCoverageEvidence(
        regime=regime,
        regime_source=regime_source,
        identity_status=identity_status,
        used_lines=used_lines,
        excluded_lines=excluded_lines,
        included_instruments=included_instruments,
        primary_blocker=None if raw_primary is None else blocker(raw_primary),
        secondary_blockers=secondary_blockers,
    )


def _source_account_evidence_to_json(
    evidence: tuple[SourceAccountEvidence, ...],
) -> list[dict[str, Any]]:
    """Serialize source lineage without losing Decimal precision."""
    return [
        {
            "field": item.field,
            "statement": item.statement,
            "status": item.status.value,
            "expected": list(item.expected),
            "found": [
                {
                    "code": ref.code,
                    "name": ref.name,
                    "value": None if ref.value is None else str(ref.value),
                }
                for ref in item.found
            ],
            "parent_code": item.parent_code,
            "formula": item.formula,
            "dependencies": list(item.dependencies),
            "blocker": None if item.blocker is None else item.blocker.value,
            "consumer_indicators": list(item.consumer_indicators),
        }
        for item in evidence
    ]


def _source_account_evidence_from_json(
    value: object,
) -> tuple[SourceAccountEvidence, ...]:
    if not isinstance(value, (list, tuple)):
        return ()

    def ref(raw: object) -> SourceAccountRef | None:
        if not isinstance(raw, Mapping):
            return None
        raw_value = raw.get("value")
        try:
            return SourceAccountRef(
                code=str(raw.get("code", "")),
                name=str(raw.get("name", "")),
                value=None if raw_value is None else Decimal(str(raw_value)),
            )
        except (InvalidOperation, ValueError, TypeError):
            return None

    parsed: list[SourceAccountEvidence] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        try:
            raw_status = raw.get("status")
            status = SourceAccountStatus(str(raw_status))
            raw_blocker = raw.get("blocker")
            blocker = None if raw_blocker is None else NullReason(str(raw_blocker))
        except (ValueError, TypeError):
            continue
        raw_expected = raw.get("expected", [])
        raw_dependencies = raw.get("dependencies", [])
        raw_consumers = raw.get("consumer_indicators", [])
        parsed.append(
            SourceAccountEvidence(
                field=str(raw.get("field", "")),
                statement=str(raw.get("statement", "")),
                status=status,
                expected=(
                    tuple(str(item) for item in raw_expected)
                    if isinstance(raw_expected, (list, tuple))
                    else ()
                ),
                found=tuple(
                    parsed_ref
                    for item in raw.get("found", [])
                    if (parsed_ref := ref(item)) is not None
                ),
                parent_code=(
                    None
                    if raw.get("parent_code") is None
                    else str(raw.get("parent_code"))
                ),
                formula=None if raw.get("formula") is None else str(raw.get("formula")),
                dependencies=(
                    tuple(str(item) for item in raw_dependencies)
                    if isinstance(raw_dependencies, (list, tuple))
                    else ()
                ),
                blocker=blocker,
                consumer_indicators=(
                    tuple(str(item) for item in raw_consumers)
                    if isinstance(raw_consumers, (list, tuple))
                    else ()
                ),
            )
        )
    return tuple(parsed)


def _bank_regulatory_provenance_to_json(
    provenance: BankRegulatoryProvenance | None,
) -> dict[str, Any] | None:
    if provenance is None:
        return None
    return {
        "source": provenance.source,
        "period_start": (
            None
            if provenance.period_start is None
            else provenance.period_start.isoformat()
        ),
        "period_end": (
            None if provenance.period_end is None else provenance.period_end.isoformat()
        ),
        "perimeter": provenance.perimeter,
        "averaging_method": provenance.averaging_method,
        "basis": provenance.basis,
        "available_inputs": sorted(provenance.available_inputs),
        "missing_inputs": sorted(provenance.missing_inputs),
        "incompatible_inputs": sorted(provenance.incompatible_inputs),
    }


def _bank_regulatory_provenance_from_json(
    value: object,
) -> BankRegulatoryProvenance | None:
    if not isinstance(value, Mapping):
        return None

    def parse_date(raw: object) -> date | None:
        if raw is None:
            return None
        try:
            return date.fromisoformat(str(raw))
        except ValueError:
            return None

    def parse_set(key: str) -> frozenset[str]:
        raw = value.get(key, [])
        return (
            frozenset(str(item) for item in raw)
            if isinstance(raw, (list, tuple, set, frozenset))
            else frozenset()
        )

    return BankRegulatoryProvenance(
        source=None if value.get("source") is None else str(value.get("source")),
        period_start=parse_date(value.get("period_start")),
        period_end=parse_date(value.get("period_end")),
        perimeter=(
            None if value.get("perimeter") is None else str(value.get("perimeter"))
        ),
        averaging_method=(
            None
            if value.get("averaging_method") is None
            else str(value.get("averaging_method"))
        ),
        basis=None if value.get("basis") is None else str(value.get("basis")),
        available_inputs=parse_set("available_inputs"),
        missing_inputs=parse_set("missing_inputs"),
        incompatible_inputs=parse_set("incompatible_inputs"),
    )


def _share_class_mappings_to_json(
    mappings: tuple[ShareClassMapping, ...],
) -> list[dict[str, Any]]:
    return [
        {
            "class_id": mapping.class_id,
            "symbol": mapping.symbol,
            "kind": None if mapping.kind is None else mapping.kind.value,
            "per_share_class": (
                None
                if mapping.per_share_class is None
                else mapping.per_share_class.value
            ),
            "status": mapping.status.value,
            "economic_rights": mapping.economic_rights.value,
            "code_evidence": [
                {
                    "symbol": evidence.symbol,
                    "filed_years": list(evidence.filed_years),
                    "source": evidence.source,
                }
                for evidence in mapping.code_evidence
            ],
            "evidence": list(mapping.evidence),
        }
        for mapping in mappings
    ]


def _share_class_mappings_from_json(value: object) -> tuple[ShareClassMapping, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    result: list[ShareClassMapping] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        try:
            kind = None if raw.get("kind") is None else ShareKind(str(raw.get("kind")))
            per_share_class = (
                None
                if raw.get("per_share_class") is None
                else PerShareClass(str(raw.get("per_share_class")))
            )
            status = ShareClassMappingStatus(str(raw.get("status")))
            rights = EconomicRightsStatus(str(raw.get("economic_rights")))
        except (TypeError, ValueError):
            continue
        code_evidence: list[TickerCodeEvidence] = []
        raw_codes = raw.get("code_evidence", [])
        if isinstance(raw_codes, (list, tuple)):
            for code in raw_codes:
                if not isinstance(code, Mapping):
                    continue
                raw_years = code.get("filed_years", [])
                years = (
                    tuple(int(year) for year in raw_years)
                    if isinstance(raw_years, (list, tuple))
                    else ()
                )
                code_evidence.append(
                    TickerCodeEvidence(
                        symbol=str(code.get("symbol", "")),
                        filed_years=years,
                        source=str(code.get("source", "cvm_fca")),
                    )
                )
        raw_evidence = raw.get("evidence", [])
        result.append(
            ShareClassMapping(
                class_id=str(raw.get("class_id", "")),
                symbol=None if raw.get("symbol") is None else str(raw.get("symbol")),
                kind=kind,
                per_share_class=per_share_class,
                status=status,
                economic_rights=rights,
                code_evidence=tuple(code_evidence),
                evidence=(
                    tuple(str(item) for item in raw_evidence)
                    if isinstance(raw_evidence, (list, tuple))
                    else ()
                ),
            )
        )
    return tuple(result)


def _class_market_values_to_json(
    values: tuple[ClassMarketValue, ...],
) -> list[dict[str, Any]]:
    return [
        {
            "class_id": value.class_id,
            "symbol": value.symbol,
            "per_share_class": value.per_share_class.value,
            "price": None if value.price is None else str(value.price),
            "shares": None if value.shares is None else str(value.shares),
            "value": None if value.value is None else str(value.value),
            "price_basis": value.price_basis,
            "share_basis": value.share_basis,
            "null_reason": (
                None if value.null_reason is None else value.null_reason.value
            ),
        }
        for value in values
    ]


def _decimal(raw: object) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _class_market_values_from_json(value: object) -> tuple[ClassMarketValue, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    result: list[ClassMarketValue] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        try:
            per_share_class = PerShareClass(str(raw.get("per_share_class")))
            raw_reason = raw.get("null_reason")
            reason = None if raw_reason is None else NullReason(str(raw_reason))
        except (TypeError, ValueError):
            continue
        result.append(
            ClassMarketValue(
                class_id=str(raw.get("class_id", "")),
                symbol=str(raw.get("symbol", "")),
                per_share_class=per_share_class,
                price=_decimal(raw.get("price")),
                shares=_decimal(raw.get("shares")),
                value=_decimal(raw.get("value")),
                price_basis=str(raw.get("price_basis", "")),
                share_basis=str(raw.get("share_basis", "")),
                null_reason=reason,
            )
        )
    return tuple(result)


def _share_counts_to_json(counts: ShareCounts | None) -> dict[str, str | None] | None:
    if counts is None:
        return None
    return {
        "common": None if counts.common is None else str(counts.common),
        "preferred": None if counts.preferred is None else str(counts.preferred),
        "total": None if counts.total is None else str(counts.total),
        "preferred_a": None if counts.preferred_a is None else str(counts.preferred_a),
        "preferred_b": None if counts.preferred_b is None else str(counts.preferred_b),
        "preferred_other": (
            None if counts.preferred_other is None else str(counts.preferred_other)
        ),
    }


def _share_counts_from_json(value: object) -> ShareCounts | None:
    if not isinstance(value, Mapping):
        return None
    return ShareCounts(
        common=_decimal(value.get("common")),
        preferred=_decimal(value.get("preferred")),
        total=_decimal(value.get("total")),
        preferred_a=_decimal(value.get("preferred_a")),
        preferred_b=_decimal(value.get("preferred_b")),
        preferred_other=_decimal(value.get("preferred_other")),
    )


def _capital_composition_to_json(
    composition: CapitalComposition | None,
) -> dict[str, str | None] | None:
    if composition is None:
        return None
    return {
        "issued_total": (
            None if composition.issued_total is None else str(composition.issued_total)
        ),
        "treasury_common": (
            None
            if composition.treasury_common is None
            else str(composition.treasury_common)
        ),
        "treasury_preferred": (
            None
            if composition.treasury_preferred is None
            else str(composition.treasury_preferred)
        ),
        "treasury_total": (
            None
            if composition.treasury_total is None
            else str(composition.treasury_total)
        ),
    }


def _capital_composition_from_json(value: object) -> CapitalComposition | None:
    if not isinstance(value, Mapping):
        return None
    return CapitalComposition(
        issued_total=_decimal(value.get("issued_total")),
        treasury_common=_decimal(value.get("treasury_common")),
        treasury_preferred=_decimal(value.get("treasury_preferred")),
        treasury_total=_decimal(value.get("treasury_total")),
    )


def _capital_provenance_to_json(
    provenance: ShareCountProvenance | None,
) -> dict[str, Any] | None:
    if provenance is None:
        return None
    return {
        "requested_year": provenance.requested_year,
        "filed_year": provenance.filed_year,
        "status": provenance.status,
        "source": provenance.source,
        "issued": _share_counts_to_json(provenance.issued),
        "outstanding": _share_counts_to_json(provenance.outstanding),
        "treasury": _capital_composition_to_json(provenance.treasury),
        "restatement_factor": (
            None
            if provenance.restatement_factor is None
            else str(provenance.restatement_factor)
        ),
        "actions": [
            {
                "approval_date": action.approval_date,
                "kind": action.kind,
                "common_before": (
                    None if action.common_before is None else str(action.common_before)
                ),
                "common_after": (
                    None if action.common_after is None else str(action.common_after)
                ),
                "preferred_before": (
                    None
                    if action.preferred_before is None
                    else str(action.preferred_before)
                ),
                "preferred_after": (
                    None
                    if action.preferred_after is None
                    else str(action.preferred_after)
                ),
                "total_before": (
                    None if action.total_before is None else str(action.total_before)
                ),
                "total_after": (
                    None if action.total_after is None else str(action.total_after)
                ),
            }
            for action in provenance.actions
        ],
        "evidence": list(provenance.evidence),
    }


def _capital_provenance_from_json(value: object) -> ShareCountProvenance | None:
    if not isinstance(value, Mapping):
        return None
    raw_evidence = value.get("evidence", [])
    raw_requested = value.get("requested_year", 0)
    raw_filed = value.get("filed_year")
    try:
        requested_year = int(str(raw_requested))
        filed_year = None if raw_filed is None else int(str(raw_filed))
    except (TypeError, ValueError):
        return None
    actions: list[CapitalActionEvidence] = []
    raw_actions = value.get("actions", [])
    if isinstance(raw_actions, (list, tuple)):
        for raw_action in raw_actions:
            if not isinstance(raw_action, Mapping):
                continue
            approval_date = raw_action.get("approval_date")
            kind = raw_action.get("kind")
            if not isinstance(approval_date, str) or not isinstance(kind, str):
                continue
            actions.append(
                CapitalActionEvidence(
                    approval_date=approval_date,
                    kind=kind,
                    common_before=_decimal(raw_action.get("common_before")),
                    common_after=_decimal(raw_action.get("common_after")),
                    preferred_before=_decimal(raw_action.get("preferred_before")),
                    preferred_after=_decimal(raw_action.get("preferred_after")),
                    total_before=_decimal(raw_action.get("total_before")),
                    total_after=_decimal(raw_action.get("total_after")),
                )
            )
    return ShareCountProvenance(
        requested_year=requested_year,
        filed_year=filed_year,
        status=str(value.get("status", "")),
        source=str(value.get("source", "cvm_fre")),
        issued=_share_counts_from_json(value.get("issued")),
        outstanding=_share_counts_from_json(value.get("outstanding")),
        treasury=_capital_composition_from_json(value.get("treasury")),
        restatement_factor=_decimal(value.get("restatement_factor")),
        actions=tuple(actions),
        evidence=(
            tuple(str(item) for item in raw_evidence)
            if isinstance(raw_evidence, (list, tuple))
            else ()
        ),
    )


def _to_row(analysis: TickerAnalysis) -> TickerAnalysisRow:
    i = analysis.indicators
    return TickerAnalysisRow(
        ticker=analysis.ticker,
        view=analysis.view,
        setor=analysis.classification.setor,
        subsetor=analysis.classification.subsetor,
        segmento=analysis.classification.segmento,
        filed_regime=(
            analysis.filed_regime.value if analysis.filed_regime is not None else None
        ),
        regime_source=(
            analysis.regime_source.value if analysis.regime_source is not None else None
        ),
        issuer_name=analysis.issuer_name,
        issuer_cd_cvm=analysis.cd_cvm,
        issuer_cnpj=analysis.cnpj,
        debt_evidence_snapshot=(
            analysis.debt_evidence_snapshot.value
            if analysis.debt_evidence_snapshot is not None
            else None
        ),
        debt_evidence=_debt_evidence_to_json(analysis.debt_evidence),
        reference_date=analysis.reference_date,
        computed_at=analysis.computed_at,
        price=analysis.price,
        price_adjusted=analysis.price_adjusted,
        price_basis=analysis.price_basis,
        share_count_basis=analysis.share_count_basis,
        liquidity_basis=analysis.liquidity_basis,
        debt_basis=analysis.debt_basis,
        roic_tax_basis=analysis.roic_tax_basis,
        roe=i.roe,
        roe_total=i.roe_total,
        roa=i.roa,
        roa_total=i.roa_total,
        roic_statutory=i.roic_statutory,
        net_margin=i.net_margin,
        net_margin_total=i.net_margin_total,
        gross_margin=i.gross_margin,
        ebit_margin=i.ebit_margin,
        ebitda_margin=i.ebitda_margin,
        asset_turnover=i.asset_turnover,
        eps=i.eps,
        eps_basic=i.eps_basic,
        eps_diluted=i.eps_diluted,
        eps_basic_market=i.eps_basic_market,
        bvps=i.bvps,
        net_debt=i.net_debt,
        cash_equivalents=i.cash_equivalents,
        current_financial_investments=i.current_financial_investments,
        net_debt_to_ebitda=i.net_debt_to_ebitda,
        net_debt_to_ebit=i.net_debt_to_ebit,
        net_debt_to_equity=i.net_debt_to_equity,
        debt_to_equity=i.debt_to_equity,
        liabilities_to_assets=i.liabilities_to_assets,
        equity_to_assets=i.equity_to_assets,
        current_ratio=i.current_ratio,
        revenue_growth=i.revenue_growth,
        net_income_growth=i.net_income_growth,
        revenue_cagr_5y=i.revenue_cagr_5y,
        ebitda_cagr_5y=i.ebitda_cagr_5y,
        ebit_cagr_5y=i.ebit_cagr_5y,
        net_income_cagr_5y=i.net_income_cagr_5y,
        pe_basic=i.pe_basic,
        pe_diluted=i.pe_diluted,
        pb=i.pb,
        company_pe=i.company_pe,
        company_pb=i.company_pb,
        pe_basic_market=i.pe_basic_market,
        psr=i.psr,
        price_to_assets=i.price_to_assets,
        price_to_ebit=i.price_to_ebit,
        price_to_working_capital=i.price_to_working_capital,
        dividend_yield=i.dividend_yield,
        payout_cash_paid_in_period=i.payout_cash_paid_in_period,
        payout_declared_in_period=i.payout_declared_in_period,
        company_cash_yield_paid_in_period=i.company_cash_yield_paid_in_period,
        company_yield_declared_in_period=i.company_yield_declared_in_period,
        ev_ebitda=i.ev_ebitda,
        ev_ebit=i.ev_ebit,
        fcf=i.fcf,
        price_to_fcf=i.price_to_fcf,
        fcf_yield=i.fcf_yield,
        net_interest_margin=i.net_interest_margin,
        efficiency_ratio=i.efficiency_ratio,
        cost_of_risk=i.cost_of_risk,
        loss_ratio=i.loss_ratio,
        combined_ratio=i.combined_ratio,
        revenue=i.revenue,
        net_income=i.net_income,
        net_income_total=i.net_income_total,
        distributions_per_security=i.distributions_per_security,
        company_distributions_paid_in_period=(i.company_distributions_paid_in_period),
        company_distributions_declared_in_period=(
            i.company_distributions_declared_in_period
        ),
        total_assets=i.total_assets,
        total_liabilities=i.total_liabilities,
        equity=i.equity,
        equity_total=i.equity_total,
        market_cap=i.market_cap,
        enterprise_value=i.enterprise_value,
        non_controlling_interests=i.non_controlling_interests,
        shares=i.shares,
        null_reasons={k: v.value for k, v in i.null_reasons.items()},
        source_account_evidence=_source_account_evidence_to_json(
            i.source_account_evidence
        ),
        bank_regulatory_provenance=_bank_regulatory_provenance_to_json(
            i.bank_regulatory_provenance
        ),
        share_class_mappings=_share_class_mappings_to_json(
            analysis.share_class_mappings
        ),
        class_market_values=_class_market_values_to_json(analysis.class_market_values),
        capital_provenance=_capital_provenance_to_json(analysis.capital_provenance),
    )


def _to_entity(row: TickerAnalysisRow) -> TickerAnalysis:
    return TickerAnalysis(
        ticker=row.ticker,
        classification=Classification(row.setor, row.subsetor, row.segmento),
        reference_date=row.reference_date,
        computed_at=row.computed_at,
        filed_regime=(
            AccountingRegime(row.filed_regime) if row.filed_regime is not None else None
        ),
        regime_source=(
            RegimeSource(row.regime_source) if row.regime_source is not None else None
        ),
        issuer_name=row.issuer_name,
        cd_cvm=row.issuer_cd_cvm,
        cnpj=row.issuer_cnpj,
        debt_evidence=_debt_evidence_from_json(row.debt_evidence),
        debt_evidence_snapshot=(
            DebtEvidenceSnapshot(row.debt_evidence_snapshot)
            if row.debt_evidence_snapshot is not None
            else None
        ),
        price=row.price,
        price_adjusted=row.price_adjusted,
        price_basis=row.price_basis,
        share_count_basis=row.share_count_basis,
        liquidity_basis=row.liquidity_basis,
        debt_basis=row.debt_basis,
        roic_tax_basis=row.roic_tax_basis,
        view=cast(AnalysisView, row.view),
        indicators=Indicators(
            roe=row.roe,
            roe_total=row.roe_total,
            roa=row.roa,
            roa_total=row.roa_total,
            roic_statutory=row.roic_statutory,
            net_margin=row.net_margin,
            net_margin_total=row.net_margin_total,
            gross_margin=row.gross_margin,
            ebit_margin=row.ebit_margin,
            ebitda_margin=row.ebitda_margin,
            asset_turnover=row.asset_turnover,
            eps=row.eps,
            eps_basic=row.eps_basic,
            eps_diluted=row.eps_diluted,
            eps_basic_market=row.eps_basic_market,
            bvps=row.bvps,
            net_debt=row.net_debt,
            cash_equivalents=row.cash_equivalents,
            current_financial_investments=row.current_financial_investments,
            net_debt_to_ebitda=row.net_debt_to_ebitda,
            net_debt_to_ebit=row.net_debt_to_ebit,
            net_debt_to_equity=row.net_debt_to_equity,
            debt_to_equity=row.debt_to_equity,
            liabilities_to_assets=row.liabilities_to_assets,
            equity_to_assets=row.equity_to_assets,
            current_ratio=row.current_ratio,
            revenue_growth=row.revenue_growth,
            net_income_growth=row.net_income_growth,
            revenue_cagr_5y=row.revenue_cagr_5y,
            ebitda_cagr_5y=row.ebitda_cagr_5y,
            ebit_cagr_5y=row.ebit_cagr_5y,
            net_income_cagr_5y=row.net_income_cagr_5y,
            pe_basic=row.pe_basic,
            pe_diluted=row.pe_diluted,
            pb=row.pb,
            company_pe=row.company_pe,
            company_pb=row.company_pb,
            pe_basic_market=row.pe_basic_market,
            psr=row.psr,
            price_to_assets=row.price_to_assets,
            price_to_ebit=row.price_to_ebit,
            price_to_working_capital=row.price_to_working_capital,
            dividend_yield=row.dividend_yield,
            payout_cash_paid_in_period=row.payout_cash_paid_in_period,
            payout_declared_in_period=row.payout_declared_in_period,
            company_cash_yield_paid_in_period=row.company_cash_yield_paid_in_period,
            company_yield_declared_in_period=row.company_yield_declared_in_period,
            ev_ebitda=row.ev_ebitda,
            ev_ebit=row.ev_ebit,
            fcf=row.fcf,
            price_to_fcf=row.price_to_fcf,
            fcf_yield=row.fcf_yield,
            net_interest_margin=row.net_interest_margin,
            efficiency_ratio=row.efficiency_ratio,
            cost_of_risk=row.cost_of_risk,
            loss_ratio=row.loss_ratio,
            combined_ratio=row.combined_ratio,
            revenue=row.revenue,
            net_income=row.net_income,
            net_income_total=row.net_income_total,
            distributions_per_security=row.distributions_per_security,
            company_distributions_paid_in_period=(
                row.company_distributions_paid_in_period
            ),
            company_distributions_declared_in_period=(
                row.company_distributions_declared_in_period
            ),
            total_assets=row.total_assets,
            total_liabilities=row.total_liabilities,
            equity=row.equity,
            equity_total=row.equity_total,
            market_cap=row.market_cap,
            enterprise_value=row.enterprise_value,
            non_controlling_interests=row.non_controlling_interests,
            shares=row.shares,
            source_account_evidence=_source_account_evidence_from_json(
                row.source_account_evidence
            ),
            bank_regulatory_provenance=_bank_regulatory_provenance_from_json(
                row.bank_regulatory_provenance
            ),
            # Pre-vocabulary rows carry NULL: degrade to "unclassified" ({}).
            null_reasons={
                k: NullReason(v) for k, v in (row.null_reasons or {}).items()
            },
        ),
        share_class_mappings=_share_class_mappings_from_json(row.share_class_mappings),
        class_market_values=_class_market_values_from_json(row.class_market_values),
        capital_provenance=_capital_provenance_from_json(row.capital_provenance),
    )


class _RunKey(NamedTuple):
    """The identity and recency of one stored run, for prune selection."""

    id: int
    ticker: str
    view: str
    reference_date: date
    computed_at: datetime


def _latest_ids(runs: Iterable[_RunKey]) -> set[int]:
    """The row ids to keep: the newest run per (ticker, view, reference_date).

    Mirrors the reads' latest-per-cell rule (newest ``computed_at``), with the row
    ``id`` as a deterministic tie-break so pruning is reproducible even in the
    (practically impossible — ``computed_at`` is one instant per run) case of a tie.
    """
    best: dict[tuple[str, str, date], tuple[datetime, int]] = {}
    for run in runs:
        cell = (run.ticker, run.view, run.reference_date)
        candidate = (run.computed_at, run.id)
        if cell not in best or candidate > best[cell]:
            best[cell] = candidate
    return {row_id for _, row_id in best.values()}


class SqlAlchemyAnalysisRepository:
    """Persists analyses and reads back the latest per ticker."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, analysis: TickerAnalysis) -> None:
        async with self._session_factory() as session:
            session.add(_to_row(analysis))
            await session.commit()

    async def latest(self, ticker: str) -> TickerAnalysis | None:
        stmt = (
            select(TickerAnalysisRow)
            .where(
                TickerAnalysisRow.ticker == ticker,
                TickerAnalysisRow.view == VIEW_TTM,
            )
            .order_by(TickerAnalysisRow.computed_at.desc())
            .limit(1)
        )
        async with self._session_factory() as session:
            row = (await session.execute(stmt)).scalars().first()
        return _to_entity(row) if row is not None else None

    async def all_latest(self) -> list[TickerAnalysis]:
        stmt = (
            select(TickerAnalysisRow)
            .where(TickerAnalysisRow.view == VIEW_TTM)
            .order_by(TickerAnalysisRow.computed_at.desc())
        )
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        seen: set[str] = set()
        latest: list[TickerAnalysis] = []
        for row in rows:
            if row.ticker not in seen:
                seen.add(row.ticker)
                latest.append(_to_entity(row))
        return latest

    async def history(self, ticker: str) -> list[TickerAnalysis]:
        """Latest computation per closed fiscal year, oldest → newest."""
        stmt = (
            select(TickerAnalysisRow)
            .where(
                TickerAnalysisRow.ticker == ticker,
                TickerAnalysisRow.view == VIEW_CLOSED_YEAR,
            )
            .order_by(TickerAnalysisRow.computed_at.desc())
        )
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        seen: set[date] = set()
        by_year: list[TickerAnalysis] = []
        for row in rows:  # newest computation first → keep the first per year
            if row.reference_date not in seen:
                seen.add(row.reference_date)
                by_year.append(_to_entity(row))
        return sorted(by_year, key=lambda a: a.reference_date)

    async def storage_scope(self, tickers: Sequence[str]) -> AnalysisStorageScope:
        """Count current, stale, and legacy rows for a doctor request.

        The normal reads intentionally collapse superseded computations.  This
        diagnostic projection reads all rows for the requested tickers and uses
        the same ``(ticker, view, reference_date, computed_at)`` rule as
        ``prune`` to identify stale rows.  A row is legacy when it predates any
        of the persisted attribution contracts that doctor relies on; nullable
        business inputs such as ``capital_provenance`` are not part of this
        check because they can legitimately be absent for a current filing.
        """
        requested = tuple(dict.fromkeys(tickers))
        if not requested:
            return AnalysisStorageScope(0, 0, 0)
        stmt = select(TickerAnalysisRow).where(TickerAnalysisRow.ticker.in_(requested))
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()

        runs = [
            _RunKey(
                row.id,
                row.ticker,
                row.view,
                row.reference_date,
                row.computed_at,
            )
            for row in rows
        ]
        keep = _latest_ids(runs)
        legacy = sum(
            row.null_reasons is None
            or row.filed_regime is None
            or row.regime_source is None
            or row.debt_evidence_snapshot is None
            or row.source_account_evidence is None
            or row.share_class_mappings is None
            or row.class_market_values is None
            for row in rows
        )
        return AnalysisStorageScope(
            persisted_rows=len(rows),
            stale_rows=len(rows) - len(keep),
            legacy_rows=legacy,
        )

    async def prune(self) -> PruneResult:
        """Delete every superseded run, keeping only the latest per cell (#71).

        The keep set is computed in Python (``_latest_ids``) from a lean projection
        of the table rather than in SQL — the table is small (hundreds of rows) and
        a pure helper is unit-testable without a database, which CI has none of.
        """
        keys = select(
            TickerAnalysisRow.id,
            TickerAnalysisRow.ticker,
            TickerAnalysisRow.view,
            TickerAnalysisRow.reference_date,
            TickerAnalysisRow.computed_at,
        )
        async with self._session_factory() as session:
            runs = [_RunKey(*row) for row in (await session.execute(keys)).all()]
            keep = _latest_ids(runs)
            if keep:
                await session.execute(
                    delete(TickerAnalysisRow).where(TickerAnalysisRow.id.not_in(keep))
                )
                await session.commit()
        # Every row is either kept or deleted, so this is exact — no need to read a
        # driver-specific rowcount back.
        return PruneResult(deleted=len(runs) - len(keep), kept=len(keep))
