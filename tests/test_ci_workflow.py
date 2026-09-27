"""The CI workflow: every pull request's image is scanned, the checks the
`main` ruleset requires keep their names, and nothing in it can write.
"""

from __future__ import annotations

import re

import yaml

from tests.release_support import ROOT, uses, writes

WORKFLOWS = ROOT / '.github' / 'workflows'
PINNED = re.compile(r'^[\w./-]+@[0-9a-f]{40}$')
MATRIX = re.compile(r'\$\{\{ matrix\.([\w-]+) \}\}')

# The status checks ruleset 24071688 requires on main. A job renamed here
# no longer reports under its old name, and every merge waits for it.
REQUIRED_CHECKS = {
    'lint + unit tests (py3.12)',
    'lint + unit tests (py3.13)',
    'lint + unit tests (py3.14)',
    'smoke tests (py3.14)',
    'docker build + container test',
    'dependency review',
    'analyze (python)',
}


def workflow(name: str = 'ci.yml') -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def docker_steps() -> list[dict]:
    return workflow()['jobs']['docker']['steps']


def scan() -> tuple[int, dict]:
    (found,) = [
        (i, step)
        for i, step in enumerate(docker_steps())
        if uses('aquasecurity/trivy-action')(step)
    ]
    return found


def check_names(name: str) -> set[str]:
    """Every status check a pull request gets from workflow ``name``."""

    names = set()
    for key, job in workflow(name)['jobs'].items():
        title = job.get('name', key)
        axes = (job.get('strategy') or {}).get('matrix') or {}
        placeholders = set(MATRIX.findall(title))
        if not placeholders:
            names.add(title)
        for axis in placeholders:
            names |= {title.replace(f'${{{{ matrix.{axis} }}}}', v) for v in axes[axis]}
    return names


def test_the_required_checks_still_exist():
    assert check_names('ci.yml') | check_names('codeql.yml') >= REQUIRED_CHECKS


def test_the_image_is_scanned_after_it_is_built_and_tested():
    runs = [step.get('run') for step in docker_steps()]
    at, _ = scan()

    assert at > runs.index('make docker-test')


def test_the_scan_covers_the_image_the_makefile_builds():
    _, step = scan()
    makefile = (ROOT / 'Makefile').read_text()

    assert f'docker build -t {step["with"]["image-ref"]} .' in makefile


def test_high_and_critical_findings_with_a_fix_fail_the_job():
    _, step = scan()

    assert step['with']['severity'] == 'HIGH,CRITICAL'
    assert step['with']['ignore-unfixed'] is True
    assert step['with']['exit-code'] == '1'
    assert 'continue-on-error' not in step
    assert 'if' not in step


def test_the_trivy_version_is_the_pinned_actions_own():
    # The action's default at the pinned commit; a separate version pin would
    # stay behind when Dependabot moves the action.
    _, step = scan()

    assert 'version' not in step['with']


def test_every_action_is_pinned_to_a_commit():
    unpinned = [
        step['uses']
        for job in workflow()['jobs'].values()
        for step in job['steps']
        if 'uses' in step and not PINNED.match(step['uses'])
    ]

    assert unpinned == []


def test_nothing_in_ci_can_write():
    assert workflow()['permissions'] == {'contents': 'read'}
    assert not any(
        writes(job.get('permissions')) for job in workflow()['jobs'].values()
    )
