"""Reading B3's classification, and reporting how the snapshot has drifted.

No network: a transport stub answers the detail endpoint, so what is exercised
is the part that can be wrong — the level split, the label corrections, and the
decision to take the tickers from our own registry rather than from B3's reply.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx

from smaug.portfolio.application.refresh_taxonomy import (
    RefreshTaxonomyUseCase,
    snapshot_payload,
)
from smaug.portfolio.domain.taxonomy import Classification
from smaug.portfolio.domain.universe import ListedCompany
from smaug.portfolio.infrastructure.b3_taxonomy import B3TaxonomySource

_ELECTRIC = "Utilidade Pública / Energia Elétrica / Energia Elétrica"


def _company(cd_cvm: str, *tickers: str) -> ListedCompany:
    return ListedCompany(
        cd_cvm=cd_cvm,
        cnpj="00.000.000/0001-00",
        denom="COMPANHIA TESTE S.A.",
        ticker=tickers[0],
        tickers=tickers,
    )


def _source(bodies: dict[str, dict[str, object] | None]) -> B3TaxonomySource:
    """A source whose endpoint answers ``bodies`` keyed by ``codeCVM``."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = request.url.path.rsplit("/", 1)[-1]
        code = json.loads(base64.b64decode(payload))["codeCVM"]
        body = bodies.get(code)
        if body is None:
            # How the endpoint says "no such listed company": 200, empty body.
            return httpx.Response(200, text="")
        return httpx.Response(200, json=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return B3TaxonomySource(client)


async def test_the_three_levels_are_split_and_applied_to_our_tickers() -> None:
    fetched = await _source({"2437": {"industryClassification": _ELECTRIC}}).fetch(
        (_company("2437", "ELET3", "ELET5", "ELET6"),)
    )

    expected = Classification(
        "Utilidade Pública", "Energia Elétrica", "Energia Elétrica"
    )
    assert fetched.classifications == {
        "ELET3": expected,
        "ELET5": expected,
        "ELET6": expected,
    }


async def test_b3s_own_codes_are_ignored_in_favour_of_the_registrys() -> None:
    """Eletrobras answers as AXIA after its rebrand, and lists debentures too.

    Taking ``otherCodes`` would classify tickers the FCA archive we ingested has
    never heard of, and leave the ones it has unclassified.
    """
    fetched = await _source(
        {
            "2437": {
                "industryClassification": _ELECTRIC,
                "otherCodes": [
                    {"code": "AXIA3"},
                    {"code": "AXIA99"},
                    {"code": "ELET-DEB12"},
                ],
            }
        }
    ).fetch((_company("2437", "ELET3"),))

    assert set(fetched.classifications) == {"ELET3"}


async def test_a_mangled_label_is_corrected_from_the_verified_table() -> None:
    raw = (
        "Petróleo. Gás e Biocombustíveis / Petróleo. Gás e Biocombustíveis"
        " / Exploração. Refino e Distribuição"
    )
    fetched = await _source({"9512": {"industryClassification": raw}}).fetch(
        (_company("9512", "PETR4"),)
    )

    assert fetched.classifications["PETR4"] == Classification(
        "Petróleo, Gás e Biocombustíveis",
        "Petróleo, Gás e Biocombustíveis",
        "Exploração, Refino e Distribuição",
    )
    assert fetched.unknown_labels == ()


async def test_an_unknown_mangling_is_reported_not_guessed() -> None:
    # A blanket period->comma rule would turn "Máq. e Equip." into "Máq, e
    # Equip,". Anything the table does not cover gets one human look instead.
    raw = "Bens Industriais / Novo. Setor Inventado / Segmento Qualquer"
    fetched = await _source({"1": {"industryClassification": raw}}).fetch(
        (_company("1", "AAAA3"),)
    )

    assert fetched.unknown_labels == ("Novo. Setor Inventado",)
    # Still stored, uncorrected — reported is not the same as discarded.
    assert fetched.classifications["AAAA3"].subsetor == "Novo. Setor Inventado"


async def test_a_company_b3_does_not_classify_is_listed_not_invented() -> None:
    fetched = await _source({}).fetch((_company("999", "FALI3"),))

    assert fetched.classifications == {}
    assert fetched.unclassified == ("FALI3",)


async def test_a_reply_without_three_levels_is_refused() -> None:
    fetched = await _source({"1": {"industryClassification": "Só Um Nível"}}).fetch(
        (_company("1", "AAAA3"),)
    )

    assert fetched.classifications == {}
    assert fetched.unclassified == ("AAAA3",)


def _write_snapshot(path: Path, entries: dict[str, Classification]) -> None:
    path.write_text(snapshot_payload(entries), encoding="utf-8")


def test_drift_separates_gained_from_changed(tmp_path: Path) -> None:
    # ``gained``/``changed`` are compared against the *committed* snapshot, which
    # is the module-level one — so this exercises tickers that cannot be in it.
    snapshot = tmp_path / "b3_taxonomy.json"
    _write_snapshot(snapshot, {"ZZZZ9": Classification("A", "B", "C")})
    use_case = RefreshTaxonomyUseCase(snapshot)

    drift = use_case.drift({"YYYY9": Classification("D", "E", "F")})

    assert drift.gained == ("YYYY9",)  # not in the committed snapshot
    assert drift.lost == ("ZZZZ9",)  # in the file, absent from the fetch
    assert drift.moved


def test_writing_the_snapshot_reproduces_its_own_bytes(tmp_path: Path) -> None:
    """A weekly diff is only readable if a no-op run is a no-op."""
    snapshot = tmp_path / "b3_taxonomy.json"
    entries = {
        "BBBB3": Classification("Financeiro", "Bancos", "Bancos"),
        "AAAA3": Classification("Saúde", "Medicamentos", "Medicamentos"),
    }
    RefreshTaxonomyUseCase(snapshot).write(entries)
    first = snapshot.read_text(encoding="utf-8")
    RefreshTaxonomyUseCase(snapshot).write(entries)

    assert snapshot.read_text(encoding="utf-8") == first
    assert "\\u00e1" not in first  # accents stay legible in the diff


def test_the_snapshot_keeps_all_three_levels(tmp_path: Path) -> None:
    snapshot = tmp_path / "b3_taxonomy.json"
    RefreshTaxonomyUseCase(snapshot).write(
        {"AAAA3": Classification("Saúde", "Medicamentos", "Genéricos")}
    )

    stored = json.loads(snapshot.read_text(encoding="utf-8"))["tickers"]["AAAA3"]

    assert len(stored) == 3
    assert all(stored)
