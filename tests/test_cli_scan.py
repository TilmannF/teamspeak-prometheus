"""Every command line in the repository masks values: a scan for plain
parsers, and the test tools checked like the exporter.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.cli_support import REPOSITORY, SECRET, command_line_tools, python_files


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
    assert '.github/scripts/changelog_section.py' in names
    assert not any(name.startswith('.venv/') for name in names)


def test_no_command_line_parser_bypasses_value_masking():
    offenders = {
        path.relative_to(REPOSITORY).as_posix(): lines
        for path in python_files()
        if (lines := plain_parsers(path))
    }

    assert offenders == {}, 'use SafeArgumentParser, see AGENTS.md "Secrets"'


# Any spelling -- a flag, an environment variable, a variable name -- or the
# package, which reads TEAMSPEAK_PASSWORD for app.py and healthcheck.py.
SEES_A_PASSWORD = re.compile(r'password|^from teamspeak_prometheus', re.I | re.M)
# Tools that see a password without a row in tool_cases(), and why.
LEAK_TESTED_ELSEWHERE = {
    'tests/container_test.sh': 'the image leak test: checks every log for its password',
}


def leak_tested_tools() -> set[str]:
    from tests.test_smoke_leaks import tool_cases

    return {
        argv[1].replace('.', '/') + '.py' if argv[0] == '-m' else argv[0]
        for _, argv, _, _ in tool_cases()
    }


def test_the_tool_scan_finds_every_kind_of_tool():
    tools = set(command_line_tools())

    assert {
        'app.py',
        'healthcheck.py',
        'tests/fake_ts3_server.py',
        'tests/exporter_harness.py',
        'tests/container_test.sh',
        '.github/scripts/changelog_section.py',
        '.github/scripts/validate-release-tag.sh',
    } <= tools
    assert 'tests/test_structure.py' not in tools  # mentions the guard, has none


def test_every_tool_that_sees_a_password_is_leak_tested():
    # AGENTS.md, "Secrets": a tool that takes or reads a ServerQuery password
    # gets a row in tool_cases(). A tool that never sees one -- the release
    # scripts -- has nothing to leak and stays out.
    sees_a_password = {
        tool
        for tool in command_line_tools()
        if SEES_A_PASSWORD.search((REPOSITORY / tool).read_text())
    }

    assert sees_a_password >= {'app.py', 'healthcheck.py', 'tests/fake_ts3_server.py'}
    untested = sees_a_password - leak_tested_tools() - set(LEAK_TESTED_ELSEWHERE)
    assert untested == set(), 'add a row to tool_cases() in tests/test_smoke_leaks.py'


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
