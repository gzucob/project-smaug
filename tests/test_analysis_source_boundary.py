"""Reject source acquisition reachable from database-only analysis composition."""

import ast
from pathlib import Path

from smaug.entrypoints import cli

_SOURCE = Path("src/smaug")
_ROOTS = {"_run_analyze", "_run_doctor", "_run_report", "_run_relink"}
_FORBIDDEN_MODULES = {
    "httpx",
    "requests",
    "aiohttp",
    "urllib",
    "socket",
    "csv",
    "zipfile",
    "smaug.shared.download",
    "smaug.shared.local_artifacts",
    "smaug.portfolio.infrastructure.cvm_registry",
    "smaug.portfolio.infrastructure.cvm_securities",
    "smaug.analysis.infrastructure.b3_prices",
}
_METADATA_MODULES = {
    "smaug.analysis.infrastructure.formulas",
    "smaug.portfolio.domain.taxonomy",
}


def test_analysis_composition_cannot_reach_source_acquisition() -> None:
    tree = ast.parse(Path(cli.__file__).read_text())
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    imports = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imports[alias.asname or alias.name] = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports[alias.asname or alias.name] = alias.name
    pending = list(_ROOTS)
    seen = set()
    modules = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        for call in ast.walk(functions[name]):
            if not isinstance(call, ast.Call):
                continue
            target = call.func
            while isinstance(target, ast.Attribute):
                target = target.value
            if not isinstance(target, ast.Name):
                continue
            called = target.id
            if called in functions:
                pending.append(called)
            elif called in imports:
                module = imports[called]
                assert not any(
                    module == blocked or (module or "").startswith(blocked + ".")
                    for blocked in _FORBIDDEN_MODULES
                ), (name, called, module)
                if module and module.startswith("smaug."):
                    modules.add(module)
    # Follow the adapter dependency graph too: moving an HTTP call behind a
    # helper import must not defeat the composition-root check.
    visited = set()
    while modules:
        module = modules.pop()
        if module in visited:
            continue
        visited.add(module)
        path = _SOURCE.parent / (module.replace(".", "/") + ".py")
        if not path.is_file():
            continue
        module_tree = ast.parse(path.read_text())
        for node in ast.walk(module_tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or ""]
            else:
                imported = []
            for dependency in imported:
                # Database initialization registers ingestion persistence models;
                # those models contain no acquisition adapters.
                assert not any(
                    dependency == blocked or dependency.startswith(blocked + ".")
                    for blocked in _FORBIDDEN_MODULES
                ), (module, dependency)
                if dependency.startswith("smaug."):
                    modules.add(dependency)
            if module in _METADATA_MODULES:
                continue
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "open", (module, node.lineno)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {
                    "open",
                    "read_text",
                    "read_bytes",
                    "read_csv",
                    "ZipFile",
                }, (module, node.lineno)
