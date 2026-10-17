"""Load the packaged reference without databases, frontend files or network I/O."""

import json
from functools import cache
from importlib.resources import files

from smaug.analysis.domain.formulas import FormulaReference
from smaug.analysis.domain.indicators import (
    CALCULATION_CONTRACT_VERSION,
    indicator_contracts,
    public_indicator_names,
)


@cache
def formula_references() -> tuple[FormulaReference, ...]:
    """Describe every selected public indicator at this software revision."""
    catalog = json.loads(
        files(__package__).joinpath("formula_catalog.json").read_text("utf-8")
    )
    names = public_indicator_names()
    if set(catalog) != set(names):
        raise ValueError("Formula catalog does not match public indicators")
    contracts = indicator_contracts(CALCULATION_CONTRACT_VERSION)
    return tuple(
        FormulaReference(
            indicator=name,
            formula=catalog[name]["formula"],
            meaning=catalog[name]["meaning"],
            unit=catalog[name]["unit"],
            period=catalog[name]["period"],
            caveat=catalog[name]["caveat"],
            calculation_contract_version=CALCULATION_CONTRACT_VERSION,
            contract=contracts.get(name),
        )
        for name in sorted(names)
    )
