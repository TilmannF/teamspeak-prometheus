"""The GitHub release notes: one CHANGELOG.md section, standalone.

``.github/scripts/changelog_section.py`` runs in the release workflow before
anything is published. It is imported here directly, so these are unit tests.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

from teamspeak_prometheus import __version__

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / '.github' / 'scripts' / 'changelog_section.py'

_spec = importlib.util.spec_from_file_location('changelog_section', SCRIPT)
notes_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(notes_module)
release_notes = notes_module.release_notes
NotesError = notes_module.NotesError


def changelog(*sections: str, links: str = '') -> str:
    return '# Changelog\n\nIntro.\n\n' + '\n\n'.join(sections) + '\n\n' + links


# -- the section ------------------------------------------------------------------


def test_the_real_changelog_yields_notes_for_the_version():
    notes = release_notes((ROOT / 'CHANGELOG.md').read_text(), f'v{__version__}')

    assert '### Added' in notes
    assert f'[{__version__}]: https://' not in notes  # the end-of-file link block


def test_notes_stop_at_the_next_version():
    text = changelog('## [1.1.0] - 2026-10-01\n\n- newer', '## [1.0.0]\n\n- first')

    assert release_notes(text, '1.1.0') == '- newer'
    assert release_notes(text, 'v1.0.0') == '- first'


def test_the_changelogs_own_link_block_is_not_part_of_the_last_section():
    text = changelog('## [1.0.0]\n\n- first', links='[1.0.0]: https://x/v1.0.0\n')

    assert release_notes(text, '1.0.0') == '- first'


@pytest.mark.parametrize('version', ['1.2.0', '1.0'])
def test_a_missing_section_fails(version):
    with pytest.raises(NotesError, match=rf"no '## \[{version}\]' section"):
        release_notes(changelog('## [1.0.0]\n\n- first'), version)


def test_an_empty_section_fails():
    with pytest.raises(NotesError, match='empty'):
        release_notes(changelog('## [1.1.0]\n\n', '## [1.0.0]\n\n- first'), '1.1.0')


# -- links that would break once the section is lifted out ------------------------


@pytest.mark.parametrize(
    ('body', 'label'),
    [
        ('- see [the docs][docs]', 'docs'),  # full
        ('- see [the manual][]', 'the manual'),  # collapsed
        ('- see [the guide]', 'the guide'),  # shortcut
        ('- see [THE  Guide]', 'THE  Guide'),  # labels match case-insensitively
        ('- see ![logo][img]', 'img'),  # images use the same references
    ],
    ids=['full', 'collapsed', 'shortcut', 'case-and-space', 'image'],
)
def test_a_reference_defined_outside_the_section_fails(body, label):
    text = changelog(
        f'## [1.1.0]\n\n{body}',
        '## [1.0.0]\n\n- first',
        links='[docs]: https://x/docs\n[the manual]: https://x/m\n'
        '[the guide]: https://x/g\n[img]: https://x/i.png\n',
    )

    with pytest.raises(NotesError, match=re.escape(f'[{label}]')):
        release_notes(text, '1.1.0')


def test_definitions_at_the_end_of_the_section_travel_with_it():
    # Codex: they used to be dropped when the next heading followed them
    section = '## [1.1.0]\n\n- see [the docs][docs] and [the guide]\n\n'
    section += '[docs]: https://x/docs\n[the guide]: https://x/g'
    text = changelog(section, '## [1.0.0]\n\n- first')

    notes = release_notes(text, '1.1.0')

    assert '[docs]: https://x/docs' in notes
    assert '[the guide]: https://x/g' in notes


@pytest.mark.parametrize(
    'body',
    [
        '- see [the docs](https://x/docs)',  # inline link
        '- ticked [x] and [1.0.0] mentioned',  # defined nowhere: plain text anyway
        '- code `a[docs]` is not a link',  # inside a code span
    ],
    ids=['inline-link', 'undefined-label', 'code-span'],
)
def test_harmless_brackets_pass(body):
    text = changelog(
        f'## [1.1.0]\n\n{body}', '## [1.0.0]\n\n- first', links='[docs]: https://x\n'
    )

    assert release_notes(text, '1.1.0') == body


def test_a_label_defined_both_inside_and_outside_is_fine():
    text = changelog(
        '## [1.1.0]\n\n- see [docs]\n\n[docs]: https://x/new',
        '## [1.0.0]\n\n- first',
        links='[docs]: https://x/old\n',
    )

    assert '[docs]: https://x/new' in release_notes(text, '1.1.0')


# -- the command line the workflow calls --------------------------------------------


@pytest.mark.smoke
def test_the_script_prints_the_notes_and_fails_loudly(tmp_path):
    ok = subprocess.run(
        [sys.executable, str(SCRIPT), f'v{__version__}', str(ROOT / 'CHANGELOG.md')],
        capture_output=True,
        text=True,
        timeout=10,
    )
    (tmp_path / 'CHANGELOG.md').write_text(changelog('## [0.9.0]\n\n- old'))
    missing = subprocess.run(
        [sys.executable, str(SCRIPT), '1.0.0', str(tmp_path / 'CHANGELOG.md')],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert ok.returncode == 0 and '### Added' in ok.stdout
    assert missing.returncode == 1 and "no '## [1.0.0]' section" in missing.stderr
