"""The complete supported mirror, partitioned by published CVM archive."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PreparationBatch:
    """One archive pass through the existing raw mirror use case."""

    dataset: str
    year: int
    modules: tuple[str, ...]

    @property
    def key(self) -> str:
        return f"{self.dataset}-{self.year}"


STATEMENT_MODULES = ("BPA", "BPP", "DRE", "DFC", "DMPL", "DVA", "DRA", "CAPITAL_DFP")
FRE_MODULES = (
    "CAPITAL",
    "CAPITAL_EVENT",
    "FREE_FLOAT",
    "SHAREHOLDER_POSITION",
    "TAG_ALONG",
)
B3_MODULES = ("LISTING_B3", "CAPITAL_EVENT_B3", "CASH_DIVIDEND_B3")


def preparation_batches(
    published: dict[str, tuple[int, ...]],
) -> tuple[PreparationBatch, ...]:
    """Read each published archive once; collect live B3 histories only once."""
    batches = tuple(
        PreparationBatch(dataset, year, modules)
        for dataset, modules in (
            ("DFP", STATEMENT_MODULES),
            ("ITR", STATEMENT_MODULES),
            ("FRE", FRE_MODULES),
        )
        for year in sorted(set(published[dataset]))
    )
    return batches + (PreparationBatch("B3", max(published["FRE"]), B3_MODULES),)
