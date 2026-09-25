"""The ServerQuery wire helpers the fake server and harness are built on."""

from __future__ import annotations

import pytest

from tests.serverquery import (
    decode_pairs,
    encode_pairs,
    encode_records,
    escape,
    unescape,
)

TRICKY_VALUES = [
    'plain',
    'Test Server',
    'Zweiter Server',
    'trailing space ',
    'pipe|separated',
    'slash/path',
    'backslash\\here',
    'double\\\\backslash',
    # The sequences that a replace-based decoder gets wrong: an escaped
    # backslash followed by a character that is itself an escape code.
    'looks\\slike\\an\\escape',
    'ends with a backslash\\',
    'tab\there',
    'newline\nhere',
    'Ümläute und ß',
    '',
]


@pytest.mark.parametrize('value', TRICKY_VALUES)
def test_escaping_round_trips(value: str):
    assert unescape(escape(value)) == value


def test_escaped_values_contain_no_raw_separators():
    escaped = escape('a b|c\nd')

    assert ' ' not in escaped
    assert '|' not in escaped
    assert '\n' not in escaped


def test_an_escaped_backslash_is_not_decoded_twice():
    # escape(r'abc\s') is 'abc\\s'; a sequential replace would turn the '\s'
    # it just produced into a space.
    assert unescape('abc\\\\s') == 'abc\\s'


def test_an_unknown_escape_code_is_left_alone():
    assert unescape('\\q') == '\\q'


def test_pairs_round_trip_through_encoding():
    record = {'virtualserver_name': 'Test Server', 'virtualserver_id': 1}

    assert decode_pairs(encode_pairs(record)) == {
        'virtualserver_name': 'Test Server',
        'virtualserver_id': '1',
    }


def test_records_are_pipe_separated():
    payload = encode_records([{'a': 'one two'}, {'a': 'three'}])

    assert payload == 'a=one\\stwo|a=three'
    assert [decode_pairs(part) for part in payload.split('|')] == [
        {'a': 'one two'},
        {'a': 'three'},
    ]


def test_a_key_without_a_value_decodes_to_none():
    assert decode_pairs('flag') == {'flag': None}
