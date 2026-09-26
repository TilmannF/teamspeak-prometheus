"""The release notes read Markdown as Markdown: fenced code is text, only a
top-level heading ends a section, and what only looks like a link is none.

Each of these once fooled the pattern-based script, or was found by review
(see test_release_notes.py for the section and its links).
"""

from __future__ import annotations

import re

import pytest

from tests.release_support import changelog, notes_module

release_notes = notes_module.release_notes
NotesError = notes_module.NotesError


# -- fenced code is text, not Markdown ---------------------------------------------


@pytest.mark.parametrize(
    'fence',
    [
        ('```', '```'),
        ('~~~', '~~~'),
        ('```markdown', '```'),  # with an info string
        ('````', '````'),
    ],
    ids=['backticks', 'tildes', 'info-string', 'four'],
)
def test_a_heading_inside_a_code_block_does_not_end_the_section(fence):
    # Codex: the example cut the release notes short, silently
    opening, closing = fence
    body = f'- example:\n\n{opening}\n## [configuration]\n{closing}\n\n- after it'
    text = changelog(f'## [1.1.0]\n\n{body}', '## [1.0.0]\n\n- first')

    assert release_notes(text, '1.1.0') == body


@pytest.mark.parametrize(
    'body',
    [
        '- example:\n\n  ```\n  ## [configuration]\n  ```\n\n- after it',
        '- ```\n  ## [configuration]\n  ```\n- after it',  # Codex: on the marker line
        '1. ```\n   ## [configuration]\n   ```\n2. after it',
        '> ```\n> ## [configuration]\n> ```\n\n- after it',
        '- > ```\n  > ## [configuration]\n  > ```\n- after it',
    ],
    ids=[
        'list-item',
        'list-marker-line',
        'ordered-list',
        'block-quote',
        'quote-in-list',
    ],
)
def test_a_heading_inside_nested_code_does_not_end_the_section(body):
    text = changelog(f'## [1.1.0]\n\n{body}', '## [1.0.0]\n\n- first')

    assert release_notes(text, '1.1.0') == body


@pytest.mark.parametrize(
    'inner',
    ['```', '~~~~', '```` more'],
    ids=['shorter', 'other-character', 'with-text'],
)
def test_only_a_matching_fence_closes_a_block(inner):
    body = f'````\n{inner}\n## [configuration]\n````\n\n- after it'
    text = changelog(f'## [1.1.0]\n\n{body}', '## [1.0.0]\n\n- first')

    assert release_notes(text, '1.1.0') == body


def test_a_backtick_line_with_a_backtick_after_it_is_no_fence():
    # CommonMark: ``` a`b is inline code, so the next heading is a heading
    text = changelog('## [1.1.0]\n\n``` a`b', '## [1.0.0]\n\n- first')

    assert release_notes(text, '1.1.0') == '``` a`b'


def test_a_definition_inside_a_code_block_is_an_example():
    # Codex: the example satisfied the real reference, and published it broken
    body = '- see [docs]\n\n```markdown\n[docs]: https://x/example\n```'
    text = changelog(
        f'## [1.1.0]\n\n{body}',
        '## [1.0.0]\n\n- first',
        links='[docs]: https://x/docs\n',
    )

    with pytest.raises(NotesError, match=re.escape('[docs]')):
        release_notes(text, '1.1.0')


def test_a_reference_inside_a_code_block_is_no_link():
    body = '- example:\n\n```\nsee [docs] and [the guide][]\n```'
    text = changelog(
        f'## [1.1.0]\n\n{body}',
        '## [1.0.0]\n\n- first',
        links='[docs]: https://x/docs\n',
    )

    assert release_notes(text, '1.1.0') == body


def test_a_code_block_never_closed_fails():
    # It would swallow the rest of the notes, and every heading after it
    text = changelog('## [1.1.0]\n\n```\n- no end', '## [1.0.0]\n\n- first')

    with pytest.raises(NotesError, match='never closed'):
        release_notes(text, '1.1.0')
    with pytest.raises(NotesError, match=r"no '## \[1\.0\.0\]' section.*never closed"):
        release_notes(text, '1.0.0')


def test_a_heading_may_be_indented_up_to_three_spaces():
    text = changelog('## [1.1.0]\n\nnewer', '   ## [1.0.0]\n\n- first')

    assert release_notes(text, '1.1.0') == 'newer'
    assert release_notes(text, '1.0.0') == '- first'


@pytest.mark.parametrize(
    'body',
    ['- ## [1.0.1] in a list', '> ## [1.0.1] in a quote', '### [1.0.1] a subheading'],
    ids=['list', 'quote', 'level-three'],
)
def test_only_a_top_level_second_level_heading_ends_the_section(body):
    text = changelog(f'## [1.1.0]\n\n{body}\n\nafter it', '## [1.0.0]\n\n- first')

    assert release_notes(text, '1.1.0') == f'{body}\n\nafter it'


@pytest.mark.parametrize(
    'body',
    [
        '- example:\n\n  ```\n## [configuration]\n  ```',
        '> ```\n## [configuration]\n> ```',
    ],
    ids=['list-item', 'block-quote'],
)
def test_code_that_leaves_its_container_fails_loudly(body):
    # Unindented, the heading ends the list item or quote -- and the code
    # block with it, unclosed. Markdown renders it that way; the notes fail.
    text = changelog(f'## [1.1.0]\n\n{body}', '## [1.0.0]\n\n- first')

    with pytest.raises(NotesError, match='never closed'):
        release_notes(text, '1.1.0')


