"""The release tag, __version__ and CHANGELOG.md agree; the tag check
accepts only a matching vX.Y.Z.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from teamspeak_prometheus import __version__
from tests.release_support import ROOT, SCRIPT, SEMVER


def test_the_version_is_plain_semver():
    assert SEMVER.match(__version__)


def test_the_changelog_has_a_section_for_the_version():
    changelog = (ROOT / 'CHANGELOG.md').read_text()

    assert f'## [{__version__}]' in changelog


def validate(tmp_path: Path, tag: str, version: str = '1.2.3', section: str = '1.2.3'):
    (tmp_path / 'teamspeak_prometheus').mkdir()
    (tmp_path / 'teamspeak_prometheus' / '__init__.py').write_text(
        f"__version__ = '{version}'\n"
    )
    (tmp_path / 'CHANGELOG.md').write_text(f'# Changelog\n\n## [{section}]\n')
    return subprocess.run(
        ['bash', str(SCRIPT), tag, str(tmp_path)],
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
    (tmp_path / 'teamspeak_prometheus').mkdir()
    (tmp_path / 'teamspeak_prometheus' / '__init__.py').write_text(
        '# no version here\n'
    )
    (tmp_path / 'CHANGELOG.md').write_text('## [1.2.3]\n')

    result = subprocess.run(
        ['bash', str(SCRIPT), 'v1.2.3', str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 1
    assert 'no __version__' in result.stdout


@pytest.mark.smoke
def test_the_real_repository_validates_for_its_own_version():
    result = subprocess.run(
        ['bash', str(SCRIPT), f'v{__version__}', str(ROOT)],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout
