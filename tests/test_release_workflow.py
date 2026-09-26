"""The release workflow: only a validated tag push publishes, with
least privilege; notes, Docker Hub page and manual runs.
"""

from __future__ import annotations

import re

import pytest

from tests.release_support import (
    ROOT,
    TAG_PUSH,
    WRITE_PERMISSIONS,
    gated_on_tag_push,
    index_of,
    jobs,
    steps,
    uses,
    workflow,
    writes,
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
        'peter-evans/dockerhub-description',
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


def test_release_notes_come_from_the_changelog_before_anything_is_published():
    (notes,) = index_of(
        lambda s: s.get('name') == 'Take the release notes from CHANGELOG.md'
    )
    publishing = index_of(
        lambda s: any(
            uses(action)(s)
            for action in ('docker/login-action', 'docker/build-push-action')
        )
    )

    assert 'changelog_section.py "$TAG"' in steps()[notes]['run']
    assert '> "$RUNNER_TEMP/release-notes.md"' in steps()[notes]['run']
    assert notes < min(publishing)
    (release,) = index_of(uses('softprops/action-gh-release'))
    assert steps()[release]['with'] == {
        'body_path': '${{ runner.temp }}/release-notes.md'
    }


def test_the_docker_hub_page_is_synced_only_with_credentials_after_the_push():
    (sync,) = index_of(uses('peter-evans/dockerhub-description'))
    (build,) = index_of(uses('docker/build-push-action'))
    step = steps()[sync]

    assert sync > build
    assert "steps.dockerhub.outputs.enabled == 'true'" in step['if']
    assert step['with']['repository'] == 'tilmannf/teamspeak-prometheus'
    assert step['with']['enable-url-completion'] is True


def test_a_manual_run_exercises_metadata_action_without_logging_in():
    build = jobs()['build']['steps']

    assert any(uses('docker/metadata-action')(s) for s in build)
    assert not any(uses('docker/login-action')(s) for s in build)


def test_a_manual_run_previews_the_release_notes():
    runs = [step.get('run', '') for step in jobs()['build']['steps']]

    assert any('changelog_section.py' in run for run in runs)


# -- the release-notes parser: exact, hashed, isolated ------------------------------

NOTES_PYTHON = '"$RUNNER_TEMP/notes/bin/python" .github/scripts/changelog_section.py'
REQUIRED_FLAGS = ['--require-hashes', '--no-deps', '--only-binary=:all:']


@pytest.mark.parametrize('job', ['build', 'release'])
def test_the_notes_run_on_the_parser_installed_from_hashes_just_before(job):
    job_steps = jobs()[job]['steps']
    runs = [str(step.get('run', '')) for step in job_steps]
    (notes,) = [i for i, run in enumerate(runs) if 'changelog_section.py' in run]
    (install,) = [i for i, run in enumerate(runs) if 'pip' in run]
    command = ' '.join(runs[install].replace('\\\n', ' ').split())

    assert NOTES_PYTHON in runs[notes]
    assert install == notes - 1
    assert 'python3 -m venv "$RUNNER_TEMP/notes"' in command
    assert all(flag in command.split() for flag in REQUIRED_FLAGS)
    assert command.endswith('-r requirements-release.txt')


def test_no_workflow_installs_anything_else_with_pip():
    installs = [
        (path.name, line.strip())
        for path in (ROOT / '.github' / 'workflows').glob('*.yml')
        for line in path.read_text().replace('\\\n', ' ').splitlines()
        if 'pip' in line and 'install' in line
    ]

    assert {name for name, _ in installs} == {'release.yml'}
    assert all('--require-hashes' in line for _, line in installs)


def release_requirements() -> dict[str, list[str]]:
    """Name to hashes, for every requirement in requirements-release.txt."""

    text = (ROOT / 'requirements-release.txt').read_text().replace('\\\n', ' ')
    found = {}
    for line in text.splitlines():
        if not (line := line.split('#')[0].strip()):
            continue
        spec, *options = line.split()
        name, _, version = spec.partition('==')
        assert version, f'{spec}: pin an exact version'
        found[name] = [o.removeprefix('--hash=sha256:') for o in options]
    return found


def test_every_release_requirement_is_pinned_with_hashes():
    requirements = release_requirements()

    assert set(requirements) == {'markdown-it-py', 'mdurl'}
    for hashes in requirements.values():
        assert hashes
        assert all(len(h) == 64 and int(h, 16) >= 0 for h in hashes)


def test_the_pins_cover_what_the_parser_needs():
    # --no-deps installs nothing unlisted: a dependency the parser gains in
    # an update must be pinned too, or the release fails at import.
    from importlib.metadata import requires

    def needs(package: str) -> set[str]:
        return {
            re.split(r'[ ;<>=~!\[]', requirement)[0].lower()
            for requirement in requires(package) or []
            if 'extra ==' not in requirement
        }

    pinned = set(release_requirements())

    assert needs('markdown-it-py') | needs('mdurl') <= pinned