@pytest.mark.parametrize(
    'block',
    [
        '```\ncode\n    ```',  # Codex: indented four spaces, it is code
        '````\ncode\n```',  # shorter
        '```\ncode\n~~~',  # the other character
        '```\ncode\n``` more',  # text after it
        '```',  # nothing after the opening
    ],
    ids=['indented-four', 'shorter', 'other-character', 'with-text', 'opening-only'],
)
def test_a_line_that_does_not_close_a_block_fails_it(block):
    text = changelog(f'## [1.1.0]\n\n{block}', '## [1.0.0]\n\n- first')

    with pytest.raises(NotesError, match='never closed'):
        release_notes(text, '1.1.0')


@pytest.mark.parametrize(
    'block',
    [
        '```\ncode\n   ```',  # indented three spaces
        '```\ncode\n```   ',  # trailing spaces
        '```\n```',  # empty
        '~~~\ncode\n~~~~~',  # longer
        '- ```\n  code\n  ```',
        '> ```\n> code\n> ```',
    ],
    ids=['indented-three', 'trailing-spaces', 'empty', 'longer', 'list', 'quote'],
)
def test_a_block_closed_by_the_rules_passes(block):
    text = changelog(f'## [1.1.0]\n\n{block}', '## [1.0.0]\n\n- first')

    assert release_notes(text, '1.1.0') == block


def test_a_block_that_looks_closed_cannot_swallow_the_next_release():
    # Codex: the four-space "fence" passed, and the unclosed block took every
    # section after it into these notes
    text = changelog('## [1.1.0]\n\n```\ncode\n    ```', '## [1.0.0]\n\n- first')

    with pytest.raises(NotesError, match='never closed'):
        release_notes(text, '1.1.0')
    with pytest.raises(NotesError, match='before it is never closed'):
        release_notes(text, '1.0.0')


@pytest.mark.parametrize(
    ('text', 'version'),
    [
        # the last section, with nothing after the fake fence
        ('# Changelog\n\n## [1.0.0]\n\n- first\n\n```\ncode\n    ```', '1.0.0'),
        # a list item's block, ended with the item by the next heading
        (
            changelog(
                '## [1.1.0]\n\n- ```\n  code\n      ```', '## [1.0.0]\n\n- first'
            ),
            '1.1.0',
        ),
        ('# Changelog\n\n## [1.0.0]\n\n> ```\n> code\n>     ```', '1.0.0'),
        ('# Changelog\n\n## [1.0.0]\n\n- ```\n  code', '1.0.0'),
    ],
    ids=['last-line', 'list-item', 'block-quote', 'no-closing-line'],
)
def test_a_block_ending_on_a_line_that_only_looks_like_a_fence_fails(text, version):
    # Codex: where the block ends -- the text or its container -- on a
    # four-space "fence", stripping the indentation made it a closing one
    with pytest.raises(NotesError, match='never closed'):
        release_notes(text, version)


# -- what a parser knows and a pattern does not -------------------------------------


OUTSIDE = '[docs]: https://x/docs\n[img]: https://x/i.png\n'


@pytest.mark.parametrize(
    'body',
    [
        '- ``[docs]`` and ```a `[docs]` b```',  # Codex: longer code spans
        '- escaped \\[docs] is text',
        '- autolink <https://x/docs>',
        '    [docs] in an indented code block',
        '<pre>\n[docs] in an HTML block\n</pre>',
    ],
    ids=['code-spans', 'escaped', 'autolink', 'indented-code', 'html-block'],
)
def test_text_that_only_looks_like_a_reference_passes(body):
    text = changelog(f'## [1.1.0]\n\n{body}', '## [1.0.0]\n\n- first', links=OUTSIDE)

    assert release_notes(text, '1.1.0') == body


@pytest.mark.parametrize(
    'body',
    [
        '- see [docs]\n\n[docs]:',  # Codex: no destination, no definition
        '- see [docs]\n\n[docs]: <',
        '- [![logo][img]](https://x)',  # an image inside an inline link
        '| a |\n| - |\n| [docs] |',  # a table cell
    ],
    ids=['no-destination', 'bad-destination', 'image-in-link', 'table'],
)
def test_a_link_that_would_break_fails_wherever_it_is(body):
    text = changelog(f'## [1.1.0]\n\n{body}', '## [1.0.0]\n\n- first', links=OUTSIDE)

    with pytest.raises(NotesError, match='outside it'):
        release_notes(text, '1.1.0')


def test_the_error_names_the_link_its_label_and_line():
    text = changelog(
        '## [1.1.0]\n\n- see [the docs][Docs]', '## [1.0.0]\n\n- first', links=OUTSIDE
    )
    line = text.splitlines().index('[docs]: https://x/docs') + 1

    with pytest.raises(NotesError) as caught:
        release_notes(text, '1.1.0')

    assert f'[the docs] uses [docs], defined on line {line}' in str(caught.value)


def test_a_definition_in_a_block_quote_travels_with_the_section():
    body = '- see [docs]\n\n> [docs]: https://x/own'
    text = changelog(f'## [1.1.0]\n\n{body}', '## [1.0.0]\n\n- first', links=OUTSIDE)

    assert release_notes(text, '1.1.0') == body


def test_a_setext_heading_is_a_heading_too():
    text = changelog(
        '[1.1.0] - 2026-10-01\n--------------------\n\nnewer', '## [1.0.0]\n\n- first'
    )

    assert release_notes(text, '1.1.0') == 'newer'
    assert release_notes(text, '1.0.0') == '- first'


def test_the_last_section_keeps_a_definition_with_its_title_line():
    text = changelog(
        '## [1.0.0]\n\n- see [docs]',
        links='[docs]: https://x/docs\n  "The docs"\n[1.0.0]: https://x/v1.0.0\n',
    )

    assert release_notes(text, '1.0.0') == (
        '- see [docs]\n\n[docs]: https://x/docs\n  "The docs"'
    )
