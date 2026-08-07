"""Content identity keeps faithful filing versions without repeat copies."""

from dataclasses import replace
from datetime import UTC, datetime

from smaug.ingestion.domain.identity import filing_identity
from tests.fakes import make_snapshot


def test_identity_ignores_fetch_time_and_dictionary_order() -> None:
    first = make_snapshot(
        "PETR4",
        "DRE",
        {"accounts": [{"name": "Revenue", "value": "10"}], "version": 1},
        fetched_at=datetime(2026, 8, 7, 9, 0, tzinfo=UTC),
    )
    replay = replace(
        first,
        fetched_at=datetime(2026, 8, 7, 10, 0, tzinfo=UTC),
        request={"statement": "DRE", "reference_date": "2024-12-31"},
        payload={"version": 1, "accounts": [{"value": "10", "name": "Revenue"}]},
    )
    first = replace(
        first,
        request={"reference_date": "2024-12-31", "statement": "DRE"},
    )

    assert filing_identity(first) == filing_identity(replay)


def test_identity_keeps_an_amended_payload_as_a_distinct_version() -> None:
    original = make_snapshot(
        "PETR4",
        "DRE",
        {"reference_date": "2024-12-31", "value": "10"},
    )
    amended = replace(original, payload={"reference_date": "2024-12-31", "value": "11"})

    assert (
        filing_identity(original).filing_discriminator
        == filing_identity(amended).filing_discriminator
    )
    assert (
        filing_identity(original).content_hash != filing_identity(amended).content_hash
    )


def test_identity_separates_filings_in_the_same_artifact() -> None:
    first_quarter = make_snapshot(
        "PETR4",
        "DRE",
        {"reference_date": "2024-03-31", "value": "10"},
    )
    second_quarter = replace(
        first_quarter,
        request={"reference_date": "2024-06-30", "statement": "DRE"},
        payload={"reference_date": "2024-06-30", "value": "10"},
    )
    first_quarter = replace(
        first_quarter,
        request={"reference_date": "2024-03-31", "statement": "DRE"},
    )

    assert (
        filing_identity(first_quarter).filing_discriminator
        != filing_identity(second_quarter).filing_discriminator
    )


def test_identity_treats_a_republished_artifact_as_a_distinct_source_version() -> None:
    original = make_snapshot("PETR4", "DRE", {"value": "10"})
    republished = replace(original, artifact_id="sha256:" + "b" * 64)
    original = replace(original, artifact_id="sha256:" + "a" * 64)

    assert (
        filing_identity(original).content_hash
        == filing_identity(republished).content_hash
    )
    assert (
        filing_identity(original).artifact_id
        != filing_identity(republished).artifact_id
    )
