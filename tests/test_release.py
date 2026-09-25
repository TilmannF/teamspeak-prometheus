"""Release consistency: the tag, ``__version__`` and CHANGELOG.md agree, and
only a validated tag push can publish.

``.github/scripts/validate-release-tag.sh`` runs in the release workflow before
anything is published. Its tests spawn bash, so they are ``smoke``; the checks
of the repository and of the workflow file itself are plain unit tests.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

from teamspeak_prometheus import __version__

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / '.github' / 'scripts' / 'validate-release-tag.sh'
SEMVER = re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$')


# -- the repository as it is --------------------------------------------------


def test_the_version_is_plain_semver():
    assert SEMVER.match(__version__)


def test_the_changelog_has_a_section_for_the_version():
    changelog = (ROOT / 'CHANGELOG.md').read_text()

    assert f'## [{__version__}]' in changelog


# -- the release script -------------------------------------------------------


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


# -- the release workflow: only a validated tag push can publish --------------

WORKFLOW = ROOT / '.github' / 'workflows' / 'release.yml'
TAG_PUSH = "github.event_name == 'push'"


def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def steps() -> list[dict]:
    return workflow()['jobs']['release']['steps']


def index_of(predicate) -> list[int]:
    return [i for i, step in enumerate(steps()) if predicate(step)]


def uses(action: str):
    return lambda step: str(step.get('uses', '')).startswith(action + '@')


def gated_on_tag_push(condition: str) -> bool:
    """``condition`` is the tag-push check, alone or AND-ed with more -- never
    something an OR could open up."""

    return '||' not in condition and (
        condition == TAG_PUSH or condition.startswith(TAG_PUSH + ' && ')
    )


@pytest.mark.parametrize(
    ('condition', 'expected'),
    [
        (TAG_PUSH, True),
        (TAG_PUSH + " && steps.dockerhub.outputs.enabled == 'true'", True),
        (TAG_PUSH + ' || true', False),
        ("steps.dry_run.outputs.value != 'true'", False),
        ('', False),
    ],
)
def test_the_gate_check_itself(condition, expected):
    assert gated_on_tag_push(condition) is expected


def test_a_manual_run_takes_no_inputs():
    # YAML 1.1 reads the key "on" as True
    triggers = workflow()[True]

    assert 'workflow_dispatch' in triggers
    assert not (triggers['workflow_dispatch'] or {}).get('inputs')


def test_the_tag_check_runs_on_every_tag_push():
    (validate,) = index_of(lambda s: s.get('name') == 'Validate release tag')
    step = steps()[validate]

    assert step['if'] == TAG_PUSH
    assert 'validate-release-tag.sh' in step['run']


@pytest.mark.parametrize(
    'publishing',
    [
        'docker/login-action',
        'actions/attest-build-provenance',
        'softprops/action-gh-release',
    ],
)
def test_publishing_steps_run_only_on_a_tag_push_after_the_check(publishing):
    (validate,) = index_of(lambda s: s.get('name') == 'Validate release tag')
    found = index_of(uses(publishing))

    assert found
    for i in found:
        assert gated_on_tag_push(steps()[i].get('if', '')), steps()[i]
        assert i > validate


def test_images_are_pushed_only_on_a_tag_push_after_the_check():
    (validate,) = index_of(lambda s: s.get('name') == 'Validate release tag')
    (build,) = index_of(uses('docker/build-push-action'))

    assert steps()[build]['with']['push'] == '${{ ' + TAG_PUSH + ' }}'
    assert build > validate


def test_latest_is_tagged_only_on_a_tag_push():
    (meta,) = index_of(uses('docker/metadata-action'))

    assert (
        f'type=raw,value=latest,enable=${{{{ {TAG_PUSH} }}}}'
        in (steps()[meta]['with']['tags'])
    )


# -- least privilege: only the tag-push job can write --------------------------

WRITE_PERMISSIONS = {'contents', 'packages', 'id-token', 'attestations'}


def jobs() -> dict[str, dict]:
    return workflow()['jobs']


def writes(permissions: dict | str | None) -> set[str]:
    if permissions in ('write-all',):
        return {'*'}
    if not isinstance(permissions, dict):
        return set()
    return {scope for scope, level in permissions.items() if level == 'write'}


def test_the_workflow_grants_nothing_but_read_by_default():
    assert workflow()['permissions'] == {'contents': 'read'}


def test_only_the_tag_push_job_has_write_permissions():
    writers = {name for name, job in jobs().items() if writes(job.get('permissions'))}

    assert writers == {'release'}
    assert jobs()['release']['if'] == TAG_PUSH
    assert writes(jobs()['release']['permissions']) == WRITE_PERMISSIONS


def test_a_manual_run_is_a_separate_read_only_build_job():
    build = jobs()['build']

    assert build['if'] == "github.event_name == 'workflow_dispatch'"
    assert writes(build.get('permissions')) == set()
    publishing = [
        step
        for step in build['steps']
        if str(step.get('uses', '')).split('@')[0]
        in {
            'docker/login-action',
            'actions/attest-build-provenance',
            'softprops/action-gh-release',
        }
    ]
    assert publishing == []
    (image,) = [s for s in build['steps'] if uses('docker/build-push-action')(s)]
    assert image['with']['push'] is False


def test_both_jobs_build_the_same_platforms():
    def platforms(job: str) -> str:
        (image,) = [
            s for s in jobs()[job]['steps'] if uses('docker/build-push-action')(s)
        ]
        return image['with']['platforms']

    assert platforms('build') == platforms('release')


# -- no workflow leaves the Git token behind ------------------------------------


def all_checkouts() -> list[tuple[str, str, dict]]:
    found = []
    for path in sorted((ROOT / '.github' / 'workflows').glob('*.yml')):
        for name, job in yaml.safe_load(path.read_text())['jobs'].items():
            for step in job.get('steps', []):
                if uses('actions/checkout')(step):
                    found.append((path.name, name, step))
    return found


def test_every_workflow_is_scanned():
    assert {f for f, _, _ in all_checkouts()} >= {
        'ci.yml',
        'release.yml',
        'codeql.yml',
        'scorecard.yml',
    }


def test_no_checkout_persists_its_credentials():
    # actions/checkout writes the token into .git/config unless told not to;
    # no job here pushes to Git, so none needs it there.
    persisting = [
        f'{file}:{job}'
        for file, job, step in all_checkouts()
        if (step.get('with') or {}).get('persist-credentials') is not False
    ]

    assert persisting == []
