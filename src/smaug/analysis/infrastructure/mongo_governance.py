"""Resolve governance from the dated CVM/B3 mirror, never from live HTTP."""

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from smaug.analysis.domain.governance import Governance, segment_tag_along
from smaug.analysis.infrastructure.mirror import mirror_filter
from smaug.analysis.infrastructure.mongo_fundamentals import RawCollection
from smaug.portfolio.domain.company import RegistrantResolver
from smaug.portfolio.domain.share_classes import UnitComponent

_SEGMENTS = {
    "NM": "novo_mercado",
    "N1": "nivel_1",
    "N2": "nivel_2",
    "BOVESPA": "basico",
    "BOLSA": "basico",
    "MP": "companhia_menor_porte",
    "MA": "bovespa_mais",
    "M2": "bovespa_mais_nivel_2",
}


class MongoGovernanceReader:
    """Select current listing observations or same-year filed share rights."""

    def __init__(
        self, collection: RawCollection, *, registrant_resolver: RegistrantResolver
    ) -> None:
        self._collection = collection
        self._registrant = registrant_resolver

    async def read(
        self, ticker: str, end: date, components: tuple[UnitComponent, ...]
    ) -> Governance:
        classes = tuple(component.per_share_class.value for component in components)
        listing_docs = await self._collection.find(
            mirror_filter(ticker, self._registrant, source="b3", module="LISTING_B3")
        ).to_list(None)
        observations: list[tuple[datetime, Mapping[str, Any]]] = []
        for doc in listing_docs:
            payload = doc.get("payload", {})
            try:
                observed = datetime.fromisoformat(payload["observed_at"])
            except (KeyError, ValueError, TypeError):
                continue
            if observed.date() <= end:
                observations.append((observed, payload))
        segment = None
        observed_on = None
        if observations:
            observed, payload = max(observations, key=lambda item: item[0])
            segment = _SEGMENTS.get(str(payload.get("supplement", {}).get("segment")))
            market = payload.get("detail", {}).get("market")
            if market == "SOMA":
                segment = "mercado_soma"
            elif market == "BALCAO NAO ORG.":
                segment = "balcao_nao_organizado"
            observed_on = observed.date()
        docs = await self._collection.find(
            mirror_filter(ticker, self._registrant, module="TAG_ALONG")
        ).to_list(None)
        candidates: list[tuple[date, int, str, Mapping[str, Any]]] = []
        for doc in docs:
            row = doc.get("payload", {})
            try:
                reference = date.fromisoformat(row["Data_Referencia"])
                version = int(row["Versao"])
                received = date.fromisoformat(row["filing"]["DT_RECEB"])
            except (KeyError, ValueError, TypeError):
                continue
            # An old rights declaration does not prove today's statute. These
            # snapshots describe the filing year, not an indefinite validity.
            if reference <= end and received <= end and reference.year == end.year:
                candidates.append(
                    (reference, version, str(row.get("ID_Documento")), row)
                )
        value = None
        source = None
        reference_text = None
        blocker: str | None = "missing_tag_along_evidence"
        declared_values: list[Decimal] = []
        statutory_conflict = False
        if candidates and classes:
            selected = max(
                (ref, version, document) for ref, version, document, _ in candidates
            )
            rows = [
                row
                for ref, version, document, row in candidates
                if (ref, version, document) == selected
            ]
            values: list[Decimal] = []
            for kind in classes:
                matching = [row for row in rows if _class(row) == kind]
                parsed = {_percent(row.get("Percentual_Tag_Along")) for row in matching}
                declared_values.extend(v for v in parsed if v is not None)
                if kind == "ON" and any(
                    v is not None and v < Decimal("0.8") for v in parsed
                ):
                    statutory_conflict = True
                if len(parsed) != 1 or None in parsed:
                    continue
                values.append(next(value for value in parsed if value is not None))
            if len(values) == len(classes) and len(set(values)) == 1:
                value = values[0]
                source = "cvm_fre"
                reference_text = (
                    f"FRE {selected[0]} v{selected[1]} document={selected[2]}"
                )
                blocker = None
            else:
                blocker = "unresolved_tag_along_classes"
        guaranteed = segment_tag_along(segment, classes)
        if guaranteed is not None:
            if any(declared != guaranteed for declared in declared_values):
                value, source, blocker = None, None, "conflicting_tag_along_evidence"
            else:
                value, source, blocker = guaranteed, "b3_listing_rules", None
                reference_text = f"segment={segment}; observed_on={observed_on}"
        if statutory_conflict:
            value, source, blocker = None, None, "conflicting_tag_along_evidence"
            reference_text = "filed_ON_right_below_law_6404_art_254a_minimum"
        return Governance(
            listing_segment=segment,
            listing_observed_on=observed_on,
            listing_source="b3" if observed_on is not None else None,
            tag_along=value,
            tag_along_source=source,
            tag_along_reference=reference_text,
            blocker=blocker,
        )


def _class(row: Mapping[str, Any]) -> str:
    if row.get("Especie_Acao") == "Ordinária":
        return "ON"
    if row.get("Especie_Acao") == "Preferencial":
        suffix = str(row.get("Classe_Acao_Preferencial") or "").strip().upper()
        return (
            "PN" + suffix.removeprefix("PREFERENCIAL CLASSE ")
            if suffix.removeprefix("PREFERENCIAL CLASSE ") in {"A", "B", "C", "D"}
            else "PN"
            if not suffix
            else suffix
        )
    return ""


def _percent(raw: object) -> Decimal | None:
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        return None
    return value / 100 if value.is_finite() and 0 <= value <= 100 else None
