"""Keeping secrets and forged lines out of everything the exporter prints."""

from __future__ import annotations

import functools
import itertools
import logging
import re
import sys
from collections.abc import Mapping

REDACTED = '*censored*'


def secrets_for_redaction(secrets: list[str]) -> list[str]:
    """Non-empty and longest first, so a secret containing another is
    replaced whole."""

    return sorted({s for s in secrets if s}, key=len, reverse=True)


def redact(text: str, secrets: list[str]) -> str:
    """Replace every occurrence of each secret; guarantee none is left.

    One pass over the original text, secrets matched longest first. The
    replacement is ``*censored*`` -- unless that would leave a secret in the
    result: a secret inside the marker itself (a password ``censor``) or
    re-formed across its edge (``d*b`` from ``…censore**d*`` + ``b``). Then the
    pass is repeated with a marker made of a character that occurs in no
    secret, which provably cannot contain or re-form one.

    ``secrets`` must come from ``secrets_for_redaction``.
    """

    if not secrets:
        return text
    pattern = _secrets_pattern(tuple(secrets))
    result = pattern.sub(lambda _: REDACTED, text)
    if any(secret in result for secret in secrets):
        marker = _marker_avoiding(secrets)
        result = pattern.sub(lambda _: marker, text)
    return result


@functools.lru_cache(maxsize=8)
def _secrets_pattern(secrets: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile('|'.join(re.escape(secret) for secret in secrets))


def _marker_avoiding(secrets: list[str]) -> str:
    """Eight copies of the first candidate character in no secret."""

    used = set(''.join(secrets))
    candidates = itertools.chain(
        '#~^%', (chr(point) for point in range(0x2580, sys.maxunicode + 1))
    )
    character = next(c for c in candidates if c not in used and c.isprintable())
    return character * 8


# C0 and C1 control characters, DEL, and the Unicode line and paragraph
# separators some log viewers break lines on. Tab stays readable.
_CONTROL_CHARACTERS = re.compile(r'[\x00-\x08\x0a-\x1f\x7f-\x9f\u2028\u2029]')


def escape_control_characters(text: str) -> str:
    """Write every control character out, e.g. a line break as backslash-n."""

    return _CONTROL_CHARACTERS.sub(lambda match: repr(match.group())[1:-1], text)


def printable(text: str, secrets: list[str]) -> str:
    """One line of text, safe to print: censored, then escaped, then censored
    again for the escaped form of each secret -- what the log filter does for
    an argument. For the output of the healthcheck and the test tools, which
    print rather than log.
    """

    every_form = secrets_for_redaction(
        [*secrets, *(escape_control_characters(secret) for secret in secrets)]
    )
    return redact(escape_control_characters(redact(text, every_form)), every_form)


class RedactingFilter(logging.Filter):
    """Keeps secrets and forged lines out of the exporter's log.

    Text from the TeamSpeak server -- error messages, virtualserver names --
    reaches the log as arguments of a log call and is untrusted: a hostile or
    compromised server could echo the password it was just sent, or embed line
    breaks to fake log lines. On every record this filter

    * escapes control characters in string arguments, so one call is always one
      log line. The message template itself is the exporter's own text and may
      span lines (the settings banner does). Numbers are left alone so
      ``%d``/``%f`` formats keep working;
    * formats the message and replaces each secret in the finished text with
      ``*censored*`` -- so also a number that happens to be the password (a
      metrics port 8000 with password 8000), or a secret split across template
      and argument -- and in a traceback.

    A message whose format string does not fit its arguments is kept as
    template plus arguments instead of raising: logging would otherwise report
    the error on stderr, arguments included, past this filter.
    """

    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        # A secret with a control character also has an escaped form (a line
        # break as backslash-n), which reads exactly like it in a log line.
        # Both forms are censored.
        self.secrets = secrets_for_redaction(
            [*secrets, *(str(self.escape(secret)) for secret in secrets)]
        )

    def redact(self, text: str) -> str:
        return redact(text, self.secrets)

    def clean(self, value: object) -> object:
        if isinstance(value, (int, float)):
            return value
        return self.escape(self.redact(str(value)))

    @staticmethod
    def escape(value: object) -> object:
        if isinstance(value, (int, float)):
            return value
        return escape_control_characters(str(value))

    def filter(self, record: logging.LogRecord) -> bool:
        # Censor each argument before escaping it -- escaping would change a
        # secret with a control character so the raw form no longer matches --
        # then censor the finished line again: formatting or escaping can
        # assemble a secret that no single argument contained.
        if isinstance(record.args, Mapping):
            record.args = {key: self.clean(v) for key, v in record.args.items()}
        elif record.args:
            record.args = tuple(self.clean(arg) for arg in record.args)
        try:
            message = record.getMessage()
        except (TypeError, ValueError, KeyError):
            message = f'{record.msg} {self.escape(repr(record.args))}'
        record.msg = self.redact(message)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self.redact(record.exc_text)
        return True
