"""Shared helpers of the command-line tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from teamspeak_prometheus.cli import (
    parse_args,
)

SECRET = 'fixture-secret'


ENVIRONMENT = [
    'TEAMSPEAK_HOST',
    'TEAMSPEAK_PORT',
    'TEAMSPEAK_USERNAME',
    'TEAMSPEAK_PASSWORD',
    'METRICS_PORT',
    'TEAMSPEAK_POLL_INTERVAL',
    'LOG_LEVEL',
]


def parse_error(argv: list[str], capsys) -> str:
    with pytest.raises(SystemExit) as caught:
        parse_args(argv)
    assert caught.value.code == 2
    return capsys.readouterr().err


def parse_error_with(argv: list[str], secrets: list[str], capsys) -> str:
    with pytest.raises(SystemExit) as caught:
        parse_args(argv, secrets=secrets)
    assert caught.value.code == 2
    return capsys.readouterr().err


def help_output(argv: list[str], capsys, secrets: list[str] | None = None) -> str:
    with pytest.raises(SystemExit) as caught:
        parse_args(argv, secrets=secrets)
    assert caught.value.code == 0
    return capsys.readouterr().out


REPOSITORY = Path(__file__).parent.parent


def python_files() -> list[Path]:
    return [
        path
        for path in REPOSITORY.rglob('*.py')
        if not any(
            part.startswith('.') or part == '__pycache__'
            for part in path.relative_to(REPOSITORY).parts
        )
    ]
