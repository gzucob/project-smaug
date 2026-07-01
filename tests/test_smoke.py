"""Smoke test: o pacote importa e esta tipado."""

import smaug


def test_should_import_package_when_installed() -> None:
    assert smaug.__doc__ is not None
