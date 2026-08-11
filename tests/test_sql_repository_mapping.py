"""SQL row <-> entity mapping for the null-reason map (no database connection).

``_to_row`` / ``_to_entity`` are pure attribute mappers, so they are exercised
directly on a transient ORM instance — what matters is that the ``NullReason``
enum survives the round trip as plain strings, and that rows persisted before
the vocabulary existed (NULL column) degrade to "unclassified" ({}).
"""

from datetime import UTC, date, datetime
from decimal import Decimal

from smaug.analysis.domain.entities import VIEW_TTM, TickerAnalysis
from smaug.analysis.domain.indicators import Indicators, NullReason
from smaug.analysis.infrastructure.sql_repository import _to_entity, _to_row
from smaug.portfolio.domain.taxonomy import Classification


def _analysis() -> TickerAnalysis:
    return TickerAnalysis(
        ticker="BBAS3",
        classification=Classification(
            "Financeiro", "Intermediários Financeiros", "Bancos"
        ),
        reference_date=date(2024, 12, 31),
        computed_at=datetime(2026, 7, 10, tzinfo=UTC),
        view=VIEW_TTM,
        price_basis="b3_latest_close",
        share_count_basis="cvm_latest_filed_outstanding_current_base",
        liquidity_basis="cpc03_cash_and_cash_equivalents",
        roic_tax_basis="br_statutory_34pct",
        indicators=Indicators(
            roe=Decimal("0.2"),
            null_reasons={
                "net_debt": NullReason.INAPPLICABLE_REGIME,
                "fcf": NullReason.SOURCE_ACCOUNT_UNMAPPED,
            },
        ),
    )


def test_null_reasons_round_trip_through_the_row() -> None:
    row = _to_row(_analysis())

    assert row.null_reasons == {
        "net_debt": "inapplicable_regime",
        "fcf": "source_account_unmapped",
    }

    entity = _to_entity(row)

    assert entity.indicators.null_reasons == {
        "net_debt": NullReason.INAPPLICABLE_REGIME,
        "fcf": NullReason.SOURCE_ACCOUNT_UNMAPPED,
    }
    assert entity.indicators.roe == Decimal("0.2")
    assert entity.price_basis == "b3_latest_close"
    assert entity.share_count_basis == "cvm_latest_filed_outstanding_current_base"
    assert entity.liquidity_basis == "cpc03_cash_and_cash_equivalents"
    assert entity.roic_tax_basis == "br_statutory_34pct"


def test_pre_vocabulary_rows_degrade_to_unclassified() -> None:
    row = _to_row(_analysis())
    row.null_reasons = None  # a row persisted before migration 0005

    assert _to_entity(row).indicators.null_reasons == {}
