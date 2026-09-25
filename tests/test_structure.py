"""The shape of the code: small modules, thin entry points, one logger.

These are the rules from policies/20-python-code-policy.md that a reviewer
would otherwise have to remember on every change.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).parent.parent
PACKAGE = REPOSITORY / 'teamspeak_prometheus'
ENTRY_POINTS = [REPOSITORY / 'app.py', REPOSITORY / 'healthcheck.py']
# The policy aims at 300; this is where a module must be split, not debated.
MAX_MODULE_LINES = 400


def modules() -> list[Path]:
    return sorted(PACKAGE.glob('*.py'))


def test_the_package_is_found():
    names = {module.stem for module in modules()}

    assert {'__init__', 'main', 'service', 'serverquery', 'redaction', 'logs'} <= names


@pytest.mark.parametrize('module', modules(), ids=lambda path: path.name)
def test_no_module_outgrows_the_limit(module):
    lines = len(module.read_text().splitlines())

    assert lines <= MAX_MODULE_LINES, f'{module.name} has {lines} lines: split it'


@pytest.mark.parametrize('entry_point', ENTRY_POINTS, ids=lambda path: path.name)
def test_entry_points_only_call_into_the_package(entry_point):
    # A docstring, one import from the package, and the __main__ guard.
    tree = ast.parse(entry_point.read_text())
    body = [
        node
        for node in tree.body
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
    ]

    assert len(body) == 2
    imported, guard = body
    assert isinstance(imported, ast.ImportFrom)
    assert imported.module.startswith('teamspeak_prometheus.')
    assert [alias.name for alias in imported.names] == ['main']
    assert isinstance(guard, ast.If)
    assert ast.unparse(guard.test) == "__name__ == '__main__'"
    assert ast.unparse(guard.body[0]) == 'raise SystemExit(main())'


def own_loggers(path: Path) -> list[int]:
    """Lines calling ``logging.getLogger``."""

    return [
        node.lineno
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == 'getLogger'
    ]


def test_there_is_exactly_one_logger():
    # The RedactingFilter sits on the logger in logs.py, and a filter on a
    # logger does not apply to child loggers: logging.getLogger(__name__) in
    # any other module would log past the censoring.
    offenders = {
        path.name: lines
        for path in [*modules(), *ENTRY_POINTS]
        if path.name != 'logs.py' and (lines := own_loggers(path))
    }

    assert offenders == {}
    assert len(own_loggers(PACKAGE / 'logs.py')) == 1


def test_the_one_logger_carries_the_filter():
    from teamspeak_prometheus.logs import configure_logging, log
    from teamspeak_prometheus.redaction import RedactingFilter

    before = list(log.filters)
    try:
        configure_logging('INFO', secrets=['x'])
        assert any(isinstance(f, RedactingFilter) for f in log.filters)
        assert log.name == 'teamspeak_prometheus'
    finally:
        log.filters[:] = before
