"""Print one version's section of CHANGELOG.md, for use as GitHub release notes.

    python3 .github/scripts/changelog_section.py 1.0.0 [CHANGELOG]

A release body is a standalone document: a reference-style link whose
definition lives elsewhere in the changelog renders as literal text once the
section is lifted out. This fails -- before anything is published -- on every
label defined in the changelog but not in the section, in each reference form
Markdown has: full ``[text][label]``, collapsed ``[label][]`` and shortcut
``[label]``. Labels defined nowhere are plain text in both places, and
definitions inside the section travel with it.

Standard library only; the release workflow runs it before any publishing
step. Grew out of TilmannF/pa2_exporter's tools/changelog-section.sh.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HEADING = re.compile(r'^## \[(?P<version>[^\]]+)\]')
DEFINITION = re.compile(r'^ {0,3}\[(?P<label>[^\]]+)\]:\s')
# [text][label], [text][] or [text] -- not followed by "(" (an inline link)
REFERENCE = re.compile(r'\[(?P<text>[^\[\]]+)\](?:\[(?P<label>[^\[\]]*)\])?(?![(\[])')
CODE_SPAN = re.compile(r'`[^`]*`')


class NotesError(Exception):
    """The section is missing or would not render as a standalone document."""


def normalize(label: str) -> str:
    """Markdown matches labels case-insensitively, with whitespace collapsed."""

    return ' '.join(label.split()).casefold()


def section(changelog: str, version: str) -> str:
    """The body of ``## [version]``, up to the next version heading.

    Link definitions right before the next heading belong to the section. In
    the last section, a trailing block of definitions is the changelog's own
    (Keep a Changelog puts them at the end of the file) and is left out.
    """

    version = version.removeprefix('v')
    lines = changelog.splitlines()
    starts = [i for i, line in enumerate(lines) if HEADING.match(line)]
    for position, start in enumerate(starts):
        if HEADING.match(lines[start])['version'] != version:
            continue
        is_last = position + 1 == len(starts)
        end = len(lines) if is_last else starts[position + 1]
        body = lines[start + 1 : end]
        if is_last:
            while body and (not body[-1].strip() or DEFINITION.match(body[-1])):
                body.pop()
        return '\n'.join(body).strip('\n')
    raise NotesError(f"no '## [{version}]' section in the changelog")


def definitions(text: str) -> set[str]:
    return {
        normalize(m['label'])
        for line in text.splitlines()
        if (m := DEFINITION.match(line))
    }


def references(text: str) -> list[str]:
    """Every label referenced, in any of the three forms, outside code spans."""

    labels = []
    for line in text.splitlines():
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
