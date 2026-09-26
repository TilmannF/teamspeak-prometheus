"""Every command line in the repository masks values: a scan for plain
parsers, and the test tools checked like the exporter.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.cli_support import REPOSITORY, SECRET, python_files


def plain_parsers(path: Path) -> list[int]:
    """Lines constructing an ``ArgumentParser`` directly."""

    return [
        node.lineno
        for node in ast.walk(ast.parse(path.read_text(), str(path)))
        if isinstance(node, ast.Call)
        and (
            (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == 'ArgumentParser'
            )
            or (isinstance(node.func, ast.Name) and node.func.id == 'ArgumentParser')
        )
    ]


def test_the_scan_sees_the_whole_repository():
    # A scan over nothing passes: every module must actually be in it.
    names = {path.relative_to(REPOSITORY).as_posix() for path in python_files()}
    package = {
        path.relative_to(REPOSITORY).as_posix()
        for path in (REPOSITORY / 'teamspeak_prometheus').glob('*.py')
    }

    assert len(package) >= 10
    assert package | {'app.py', 'healthcheck.py', 'tests/exporter_harness.py'} <= names
    assert not any(name.startswith('.venv/') for name in names)


def test_no_command_line_parser_bypasses_value_masking():
    offenders = {
        path.relative_to(REPOSITORY).as_posix(): lines
        for path in python_files()
        if (lines := plain_parsers(path))
    }

    assert offenders == {}, 'use SafeArgumentParser, see AGENTS.md "Secrets"'


@pytest.mark.parametrize(
    'argv',
    [
        ['--ts3pasword', SECRET],
        ['--ts3pasword=' + SECRET],
        [SECRET],
    ],
    ids=['typo', 'typo-equals', 'positional'],
)
@pytest.mark.parametrize('tool', ['harness', 'fake-server'])
def test_the_test_tools_never_print_a_value_either(tool, argv, capsys):
    from tests import exporter_harness, fake_ts3_server

    main = {'harness': exporter_harness.main, 'fake-server': fake_ts3_server.main}[tool]

    with pytest.raises(SystemExit) as caught:
        main(argv)

    err = capsys.readouterr().err
    assert caught.value.code == 2
    assert 'unrecognized arguments' in err
    assert SECRET not in err


@pytest.mark.parametrize('tool', ['harness', 'fake-server'])
def test_the_test_tools_censor_their_password_equal_to_a_flag_name(tool, capsys):
    from tests import exporter_harness, fake_ts3_server

    main, option, other = {
        'harness': (exporter_harness.main, '--ts3password', '--metricsport'),
        'fake-server': (fake_ts3_server.main, '--password', '--port'),
    }[tool]

    with pytest.raises(SystemExit):
        main([f'{option}={other}', other])

    assert other not in capsys.readouterr().err


@pytest.mark.parametrize(
    ('tool', 'argv', 'secret'),
    [
        ('harness', ['--ts3password', 'fake', '--help'], 'fake'),
        ('fake-server', ['--password', 'docs/testing.md', '--help'], 'docs/testing.md'),
    ],
)
def test_the_test_tools_censor_their_help(tool, argv, secret, capsys):
    from tests import exporter_harness, fake_ts3_server

    main = {'harness': exporter_harness.main, 'fake-server': fake_ts3_server.main}[tool]

    with pytest.raises(SystemExit) as caught:
        main(argv)

    assert caught.value.code == 0
    out = capsys.readouterr().out
    assert 'usage:' in out
    assert secret not in out
    assert '*censored*' in out  # the password did occur in the help text
