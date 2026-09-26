"""Print one version's section of CHANGELOG.md, for use as GitHub release notes.

    python3 .github/scripts/changelog_section.py 1.0.0 [CHANGELOG]

A release body is a standalone document: a reference-style link whose
definition lives elsewhere in the changelog renders as literal text once the
section is lifted out. This fails -- before anything is published -- on every
label defined in the changelog but not in the section, in each reference form
Markdown has: full ``[text][label]``, collapsed ``[label][]`` and shortcut
``[label]``. Labels defined nowhere are plain text in both places, and
definitions inside the section travel with it. Fenced code blocks are text,
not Markdown: a heading, definition or reference inside one is none of those.

Standard library only; the release workflow runs it before any publishing
step. Grew out of TilmannF/pa2_exporter's tools/changelog-section.sh.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HEADING = re.compile(r'^ {0,3}##[ \t]+\[(?P<version>[^\]]+)\]')
# The destination may follow the colon directly, or on the next line.
DEFINITION = re.compile(r'^ {0,3}\[(?P<label>[^\]]+)\]:')
# [text][label], [text][] or [text] -- not followed by "(" (an inline link)
REFERENCE = re.compile(r'\[(?P<text>[^\[\]]+)\](?:\[(?P<label>[^\[\]]*)\])?(?![(\[])')
CODE_SPAN = re.compile(r'`[^`]*`')
# Opening or closing fence. Inside a list item or block quote a fence is
# indented or prefixed; any such prefix is accepted -- an approximation that
# errs toward reading a line as code.
FENCE = re.compile(r'^[ \t>]*(?P<fence>`{3,}|~{3,})(?P<info>.*)$')


class NotesError(Exception):
    """The section is missing or would not render as a standalone document."""


def normalize(label: str) -> str:
    """Markdown matches labels case-insensitively, with whitespace collapsed."""

    return ' '.join(label.split()).casefold()


def fenced(lines: list[str]) -> tuple[list[bool], bool]:
    """Which lines are fenced code (the fences too), and whether one is open.

    As in CommonMark, a block closes with a fence of the same character, at
    least as long, with nothing after it; one that never closes runs to the
    end. A backtick fence's info string cannot contain a backtick.
    """

    marks: list[bool] = []
    fence = ''
    for line in lines:
        match = FENCE.match(line)
        if not fence:
            if match and not (match['fence'][0] == '`' and '`' in match['info']):
                fence = match['fence']
            marks.append(bool(fence))
            continue
        marks.append(True)
        if (
            match
            and match['fence'][0] == fence[0]
            and len(match['fence']) >= len(fence)
            and not match['info'].strip()
        ):
            fence = ''
    return marks, bool(fence)


def prose(text: str) -> list[str]:
    """The lines of ``text`` outside fenced code blocks."""

    lines = text.splitlines()
    return [
        line for line, code in zip(lines, fenced(lines)[0], strict=True) if not code
    ]


def section(changelog: str, version: str) -> str:
    """The body of ``## [version]``, up to the next version heading.

    Link definitions right before the next heading belong to the section. In
    the last section, the trailing block of definitions is mostly the
    changelog's own (Keep a Changelog puts them at the end of the file): only
    the definitions the section uses stay.
    """

    version = version.removeprefix('v')
    lines = changelog.splitlines()
    code, still_open = fenced(lines)
    starts = [i for i, line in enumerate(lines) if not code[i] and HEADING.match(line)]
    for position, start in enumerate(starts):
        if HEADING.match(lines[start])['version'] != version:
            continue
        is_last = position + 1 == len(starts)
        end = len(lines) if is_last else starts[position + 1]
        body = lines[start + 1 : end]
        if fenced(body)[1]:
            # It would swallow the rest of the release notes
            raise NotesError(f"a code block in '## [{version}]' is never closed")
        if is_last:
            body = without_the_link_block(body)
        return '\n'.join(body).strip('\n')
    if still_open:
        # The unclosed block hides every heading after it
        raise NotesError(
            f"no '## [{version}]' section in the changelog; a code block "
            'before it is never closed'
        )
    raise NotesError(f"no '## [{version}]' section in the changelog")


def without_the_link_block(body: list[str]) -> list[str]:
    """Drop the trailing definitions, except those the rest of ``body`` uses."""

    cut = len(body)
    while cut and (not body[cut - 1].strip() or DEFINITION.match(body[cut - 1])):
        cut -= 1
    text = body[:cut]
    used = {normalize(label) for label in references('\n'.join(text))}
    kept = [
        line
        for line in body[cut:]
        if (match := DEFINITION.match(line)) and normalize(match['label']) in used
    ]
    return text + [''] + kept if kept else text


def definitions(text: str) -> set[str]:
    return {
        normalize(m['label']) for line in prose(text) if (m := DEFINITION.match(line))
    }


def references(text: str) -> list[str]:
    """Every label referenced, in any of the three forms, outside code."""

    labels = []
    for line in prose(text):
        if DEFINITION.match(line):
            continue
        for match in REFERENCE.finditer(CODE_SPAN.sub('', line)):
            label = match['label']
            labels.append(label if label else match['text'])  # collapsed, shortcut
    return labels


def broken_references(notes: str, changelog: str) -> list[str]:
    outside = definitions(changelog) - definitions(notes)
    return sorted({label for label in references(notes) if normalize(label) in outside})


def release_notes(changelog: str, version: str) -> str:
    notes = section(changelog, version)
    if not notes.strip():
        raise NotesError(f"the '## [{version.removeprefix('v')}]' section is empty")
    broken = broken_references(notes, changelog)
    if broken:
        raise NotesError(
            'the section references '
            + ', '.join(f'[{label}]' for label in broken)
            + ', defined outside it; use an inline link, or move the '
            'definition into the section'
        )
    return notes


def main(argv: list[str]) -> int:
    if not 1 <= len(argv) <= 2:
        print('usage: changelog_section.py VERSION [CHANGELOG]', file=sys.stderr)
        return 2
    path = Path(argv[1] if len(argv) == 2 else 'CHANGELOG.md')
    try:
        print(release_notes(path.read_text(encoding='utf-8'), argv[0]))
    except NotesError as err:
        print(f'{path}: {err}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
