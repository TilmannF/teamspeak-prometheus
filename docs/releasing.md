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
   `__version__` in `app.py` to `X.Y.Z` — it is what
   `teamspeak_exporter_build_info` reports.
2. Commit that on a branch, get it reviewed and merged to `master` like any
   other change.
3. From `master`, tag and push. Release tags are exactly `vMAJOR.MINOR.PATCH`
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

4. `.github/workflows/release.yml` does the rest: builds and pushes the
   multi-arch image to GHCR (and to Docker Hub, if `DOCKERHUB_USERNAME` and
   `DOCKERHUB_TOKEN` are configured as repository secrets), attests build
   provenance, and creates a GitHub Release with generated notes.

To prove the build without publishing anything, start the workflow manually
(`workflow_dispatch`, "Run workflow" in the Actions tab). A manual run is a
separate `build` job with read-only permissions: it never logs in to a
registry, pushes, attests, or creates a release, and never holds a write
token. Publishing happens only in the `release` job, for a pushed tag that
passed the check in step 3; only that job has write permissions.

To retry a release that failed after the tag was pushed, re-run the original
tag-push run ("Re-run jobs"). That run is a tag push again, so the tag is
validated again. `tests/test_release.py` fails if any publishing step could
run without a validated tag push, if any job other than `release` could write,
or if any workflow's checkout leaves the Git token in `.git/config`.
