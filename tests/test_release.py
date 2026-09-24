"""Release consistency: the tag, ``__version__`` and CHANGELOG.md agree.

``.github/scripts/validate-release-tag.sh`` runs in the release workflow before
anything is published. Its tests spawn bash, so they are ``smoke``; the checks
of the repository itself are plain unit tests.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

import app

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / '.github' / 'scripts' / 'validate-release-tag.sh'
SEMVER = re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$')


# -- the repository as it is --------------------------------------------------


def test_the_version_is_plain_semver():
    assert SEMVER.match(app.__version__)


def test_the_changelog_has_a_section_for_the_version():
    changelog = (ROOT / 'CHANGELOG.md').read_text()

    assert f'## [{app.__version__}]' in changelog


# -- the release script -------------------------------------------------------


def validate(tmp_path: Path, tag: str, version: str = '1.2.3', section: str = '1.2.3'):
    (tmp_path / 'app.py').write_text(f"__version__ = '{version}'\n")
    (tmp_path / 'CHANGELOG.md').write_text(f'# Changelog\n\n## [{section}]\n')
    return subprocess.run(
        ['bash', str(SCRIPT), tag, str(tmp_path / 'app.py')],
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.smoke
def test_a_matching_tag_passes(tmp_path):
    result = validate(tmp_path, 'v1.2.3')

    assert result.returncode == 0, result.stdout


@pytest.mark.smoke
@pytest.mark.parametrize(
    'tag',
    [
        'v1.2',
        'v1.2.foo',
        'v01.2.3',
        'v1.02.3',
        'v1.2.03',
        'v1.2.3-rc.1',
        'v1.2.3+build',
        'v1.2.3+foo..bar',
        'v1.2.3-alpha..x',
        'v1.2.3-01',
        '1.2.3',
        'v1.2.3 ',
    ],
)
def test_a_tag_that_is_not_plain_semver_fails(tmp_path, tag):
    result = validate(tmp_path, tag)

    assert result.returncode == 1
    assert 'not a release tag' in result.stdout


@pytest.mark.smoke
def test_a_tag_ahead_of_the_version_fails(tmp_path):
    result = validate(tmp_path, 'v1.2.4')

    assert result.returncode == 1
    assert "does not match __version__ '1.2.3'" in result.stdout


@pytest.mark.smoke
def test_a_version_without_a_changelog_section_fails(tmp_path):
    result = validate(tmp_path, 'v1.2.3', section='1.2.2')

    assert result.returncode == 1
    assert "no '## [1.2.3]' section" in result.stdout


@pytest.mark.smoke
def test_a_missing_version_fails(tmp_path):
    (tmp_path / 'app.py').write_text('print("no version here")\n')
    (tmp_path / 'CHANGELOG.md').write_text('## [1.2.3]\n')

    result = subprocess.run(
        ['bash', str(SCRIPT), 'v1.2.3', str(tmp_path / 'app.py')],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 1
    assert 'no __version__' in result.stdout


@pytest.mark.smoke
def test_the_real_repository_validates_for_its_own_version():
    result = subprocess.run(
        ['bash', str(SCRIPT), f'v{app.__version__}', str(ROOT / 'app.py')],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout
