"""Every log call and every printed line keeps values out of its template.

AGENTS.md: values are passed as logging arguments -- where the filter escapes
them -- never formatted into the template, which is not escaped. This scans the
code, so the rule cannot erode one log call at a time.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from teamspeak_prometheus.redaction import printable

REPOSITORY = Path(__file__).parent.parent
LOG_METHODS = {'debug', 'info', 'warning', 'error', 'exception', 'critical', 'log'}
LOGGERS = {'log', 'logger', 'logging'}


def python_files() -> list[Path]:
    return [
        path
        for path in REPOSITORY.rglob('*.py')
        if not any(
            part.startswith('.') or part == '__pycache__'
            for part in path.relative_to(REPOSITORY).parts
        )
    ]


def is_logger(node: ast.expr) -> bool:
    """``log``, ``log``, ``logging``, ``logger``, ``self.log``."""

    if isinstance(node, ast.Name):
        return node.id in LOGGERS
    return isinstance(node, ast.Attribute) and node.attr in LOGGERS


def formatted_templates(path: Path) -> list[str]:
    """Log calls whose template is built at runtime.

    A string literal is fine. So is a bare name that is a parameter of the
    enclosing function -- a helper forwarding its caller's literal template;
    its callers are then checked like any other call site of that helper.
    """

    tree = ast.parse(path.read_text(), str(path))
    # module-level string constants, like SETTINGS_BANNER, are fixed text too
    constants = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign) and _is_literal_text(node.value)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    visitor = _TemplateVisitor(constants, path.name)
    visitor.visit(tree)
    offenders = visitor.offenders
    return sorted(set(offenders))


class _TemplateVisitor(ast.NodeVisitor):
    """Checks each log call against the parameters of its own enclosing
    function, not of any function around that."""

    def __init__(self, constants: set[str], filename: str) -> None:
        self.constants = constants
        self.filename = filename
        self.parameters: list[set[str]] = [set()]
        self.offenders: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        arguments = node.args
        names = {
            a.arg for a in arguments.posonlyargs + arguments.args + arguments.kwonlyargs
        }
        self.parameters.append(names)
        self.generic_visit(node)
        self.parameters.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in LOG_METHODS
            and is_logger(node.func.value)
            and node.args
        ):
            template = node.args[1] if node.func.attr == 'log' else node.args[0]
            allowed = self.parameters[-1] | self.constants
            if not (
                _is_literal_text(template)
                or (isinstance(template, ast.Name) and template.id in allowed)
            ):
                self.offenders.append(
                    f'{self.filename}:{node.lineno}: {ast.unparse(template)}'
                )
        self.generic_visit(node)


def _is_literal_text(node: ast.expr) -> bool:
    """A string literal, or literals implicitly concatenated."""

    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def forwarding_calls(path: Path, helper: str) -> list[str]:
    """Calls of ``self.<helper>(reason, template, ...)`` with a runtime template."""

    offenders = []
    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == helper
            and len(node.args) >= 2
            and not (
                isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            )
        ):
            offenders.append(f'{path.name}:{node.lineno}: {ast.unparse(node.args[1])}')
    return offenders


def test_the_scan_sees_the_whole_repository():
    names = {path.relative_to(REPOSITORY).as_posix() for path in python_files()}

    assert {'app.py', 'healthcheck.py', 'tests/exporter_harness.py'} <= names


def test_no_log_template_is_built_at_runtime():
    offenders = [line for path in python_files() for line in formatted_templates(path)]

    assert offenders == [], 'pass values as logging arguments, see AGENTS.md'


def test_the_poll_error_helper_is_only_given_literal_templates():
    assert forwarding_calls(REPOSITORY / 'app.py', '_failed') == []


@pytest.mark.parametrize(
    'source',
    [
        "log.info(f'x {y}')",
        "log.info('x ' + y)",
        "log.info('x %s' % y)",
        'log.warning(describe(y))',
        'log.error(message)',
        "logging.log(20, f'{y}')",
    ],
)
def test_the_scan_catches_runtime_templates(tmp_path, source):
    path = tmp_path / 'sample.py'
    path.write_text(source + '\n')

    assert formatted_templates(path)


@pytest.mark.parametrize(
    'source',
    [
        "log.info('x %s', y)",
        'def f(message):\n    log.error(message, 1)',
        "metric.info({'version': v})",  # an Info metric, not a logger
    ],
)
def test_the_scan_allows_literal_and_forwarded_templates(tmp_path, source):
    path = tmp_path / 'sample.py'
    path.write_text(source + '\n')

    assert formatted_templates(path) == []


# -- printed lines: censored and escaped like log arguments --------------------


@pytest.mark.parametrize(
    'text',
    ['listening on x\n2026 CRITICAL forged', 'a\r\nb', 'esc\x1b[2Kx', 'sep\u2028x'],
)
def test_a_printed_line_is_always_one_line(text):
    line = printable(text, [])

    assert len(line.splitlines()) == 1
    assert '\x1b' not in line


def test_a_printed_line_is_censored_in_any_form():
    secret = 'pass\nword'

    line = printable(f'error: {secret} and pass\\nword', [secret])

    assert 'pass' not in line
