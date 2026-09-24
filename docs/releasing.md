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
3. From `master`, tag and push:

   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

4. `.github/workflows/release.yml` does the rest: builds and pushes the
   multi-arch image to GHCR (and to Docker Hub, if `DOCKERHUB_USERNAME` and
   `DOCKERHUB_TOKEN` are configured as repository secrets), attests build
   provenance, and creates a GitHub Release with generated notes.

To prove the build without publishing anything, run the same workflow via
`workflow_dispatch` with `dry_run: true`.
