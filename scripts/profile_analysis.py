"""Measure full offline analysis on explicitly configured isolated databases.

Never point this tool at development databases: it executes the real CLI write
path. Prepare a frozen MongoDB copy and an empty migrated PostgreSQL database
first. Each invocation starts fresh application caches; database/OS caches are
left alone. Outputs include exact persisted contracts and exclusive wall times.
"""

from __future__ import annotations

import argparse
import asyncio
import builtins
import hashlib
import inspect
import json
import os
import platform
import resource
import socket
import time
import zipfile
from collections import Counter, defaultdict
from contextlib import AsyncExitStack, ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from functools import partial, wraps
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import urlparse

import httpx
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.cursor import AsyncCursor
from sqlalchemy import func, select

from smaug.analysis.application import analyze
from smaug.analysis.domain.outcomes import AnalysisRun
from smaug.analysis.infrastructure import (
    mongo_fundamentals,
    mongo_prices,
    sql_repository,
)
from smaug.analysis.infrastructure.mongo_capital import MongoSharesReader
from smaug.analysis.infrastructure.mongo_dividends import MongoCashEventReader
from smaug.analysis.infrastructure.mongo_governance import MongoGovernanceReader
from smaug.analysis.infrastructure.sqlalchemy_models import (
    AnalysisOutcomeRow,
    TickerAnalysisRow,
)
from smaug.entrypoints import cli
from smaug.portfolio.domain.taxonomy import TAXONOMY_SNAPSHOT
from smaug.shared.config import get_settings
from smaug.shared.source_inputs import MongoSourceInputs


@dataclass
class Frame:
    children: float = 0


class Timings:
    def __init__(self) -> None:
        self.seconds: dict[str, float] = defaultdict(float)
        self.calls: Counter[str] = Counter()
        self.current: ContextVar[Frame | None] = ContextVar(
            "profile_frame", default=None
        )

    @contextmanager
    def measure(self, category: str) -> Any:
        parent = self.current.get()
        frame = Frame()
        token = self.current.set(frame)
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self.current.reset(token)
            self.seconds[category] += elapsed - frame.children
            self.calls[category] += 1
            if parent is not None:
                parent.children += elapsed

    def wrap(self, fn: Any, category: str) -> Any:
        if inspect.iscoroutinefunction(fn):

            @wraps(fn)
            async def async_call(*args: Any, **kwargs: Any) -> Any:
                with self.measure(category):
                    return await fn(*args, **kwargs)

            return async_call

        @wraps(fn)
        def sync_call(*args: Any, **kwargs: Any) -> Any:
            with self.measure(category):
                return fn(*args, **kwargs)

        return sync_call

    def install(self, stack: ExitStack) -> None:
        targets = [
            (AsyncCollection, "find_one", "source_queries"),
            (AsyncCollection, "aggregate", "source_queries"),
            (AsyncCursor, "to_list", "source_queries"),
            (AsyncCursor, "__anext__", "source_queries"),
            (mongo_fundamentals.MongoFundamentalsReader, "_load", "transformation"),
            (mongo_prices.MongoCotahistArchive, "year", "transformation"),
            (MongoSourceInputs, "fca", "transformation"),
            (analyze, "compute", "formulas"),
        ]
        for name in ("save", "save_many", "save_outcome"):
            if hasattr(sql_repository.SqlAlchemyAnalysisRepository, name):
                targets.append(
                    (sql_repository.SqlAlchemyAnalysisRepository, name, "persistence")
                )
        for cls in (
            MongoSharesReader,
            MongoCashEventReader,
            MongoGovernanceReader,
            cli.RestatedPriceProvider,
            cli.DividendAdjustedPriceProvider,
            cli.SuccessionPriceProvider,
            cli.CodeSuccession,
        ):
            for name, method in vars(cls).items():
                if inspect.iscoroutinefunction(method):
                    targets.append((cls, name, "transformation"))
        for obj, name, category in targets:
            stack.enter_context(
                patch.object(obj, name, self.wrap(getattr(obj, name), category))
            )


