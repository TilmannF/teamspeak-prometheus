"""The Docker Hub page workflow: README.md from main, and nothing else.

It holds the Docker Hub token and runs on a push to main or by hand, so the
rules are about what it can be made to do: which branch's README it sends,
which permissions it has, and that it publishes nothing but the page.
"""

from __future__ import annotations

import re

import yaml

from tests.release_support import ROOT, writes

WORKFLOW = ROOT / '.github' / 'workflows' / 'dockerhub-description.yml'
PINNED = re.compile(r'^[\w./-]+@[0-9a-f]{40}$')


def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def job() -> dict:
    (only,) = workflow()['jobs'].values()
    return only


def step_using(action: str) -> dict:
    (found,) = [s for s in job()['steps'] if s.get('uses', '').startswith(action + '@')]
    return found


def test_it_runs_on_a_readme_change_on_main_or_by_hand():
    triggers = workflow()[True]  # YAML 1.1 reads the key "on" as True

    assert set(triggers) == {'push', 'workflow_dispatch'}
    assert triggers['push']['branches'] == ['main']
    assert 'README.md' in triggers['push']['paths']
    assert not (triggers['workflow_dispatch'] or {}).get('inputs')


def test_only_mains_readme_becomes_the_page():
    # A manual run can be started on any branch
    assert job()['if'] == "github.ref == 'refs/heads/main'"


def test_it_writes_nothing_on_github():
    assert workflow()['permissions'] == {'contents': 'read'}
    assert not writes(job().get('permissions'))


def test_it_does_nothing_but_sync_the_page():
    actions = {s['uses'].split('@')[0] for s in job()['steps'] if 'uses' in s}

    assert actions == {'actions/checkout', 'peter-evans/dockerhub-description'}
    assert all(
        PINNED.match(s['uses'].split()[0]) for s in job()['steps'] if 'uses' in s
    )


def test_the_page_is_the_readme_with_working_links():
    sync = step_using('peter-evans/dockerhub-description')

    assert sync['if'] == "steps.dockerhub.outputs.enabled == 'true'"
    assert sync['with']['repository'] == 'tilmannf/teamspeak-prometheus'
    assert sync['with']['enable-url-completion'] is True
    assert 'readme-filepath' not in sync['with']  # the default: ./README.md


def test_the_token_reaches_only_the_check_and_the_sync():
    holders = [
        s.get('name') or s['uses'].split('@')[0]
        for s in job()['steps']
        if 'secrets.DOCKERHUB_TOKEN' in str(s)
    ]

    assert holders == [
        'Check Docker Hub credentials',
        'peter-evans/dockerhub-description',
    ]


def test_the_docs_name_the_scope_docker_hub_requires():
    # Read & Write gets "Forbidden" on the description (v1.0.0)
    releasing = ' '.join((ROOT / 'docs' / 'releasing.md').read_text().split())

    assert 'Read, Write, Delete' in releasing
    assert 'Read & Write)' not in releasing
