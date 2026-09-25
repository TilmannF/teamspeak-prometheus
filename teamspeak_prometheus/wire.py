"""The ServerQuery wire format: line framing, escaping, records."""

from __future__ import annotations

LINE_TERMINATOR = b'\n\r'

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
_UNESCAPES = {escaped[1]: raw for raw, escaped in _ESCAPES}


def escape(value: object) -> str:
    text = str(value)
    for raw, escaped in _ESCAPES:
        text = text.replace(raw, escaped)
    return text


def unescape(value: str) -> str:
    """Decode escape sequences in a single left-to-right pass.

    Sequential ``str.replace`` calls would re-examine text they just produced
    and decode an escaped backslash followed by an escape letter twice.
    """

    out: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character == '\\' and index + 1 < len(value):
            replacement = _UNESCAPES.get(value[index + 1])
            if replacement is not None:
                out.append(replacement)
                index += 2
                continue
        out.append(character)
        index += 1
    return ''.join(out)


def decode_record(payload: str) -> dict[str, str | None]:
    """Parse one ``key=value key=value`` record."""

    record: dict[str, str | None] = {}
    for chunk in payload.strip().split(' '):
        if not chunk:
            continue
        key, separator, value = chunk.partition('=')
        record[key] = unescape(value) if separator else None
    return record