def deny_sources(stack: ExitStack) -> None:
    original_open = builtins.open
    original_path_open = Path.open
    original_connect = socket.socket.connect
    settings = get_settings()
    allowed_addresses = set()
    for uri, default_port in (
        (settings.mongo_uri, 27017),
        (settings.postgres_uri, 5432),
    ):
        parsed = urlparse(uri)
        port = parsed.port or default_port
        for info in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM):
            allowed_addresses.add(info[4][:2])

    def check_file(file: Any) -> None:
        if isinstance(file, (str, os.PathLike)):
            path = Path(file).resolve()
            if path == TAXONOMY_SNAPSHOT.resolve():
                return
            if path.suffix.lower() in {".zip", ".csv", ".txt", ".json"}:
                raise AssertionError(f"financial source file read: {path}")

    def guarded_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        check_file(file)
        return original_open(file, *args, **kwargs)

    def guarded_path_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        check_file(path)
        return original_path_open(path, *args, **kwargs)

    def connect(sock: socket.socket, address: Any) -> Any:
        if isinstance(address, tuple) and address[:2] not in allowed_addresses:
            raise AssertionError(f"external network connection: {address}")
        return original_connect(sock, address)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("HTTP/ZIP access during offline analysis")

    stack.enter_context(patch("builtins.open", guarded_open))
    stack.enter_context(patch.object(Path, "open", guarded_path_open))
    stack.enter_context(patch.object(zipfile, "ZipFile", forbidden))
    stack.enter_context(patch.object(httpx.AsyncClient, "request", forbidden))
    stack.enter_context(patch.object(socket.socket, "connect", connect))


async def export_persisted(path: Path) -> tuple[dict[str, int], str]:
    engine = cli.create_engine(get_settings())
    coverage: dict[str, int] = {}
    digest = hashlib.sha256()
    try:
        async with AsyncExitStack() as stack:
            connection = await stack.enter_async_context(engine.connect())
            write = stack.enter_context(_json_lines(path, digest))
            for model, order in (
                (TickerAnalysisRow, ("ticker", "view", "reference_date")),
                (AnalysisOutcomeRow, ("ticker",)),
            ):
                table = model.__table__
                columns = [
                    c
                    for c in table.columns
                    if c.name not in {"computed_at", "recorded_at", "run_id"}
                ]
                statement = select(*columns).order_by(*(table.c[key] for key in order))
                if model is AnalysisOutcomeRow:
                    statement = statement.distinct(table.c.ticker).order_by(
                        table.c.recorded_at.desc(), table.c.id.desc()
                    )
                coverage[table.name] = 0
                result = await connection.stream(statement)
                async for row in result.mappings():
                    write({"table": table.name, "row": dict(row)})
                    coverage[table.name] += 1
    finally:
        await engine.dispose()
    return coverage, digest.hexdigest()


@contextmanager
def _json_lines(path: Path, digest: Any) -> Any:
    with path.open("w") as stream:

        def write(value: Any) -> None:
            line = (
                json.dumps(canonical(value), sort_keys=True, separators=(",", ":"))
                + "\n"
            )
            digest.update(line.encode())
            stream.write(line)

        yield write


def canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: canonical(item)
            for key, item in value.items()
            if key not in {"computed_at", "recorded_at", "run_id"}
        }
    if isinstance(value, (list, tuple)):
        return [canonical(item) for item in value]
    if isinstance(value, (Decimal, date, datetime)):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    return value


