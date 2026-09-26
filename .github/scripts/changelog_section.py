"""Print one version's section of CHANGELOG.md, for use as GitHub release notes.

    python3 .github/scripts/changelog_section.py 1.0.0 [CHANGELOG]

A release body is a standalone document: a reference-style link whose
definition lives elsewhere in the changelog renders as literal text once the
section is lifted out. This fails -- before anything is published -- on every
such link. It does not guess at Markdown: the section is parsed twice by a
CommonMark parser, alone and with the rest of the changelog's definitions,
and a link that only exists in the second parse is a broken one. Headings,
code, lists and escapes are the parser's business, not a regular expression's.

Needs markdown-it-py (requirements-release.txt, installed with hashes by the
release workflow; requirements-dev.txt for the tests). Release tooling only.
Grew out of TilmannF/pa2_exporter's tools/changelog-section.sh.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.token import Token

# CommonMark plus the GitHub extensions that change block structure. How a
# reference resolves is the same in both.
MARKDOWN = MarkdownIt('commonmark').enable(['table', 'strikethrough'])
# The start of a version heading's text: "[1.0.0] - 2026-09-26"
VERSION = re.compile(r'\[(?P<version>[^\]]+)\]')
# The label as written, from the first line of a definition
RAW_LABEL = re.compile(r'^[ \t>]*\[(?P<label>(?:\\.|[^\]\\])+)\]:')

References = dict[str, dict]


class NotesError(Exception):
    """The section is missing or would not render as a standalone document."""


def parse(
    text: str, references: References | None = None
) -> tuple[list[Token], References]:
    """Block tokens and the link definitions in effect.

    ``references`` are definitions from elsewhere; the text's own are added
    to them.
    """

    env = {'references': dict(references or {})}
    # Ending in a newline, every line of a code block's content does too:
    # unclosed_fences counts on it
    tokens = MARKDOWN.parse(text if text.endswith('\n') else text + '\n', env)
    return tokens, env['references']


def unclosed_fences(tokens: list[Token]) -> list[int]:
    """Opening lines of fenced code blocks that never close.

    The parser ends such a block with its container -- at the end of a list
    item, or of the document -- and says nothing: it swallows the rest. But
    its line range tells: a block with n lines of content spans n + 2 lines
    with a closing fence, n + 1 without. Which line closes a block (its
    indentation, its container) stays the parser's call.
    """

    return [
        token.map[0]
        for token in tokens
        if token.type == 'fence'
        and token.map[1] - token.map[0] == token.content.count('\n') + 1
    ]


def section(changelog: str, version: str) -> str:
    """The body of ``## [version]``, up to the next version heading.

    Only real top-level headings count: one inside code, a list or a block
    quote is text. Definitions right before the next heading belong to the
    section; in the last section, the trailing ones are mostly the
    changelog's own link block, and only those the section uses stay.
    """

    version = version.removeprefix('v')
    lines = changelog.splitlines()
    tokens, _ = parse(changelog)
    headings = [
        (match['version'], token.map)
        for token, inline in zip(tokens, tokens[1:], strict=False)
        if token.type == 'heading_open'
        and token.tag == 'h2'
        and token.level == 0
        and (match := VERSION.match(inline.content))
    ]
    for position, (found, (_, start)) in enumerate(headings):
        if found != version:
            continue
        is_last = position + 1 == len(headings)
        end = len(lines) if is_last else headings[position + 1][1][0]
        body = lines[start:end]
        if unclosed_fences(parse('\n'.join(body))[0]):
            raise NotesError(f"a code block in '## [{version}]' is never closed")
        if is_last:
            body = without_the_link_block(body)
        return '\n'.join(body).strip('\n')
    if unclosed_fences(tokens):
        raise NotesError(
            f"no '## [{version}]' section in the changelog; a code block "
            'before it is never closed'
        )
    raise NotesError(f"no '## [{version}]' section in the changelog")


def without_the_link_block(body: list[str]) -> list[str]:
    """Drop the trailing definitions, except those the rest of ``body`` uses.

    Used means: taking it out changes a link.
    """

    tokens, _ = parse('\n'.join(body))
    cut = max((token.map[1] for token in tokens if token.map), default=0)
    text, block = body[:cut], body[cut:]
    while text and not text[-1].strip():
        text.pop()
    _, references = parse('\n'.join(block))
    spans = sorted(tuple(reference['map']) for reference in references.values())

    def links_without(dropped: tuple[int, int] | None) -> Counter:
        kept = [
            line
            for number, line in enumerate(block)
            if not (dropped and dropped[0] <= number < dropped[1])
        ]
        return links(parse('\n'.join([*text, '', *kept]))[0])

    everything = links_without(None)
    used = [span for span in spans if links_without(span) != everything]
    kept = [line for start, end in used for line in block[start:end]]
    return text + [''] + kept if kept else text


def links(tokens: list[Token]) -> Counter:
    """Every link and image: kind, text, target."""

    found: Counter = Counter()
    for token in tokens:
        text, href = None, ''
        for child in token.children or []:
            if child.type == 'link_open':
                text, href = [], child.attrGet('href')
            elif child.type == 'link_close':
                found['link', ''.join(text), href] += 1
                text = None
            elif child.type == 'image':
                found['image', child.content, child.attrGet('src')] += 1
            elif text is not None:
                text.append(child.content)
    return found


def broken_links(notes: str, changelog: str) -> list[str]:
    """The links in ``notes`` that need a definition from outside them."""

    changelog_lines = changelog.splitlines()
    _, everywhere = parse(changelog)
    alone, own = parse(notes)
    outside = {label: ref for label, ref in everywhere.items() if label not in own}
    in_context, _ = parse(notes, outside)
    broken = links(in_context) - links(alone)
    messages = []
    for kind, text, href in sorted(broken):
        for label, reference in sorted(outside.items()):
            if reference['href'] != href:
                continue
            line = reference['map'][0]
            raw = RAW_LABEL.match(changelog_lines[line])
            written = raw['label'] if raw else label
            shown = f'![{text}]' if kind == 'image' else f'[{text}]'
            messages.append(f'{shown} uses [{written}], defined on line {line + 1}')
    return messages


def release_notes(changelog: str, version: str) -> str:
    notes = section(changelog, version)
    if not notes.strip():
        raise NotesError(f"the '## [{version.removeprefix('v')}]' section is empty")
    broken = broken_links(notes, changelog)
    if broken:
        raise NotesError(
            'the section links to definitions outside it: '
            + '; '.join(broken)
            + '. Use an inline link, or move the definition into the section'
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
