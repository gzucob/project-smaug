"""Identify changed issuers and tape revisions in the append-only mirror."""

from collections import defaultdict
from typing import Any

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from smaug.analysis.domain.incremental import RefreshCheckpoint, RefreshScope
from smaug.shared.source_inputs import SYNC_RUN_PREFIX, MongoSourceInputs


class MongoRefreshInputs:
    def __init__(self, database: AsyncDatabase[dict[str, Any]]) -> None:
        self._raw = database["raw_ingestions"]
        self._database = database

    def _archives(self) -> MongoSourceInputs:
        return MongoSourceInputs(
            self._database["source_input_archives"], self._database["source_input_rows"]
        )

    async def checkpoint(self) -> RefreshCheckpoint:
        archives = {
            f"{dataset}:{year}": revision.artifact_id
            for (dataset, year), revision in (await self._archives().pin()).items()
        }
        row = await self._raw.find_one(
            {"run_id": {"$regex": f"^{SYNC_RUN_PREFIX}"}},
            {"_id": 1},
            sort=[("_id", -1)],
        )
        return RefreshCheckpoint(str(row["_id"]) if row else None, archives)

    async def changes(
        self, previous: RefreshCheckpoint, current: RefreshCheckpoint
    ) -> RefreshScope:
        issuers: set[str] = set()
        if current.raw_id is not None and current.raw_id != previous.raw_id:
            bounds = {"$lte": ObjectId(current.raw_id)}
            if previous.raw_id is not None:
                bounds["$gt"] = ObjectId(previous.raw_id)
            codes = await self._raw.distinct(
                "cvm_code",
                {"_id": bounds, "run_id": {"$regex": f"^{SYNC_RUN_PREFIX}"}},
            )
            issuers.update(str(code) for code in codes if code)

        compare_prices = False
        inputs = self._archives()
        rejected, unscoped = await inputs.price_rejections()
        previous_rejections: set[str] = set()
        previously_unscoped = False
        for key in previous.archives.keys() | current.archives.keys():
            old, new = previous.archives.get(key), current.archives.get(key)
            if old == new:
                continue
            if key.startswith("COTAHIST:"):
                compare_prices = True
                if old is not None:
                    tape = await inputs.completed_revision(old)
                    previous_rejections.update(tape.rejected_price_codes)
                    previously_unscoped |= tape.unscoped_price_rejection
            elif key.startswith("FCA:"):
                before = await self._fca_rows(old)
                after = await self._fca_rows(new)
                issuers.update(
                    code
                    for code in before.keys() | after.keys()
                    if before.get(code) != after.get(code)
                )
        return RefreshScope(
            frozenset(issuers),
            compare_prices,
            rejected,
            unscoped,
            frozenset(previous_rejections),
            previously_unscoped,
        )

    async def _fca_rows(self, artifact: str | None) -> dict[str, list[dict[str, str]]]:
        grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
        if artifact is not None:
            inputs = self._archives()
            revision = await inputs.completed_revision(artifact)
            members = await inputs.fca(revision)
            codes = {
                _fca_cnpj(row): code
                for row in members.get(f"fca_cia_aberta_geral_{revision.year}.csv", [])
                if (code := _fca_code(row)) and _fca_cnpj(row)
            }
            for member, rows in members.items():
                for row in rows:
                    code = _fca_code(row) or codes.get(_fca_cnpj(row), "")
                    if code:
                        row.pop("__source_line_number__", None)
                        grouped[code].append({**row, "member": member})
        return dict(grouped)


def _fca_code(row: dict[str, str]) -> str:
    return (row.get("Codigo_CVM") or row.get("CD_CVM", "")).strip().lstrip("0")


def _fca_cnpj(row: dict[str, str]) -> str:
    return "".join(
        c for c in row.get("CNPJ_Companhia", "") if c.isascii() and c.isdigit()
    )
