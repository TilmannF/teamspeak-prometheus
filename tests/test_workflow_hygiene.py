"""Every workflow and the dependency updates: no persisted tokens, the
default branch, version ranges kept.
"""

from __future__ import annotations

import yaml

from tests.release_support import ROOT, all_checkouts


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


def test_workflows_trigger_on_the_default_branch_only():
    # A leftover branch name here silently stops CI on pushes to main.
    for path in sorted((ROOT / '.github' / 'workflows').glob('*.yml')):
        triggers = yaml.safe_load(path.read_text())[True]  # YAML 1.1: "on" is True
        for event in ('push', 'pull_request'):
            branches = ((triggers or {}).get(event) or {}).get('branches')
            if branches is not None:
                assert branches == ['main'], f'{path.name}: {event} on {branches}'


def test_no_file_refers_to_the_old_default_branch():
    stale = [
        path.relative_to(ROOT).as_posix()
        for path in [
            *ROOT.glob('*.md'),
            *(ROOT / 'docs').glob('*.md'),
            *(ROOT / '.github').rglob('*.yml'),
        ]
        if path.name != 'CODE_OF_CONDUCT.md'
        and (
            'blob/master' in path.read_text()
            or '[master]' in path.read_text()
            or '`master`' in path.read_text()
        )
    ]

    assert stale == []


def test_dependabot_moves_python_ranges_only_when_it_must():
    config = yaml.safe_load((ROOT / '.github' / 'dependabot.yml').read_text())
    (pip,) = [u for u in config['updates'] if u['package-ecosystem'] == 'pip']

    assert pip['versioning-strategy'] == 'increase-if-necessary'


def test_the_runtime_floor_is_still_the_tested_one():
    # requirements.txt documents the oldest verified prometheus_client; a
    # floor raised without re-verifying would make that claim stale
    requirements = (ROOT / 'requirements.txt').read_text()

    assert 'prometheus_client>=0.7.1,<1.0' in requirements
    assert '0.7.1' in requirements.split('prometheus_client')[0]