async def main(args: argparse.Namespace) -> None:
    if not args.isolated:
        raise SystemExit("Pass --isolated only after configuring disposable databases")
    if args.max_seconds is not None and args.max_seconds <= 0:
        raise SystemExit("--max-seconds must be positive")
    reference = json.loads(Path(args.reference).read_text()) if args.reference else None
    engine = cli.create_engine(get_settings())
    try:
        async with engine.connect() as connection:
            for model in (TickerAnalysisRow, AnalysisOutcomeRow):
                if await connection.scalar(select(func.count()).select_from(model)):
                    raise SystemExit(
                        "Use an empty migrated PostgreSQL database per run"
                    )
    finally:
        await engine.dispose()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    timings = Timings()
    captured: list[AnalysisRun] = []
    original_execute = cli.AnalyzePortfolioUseCase.execute

    async def capture_run(instance: Any, *args: Any, **kwargs: Any) -> AnalysisRun:
        run = await original_execute(instance, *args, **kwargs)
        captured.append(run)
        return run

    with ExitStack() as stack:
        as_of = datetime.fromisoformat(args.as_of)
        if as_of.tzinfo is None:
            raise SystemExit("--as-of must include a timezone")
        stack.enter_context(
            patch.object(cli.AnalyzePortfolioUseCase, "execute", capture_run)
        )
        stack.enter_context(
            patch.object(
                cli,
                "AnalyzePortfolioUseCase",
                partial(cli.AnalyzePortfolioUseCase, clock=lambda: as_of),
            )
        )
        if args.breakdown:
            timings.install(stack)
        deny_sources(stack)
        cpu_before = resource.getrusage(resource.RUSAGE_SELF)
        started = time.perf_counter()
        tickers = tuple(args.ticker or ())
        status = await cli._run_analyze(tickers, whole_exchange=not tickers)
        elapsed = time.perf_counter() - started
        peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        cpu_after = resource.getrusage(resource.RUSAGE_SELF)
    # Export is outside the measurement; no post-run query time is hidden in it.
    coverage, contract_digest = await export_persisted(
        output.with_suffix(".contract.jsonl")
    )
    # Also compare exact domain Decimals before PostgreSQL numeric typmods apply.
    domain_digest = hashlib.sha256()
    with _json_lines(output.with_suffix(".domain.jsonl"), domain_digest) as write:
        for outcome in captured[0].outcomes:
            write(asdict(outcome))
    report = {
        "elapsed_seconds": elapsed,
        "as_of": args.as_of,
        "scope": list(tickers) if tickers else "complete_traded_code_universe",
        "exit_status": status,
        "exclusive_seconds": dict(timings.seconds),
        "timing_scope": "exclusive within each task; concurrent waits overlap",
        "unattributed_seconds": max(0, elapsed - sum(timings.seconds.values())),
        "overlapping_task_seconds": max(0, sum(timings.seconds.values()) - elapsed),
        "process_cpu_seconds": (
            cpu_after.ru_utime
            + cpu_after.ru_stime
            - cpu_before.ru_utime
            - cpu_before.ru_stime
        ),
        "calls": dict(timings.calls),
        "python_peak_rss_kib": peak_rss,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "contract_sha256": contract_digest,
        "domain_sha256": domain_digest.hexdigest(),
        "coverage": coverage,
        "excluded_fields": ["computed_at", "recorded_at", "run_id"],
    }
    failures = []
    if reference is not None:
        for key in (
            "as_of",
            "scope",
            "contract_sha256",
            "domain_sha256",
            "coverage",
            "excluded_fields",
        ):
            if report[key] != reference[key]:
                failures.append(f"reference mismatch: {key}")
    if args.max_seconds is not None and elapsed >= args.max_seconds:
        failures.append(f"analysis exceeded {args.max_seconds} seconds")
    report["verification_failures"] = failures
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if status:
        raise SystemExit(status)
    if failures:
        raise SystemExit("; ".join(failures))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolated", action="store_true")
    parser.add_argument("--breakdown", action="store_true")
    parser.add_argument("--output", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--ticker", action="append", help="Diagnostic scope only")
    parser.add_argument(
        "--reference", help="Prior report with exact equivalence hashes"
    )
    parser.add_argument(
        "--max-seconds", type=float, help="Fail when elapsed >= this limit"
    )
    asyncio.run(main(parser.parse_args()))
