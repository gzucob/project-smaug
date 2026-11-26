"""The installed package can be imported."""

import runpy
from unittest.mock import Mock

import pytest
import typer

import smaug
from smaug.entrypoints import cli


def test_should_import_package_when_installed() -> None:
    assert smaug.__doc__ is not None


@pytest.mark.parametrize("name", ["analyze", "ingest", "report"])
def test_module_entrypoints_dispatch_the_expected_command(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    entrypoint = Mock()
    monkeypatch.setattr(typer, "run", entrypoint)

    runpy.run_module(f"smaug.{name}", run_name="__main__")

    entrypoint.assert_called_once_with(getattr(cli, name))
