"""Shared helpers of the command-line tests."""

from __future__ import annotations

import ast
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


def repository_files(pattern: str) -> list[Path]:
    """Files of the repository -- its scripts in .github/ too, no virtualenv."""

    return [
        path
        for path in REPOSITORY.rglob(pattern)
        if not any(
            (part.startswith('.') and part != '.github') or part == '__pycache__'
            for part in path.relative_to(REPOSITORY).parts
        )
    ]


def python_files() -> list[Path]:
    return repository_files('*.py')


def has_main_guard(path: Path) -> bool:
    return any(
        isinstance(node, ast.If) and ast.unparse(node.test) == "__name__ == '__main__'"
        for node in ast.parse(path.read_text(), str(path)).body
    )


def command_line_tools() -> list[str]:
    """Every file that runs as a program: a __main__ guard or a shebang."""

    tools = [path for path in python_files() if has_main_guard(path)]
    tools += [
        path for path in repository_files('*.sh') if path.read_text().startswith('#!')
    ]
    return sorted(path.relative_to(REPOSITORY).as_posix() for path in tools)
