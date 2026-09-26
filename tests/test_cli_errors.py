"""Everything argparse prints: errors and --help never show a value
or a password.
"""

from __future__ import annotations

import pytest

from tests.cli_support import SECRET, help_output, parse_error, parse_error_with


@pytest.mark.parametrize(
    'argv',
    [
        ['--ts3pasword', SECRET],  # typo: unrecognized flag and its value
        ['--ts3pasword=' + SECRET],
        [SECRET],  # stray positional
        ['--ts3=' + SECRET],  # ambiguous abbreviation
        ['--ts3password', SECRET, '--bogus', 'x'],
        # a password can itself look like an option
        ['--ts3pasword', '-' + SECRET],
        ['--ts3pasword', '--' + SECRET],
        ['--ts3pasword', '--' + SECRET + '=x'],
        ['-' + SECRET],
    ],
    ids=[
        'typo',
        'typo-equals',
        'positional',
        'ambiguous',
        'other-unknown',
        'dash-value',
        'double-dash-value',
        'double-dash-value-with-equals',
        'lone-dash-value',
    ],
)
def test_argument_errors_never_print_a_value(argv, capsys):
    err = parse_error(argv, capsys)

    assert 'error:' in err
    assert SECRET not in err


def test_unknown_arguments_are_hidden_entirely(capsys):
    # A mistyped flag name and a dash-prefixed password are indistinguishable,
    # so no unknown token is shown -- only that values were hidden.
    err = parse_error(['--ts3pasword', '-' + SECRET], capsys)

    assert 'unrecognized arguments: … … (argument values hidden; see --help)' in err


def test_known_flags_stay_visible(capsys):
    err = parse_error(['--ts3password=' + SECRET, '--ts3host'], capsys)

    assert 'argument --ts3host: expected one argument' in err
    assert SECRET not in err


def test_masking_replaces_whole_values_only(capsys):
    # a stray value "3" -- not a password, attached to no flag -- must not
    # turn "--ts3port" into "--ts…port"
    err = parse_error(['3', '--ts3'], capsys)

    assert 'could match --ts3host, --ts3port' in err
    assert 'ambiguous option: … could match' in err


def test_an_ambiguous_abbreviation_of_the_password_flag_is_treated_as_one(capsys):
    # "--ts3=3" might mean --ts3password=3: censoring too much is the safe side
    err = parse_error(['--ts3=3'], capsys)

    assert '3' not in err.split('error:')[1]


def test_a_password_is_censored_wherever_it_appears(capsys):
    # The password "3" is part of "--ts3port": showing that flag shows the
    # password. Censoring wins over readability.
    err = parse_error(['--ts3password', '3', '--ts3=x'], capsys)

    assert '3' not in err.split('error:')[1]
    assert '3' not in err.split('error:')[0]  # the usage line too


def test_a_flag_missing_its_value_is_still_reported(capsys):
    err = parse_error(['--ts3port'], capsys)

    assert 'argument --ts3port: expected one argument' in err


def test_an_environment_password_equal_to_a_flag_name_is_censored(capsys):
    err = parse_error_with(['--ts3port'], ['--ts3port'], capsys)

    assert '--ts3port' not in err
    assert 'expected one argument' in err


@pytest.mark.parametrize(
    'argv',
    [
        ['--ts3password=--ts3port', '--ts3port'],
        ['--ts3pass=--ts3port', '--ts3port'],  # abbreviated
        ['--ts3password', '--ts3port', '--ts3port'],
    ],
    ids=['equals', 'abbreviated', 'separate'],
)
def test_a_flag_password_equal_to_a_flag_name_is_censored(argv, capsys):
    err = parse_error(argv, capsys)

    assert '--ts3port' not in err


def test_help_censors_an_environment_password_that_matches_a_default(capsys):
    out = help_output(['--help'], capsys, secrets=['8000'])

    assert '8000' not in out
    assert '(default: *censored*)' in out


@pytest.mark.parametrize(
    'argv',
    [
        ['--ts3password', '86400', '--help'],
        ['--help', '--ts3password', '86400'],
        ['--ts3password=86400', '-h'],
        ['--ts3pass', '86400', '--help'],
    ],
    ids=['before', 'after', 'equals', 'abbreviated'],
)
def test_help_censors_a_flag_password_that_matches_help_text(argv, capsys):
    out = help_output(argv, capsys)

    assert '86400' not in out
    assert 'usage:' in out


def test_help_is_unchanged_without_a_matching_password(capsys):
    out = help_output(['--help'], capsys, secrets=['nothing-in-help'])

    assert '(default: 8000)' in out
    assert '*censored*' not in out
