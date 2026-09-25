"""The command line: a parser that never prints a value or a password."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Mapping
from typing import Any, NoReturn

from teamspeak_prometheus.config import (
    DEFAULT_LOG_LEVEL,
    DEFAULT_METRICS_PORT,
    DEFAULT_POLL_INTERVAL_IN_SECONDS,
    DEFAULT_TS3_HOST,
    DEFAULT_TS3_PORT,
    DEFAULT_TS3_USERNAME,
    LOG_LEVELS,
    MAX_POLL_INTERVAL_IN_SECONDS,
    MIN_POLL_INTERVAL_IN_SECONDS,
)
from teamspeak_prometheus.redaction import redact, secrets_for_redaction


class SafeArgumentParser(argparse.ArgumentParser):
    """An ``ArgumentParser`` whose error messages never repeat a value.

    Every command-line parser in this repository uses it -- the exporter, the
    test harness, the fake server -- and tests/test_cli.py fails on any other.

    argparse quotes command-line fragments in its errors -- ``unrecognized
    arguments: --ts3pasword <password>`` after a typo, ``ambiguous option:
    --ts3=<password>`` -- and prints them to stderr, where no logging filter
    sees them. Only option strings this parser defines stay visible; every
    other token is replaced by ``…``, and so is anything after ``=``. That
    includes tokens starting with ``-``: an unknown ``--ts3pasword`` and a
    password ``-secret`` look alike, so neither can be shown.
    """

    _argv: list[str] = []

    def __init__(
        self,
        *args: Any,
        secret_options: tuple[str, ...] = (),
        secrets: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """``secret_options`` are the flags that take a password; ``secrets``
        are passwords known from elsewhere (the environment). Both are censored
        in error messages even where they equal a flag name, which masking
        alone leaves visible."""

        super().__init__(*args, **kwargs)
        self.secret_options = secret_options
        self.secrets = list(secrets or [])

    def parse_known_args(self, args=None, namespace=None):  # type: ignore[override]
        self._argv = list(sys.argv[1:] if args is None else args)
        return super().parse_known_args(args, namespace)

    # Everything argparse prints goes through print_help, print_usage or exit
    # (errors print the usage, then exit with the message; --help prints the
    # help and exits). Censoring here covers all of it, whichever action
    # prints: a password can equal a default in the help text or a flag name.

    def print_help(self, file: Any = None) -> None:
        self._print_message(
            redact(self.format_help(), self._secrets()), file or sys.stdout
        )

    def print_usage(self, file: Any = None) -> None:
        self._print_message(
            redact(self.format_usage(), self._secrets()), file or sys.stdout
        )

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        super().exit(status, redact(message, self._secrets()) if message else None)

    def error(self, message: str) -> NoReturn:
        super().error(
            _mask_values(message, self._argv, set(self._option_string_actions))
        )

    def _secrets(self) -> list[str]:
        return secrets_for_redaction(
            self.secrets + _option_values(self._argv, self.secret_options)
        )


def _option_values(argv: list[str], options: tuple[str, ...]) -> list[str]:
    """Values given to any of ``options``, as argparse would read them:
    ``--opt value``, ``--opt=value``, and unambiguous abbreviations. Taking a
    few too many is harmless -- they are only censored."""

    values = []
    for index, token in enumerate(argv):
        name, separator, value = token.partition('=')
        if len(name) < 3 or not name.startswith('--'):
            continue
        if not any(option.startswith(name) for option in options):
            continue
        if separator:
            values.append(value)
        elif index + 1 < len(argv):
            values.append(argv[index + 1])
    return values


def _mask_values(message: str, argv: list[str], known: set[str]) -> str:
    """Replace every command-line fragment except known option strings."""

    fragments = set()
    for token in argv:
        name, separator, value = token.partition('=')
        if token in known:
            continue
        if separator and name in known:
            if value:
                fragments.add(value)
            continue
        if token:
            fragments.add(token)
    masked = message
    for fragment in sorted(fragments, key=len, reverse=True):
        # Whole fragments only: a value "3" must not mangle "--ts3port".
        pattern = r'(?<![^\s\'"=])' + re.escape(fragment) + r'(?![^\s\'"])'
        masked = re.sub(pattern, '…', masked)
    if masked != message:
        masked += ' (argument values hidden; see --help)'
    return masked


def parse_args(
    argv: list[str] | None = None, secrets: list[str] | None = None
) -> argparse.Namespace:
    """Parse flags as plain strings.

    Nothing is converted or validated here: environment variables take
    precedence over flags, so a flag is checked only if it is actually used --
    by ``resolve_config``, with the same parser as the environment variable.
    Unset flags stay ``None`` so that precedence can be traced.
    """

    parser = SafeArgumentParser(secret_options=('--ts3password',), secrets=secrets)
    parser.add_argument(
        '--ts3host',
        help=f'Hostname or ip address of TS3 server (default: {DEFAULT_TS3_HOST})',
    )
    parser.add_argument(
        '--ts3port',
        help=f'Port of TS3 server (default: {DEFAULT_TS3_PORT})',
    )
    parser.add_argument(
        '--ts3username',
        help=f'ServerQuery username of TS3 server (default: {DEFAULT_TS3_USERNAME})',
    )
    parser.add_argument(
        '--ts3password',
        help='ServerQuery password of TS3 server. Prefer TEAMSPEAK_PASSWORD: '
        'flags are visible in the process list',
        action=RememberEveryValue,
    )
    parser.add_argument(
        '--metricsport',
        help='Port on which this service exposes the metrics '
        f'(default: {DEFAULT_METRICS_PORT})',
    )
    parser.add_argument(
        '--pollinterval',
        help='Seconds between two polls of the TS3 server, '
        f'{MIN_POLL_INTERVAL_IN_SECONDS:g} to {MAX_POLL_INTERVAL_IN_SECONDS:g} '
        f'(default: {DEFAULT_POLL_INTERVAL_IN_SECONDS:g})',
    )
    parser.add_argument(
        '--loglevel',
        help=f'Log level: {", ".join(LOG_LEVELS)} (default: {DEFAULT_LOG_LEVEL})',
    )
    return parser.parse_args(argv)


class RememberEveryValue(argparse.Action):
    """For password flags: store the value as usual -- the last of repeated
    flags wins -- and also remember every value given in ``<dest>_given``.

    argparse keeps only the last value, but ``--ts3password old --ts3password
    new`` gave two passwords, and both must be censored. argparse still
    resolves abbreviations and ``--flag=value`` itself.
    """

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        setattr(namespace, self.dest, values)
        given = getattr(namespace, self.dest + '_given', None) or []
        setattr(namespace, self.dest + '_given', [*given, values])


def given_values(args: argparse.Namespace, dest: str) -> list[str]:
    """Every value given for ``dest``: all repeats of a ``RememberEveryValue``
    flag, else its single value (a default, say), else none."""

    given = getattr(args, dest + '_given', None)
    if given is not None:
        return list(given)
    value = getattr(args, dest, None)
    return [value] if value else []


def password_candidates(args: argparse.Namespace, env: Mapping[str, str]) -> list[str]:
    """Every password given, used or not: each repeat of ``--ts3password`` and
    ``TEAMSPEAK_PASSWORD``. An overridden one is a secret too."""

    return [
        value
        for value in (*given_values(args, 'ts3password'), env.get('TEAMSPEAK_PASSWORD'))
        if value
    ]
