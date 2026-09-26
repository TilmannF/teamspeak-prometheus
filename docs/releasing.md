# Releasing

## What the version number means

Versioning is [SemVer](https://semver.org/). The thing SemVer is measured
against is the metric surface — the names in `METRICS_NAMES`, the
`teamspeak_` prefix, and the `virtualserver_name` label — plus the flags,
environment variables, and ports documented in `README.md`. See `AGENTS.md`,
"The Metric Contract".

- **Major**: a metric was renamed, removed, re-prefixed, or re-labelled, or a
  flag/environment variable/default port changed. Requires the human review
  checkpoint in `AGENTS.md` before it happens at all.
- **Minor**: a new metric was added (TeamSpeak's `serverinfo` already returns
  it — see `AGENTS.md`), or new optional configuration was added.
- **Patch**: a bug fix, dependency bump, or anything with no effect on the
  contract above.

## Cutting a release

1. Update `CHANGELOG.md`: move the pending changes into a new `## [X.Y.Z]`
   section, dated, with a link reference at the bottom of the file. Set
   `__version__` in `teamspeak_prometheus/__init__.py` to `X.Y.Z` — it is what
   `teamspeak_exporter_build_info` reports.
2. Commit that on a branch, get it reviewed and merged to `main` like any
   other change.
3. From `main`, tag and push. Release tags are exactly `vMAJOR.MINOR.PATCH`
   (no prerelease or build suffix, no leading zeros), equal to `v` +
   `__version__`, with a matching `## [X.Y.Z]` section in `CHANGELOG.md`.
   `.github/scripts/validate-release-tag.sh` checks all three first thing in
   the release workflow and fails before anything is published. Run it
   locally before pushing a tag:

   ```bash
   .github/scripts/validate-release-tag.sh vX.Y.Z
   ```

   Then:

   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

4. `.github/workflows/release.yml` does the rest:
   - builds the multi-arch image (amd64, arm64) once and pushes it to GHCR and
     Docker Hub — Docker Hub if the `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`
     repository secrets are set, which they are;
   - attests build provenance and attaches an SBOM;
   - syncs the README to the Docker Hub description, relative links made
     absolute;
   - creates the GitHub Release with the version's `CHANGELOG.md` section as
     its notes. `.github/scripts/changelog_section.py` extracts it and refuses
     a section with a link that needs a definition from elsewhere in the
     changelog — it would render as plain brackets — or a code block left
     open, which would swallow the rest. It parses the Markdown with
     `markdown-it-py`, a CommonMark parser, rather than guessing with
     patterns: the section is parsed alone and with the changelog's other
     definitions, and a link only the second parse has is a broken one. It
     runs before anything is published.

     The parser is release tooling only, never a runtime dependency. The
     workflow installs it into its own environment from
     `requirements-release.txt`: exact versions, wheels only, every file
     checked against its hash, nothing resolved beyond the list. Dependabot
     updates the pins and hashes; `tests/test_release_workflow.py` fails if a
     pin loses its hash or the parser gains a dependency the list lacks.

   Run `.venv/bin/python .github/scripts/changelog_section.py vX.Y.Z` locally
   (after `make setup`, which installs the parser) to preview the notes.

   The Docker Hub token is a personal access token (Read & Write) created at
   hub.docker.com → Account settings → Personal access tokens.

To prove the build without publishing anything, start the workflow manually
(`workflow_dispatch`, "Run workflow" in the Actions tab). A manual run is a
separate `build` job with read-only permissions: it never logs in to a
registry, pushes, attests, or creates a release, and never holds a write
token. Publishing happens only in the `release` job, for a pushed tag that
passed the check in step 3; only that job has write permissions.

To retry a release that failed after the tag was pushed, re-run the original
tag-push run ("Re-run jobs"). That run is a tag push again, so the tag is
validated again. `tests/test_release_workflow.py` and
`tests/test_workflow_hygiene.py` fail if any publishing step could run without
a validated tag push, if any job other than `release` could write, or if any
workflow's checkout leaves the Git token in `.git/config`.
