"""Minimal TeamSpeak ServerQuery wire helpers shared by the test support code.

The escaping rules and the ``\\n\\r`` line terminator match what a real TS3
ServerQuery interface uses, so anything built on this talks to the real
protocol as well as to `tests.fake_ts3_server`.
"""

from __future__ import annotations

LINE_TERMINATOR = b'\n\r'
BANNER = b'TS3\n\r'

_ESCAPES = [
    ('\\', r'\\'),
    ('/', r'\/'),
    (' ', r'\s'),
    ('|', r'\p'),
    ('\a', r'\a'),
    ('\b', r'\b'),
    ('\f', r'\f'),
    ('\n', r'\n'),
    ('\r', r'\r'),
    ('\t', r'\t'),
    ('\v', r'\v'),
]


def escape(value: object) -> str:
    text = str(value)
    for raw, escaped in _ESCAPES:
        text = text.replace(raw, escaped)
    return text


def unescape(value: str) -> str:
    for raw, escaped in reversed(_ESCAPES):
        value = value.replace(escaped, raw)
    return value


def encode_pairs(pairs: dict[str, object]) -> str:
    """Render one ``key=value`` record."""

    return ' '.join('%s=%s' % (key, escape(value)) for key, value in pairs.items())


def encode_records(records: list[dict[str, object]]) -> str:
    """Render a pipe-separated list of records, as ``serverlist`` returns."""

    return '|'.join(encode_pairs(record) for record in records)


def decode_pairs(payload: str) -> dict[str, str | None]:
    parsed: dict[str, str | None] = {}
    for chunk in payload.strip().split(' '):
        if not chunk:
            continue
        key, separator, value = chunk.partition('=')
        parsed[key] = unescape(value) if separator else None
    return parsed
