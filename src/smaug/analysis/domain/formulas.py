"""Public, descriptive formula references; calculations remain in the calculator."""

from dataclasses import dataclass

from smaug.analysis.domain.indicators import IndicatorContract


@dataclass(frozen=True)
class FormulaReference:
    indicator: str
    formula: str
    meaning: str
    unit: str
    period: str
    caveat: str | None
    calculation_contract_version: str
    contract: IndicatorContract | None
