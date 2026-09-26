"""Shared helpers of the release tests: the workflow file, parsed, and the
release-notes script, imported."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent


SCRIPT = ROOT / '.github' / 'scripts' / 'validate-release-tag.sh'


SEMVER = re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$')


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


WRITE_PERMISSIONS = {'contents', 'packages', 'id-token', 'attestations'}


def jobs() -> dict[str, dict]:
    return workflow()['jobs']


def writes(permissions: dict | str | None) -> set[str]:
    if permissions in ('write-all',):
        return {'*'}
    if not isinstance(permissions, dict):
        return set()
    return {scope for scope, level in permissions.items() if level == 'write'}


def all_checkouts() -> list[tuple[str, str, dict]]:
    found = []
    for path in sorted((ROOT / '.github' / 'workflows').glob('*.yml')):
        for name, job in yaml.safe_load(path.read_text())['jobs'].items():
            for step in job.get('steps', []):
                if uses('actions/checkout')(step):
                    found.append((path.name, name, step))
    return found


NOTES_SCRIPT = ROOT / '.github' / 'scripts' / 'changelog_section.py'

_spec = importlib.util.spec_from_file_location('changelog_section', NOTES_SCRIPT)
notes_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(notes_module)


def changelog(*sections: str, links: str = '') -> str:
    return '# Changelog\n\nIntro.\n\n' + '\n\n'.join(sections) + '\n\n' + links
