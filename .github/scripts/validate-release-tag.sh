#!/usr/bin/env bash
# Fails unless TAG is a release tag that matches the code being released.
#
#   .github/scripts/validate-release-tag.sh TAG [APP_PY]
#
# A release tag is exactly vMAJOR.MINOR.PATCH: SemVer without prerelease or
# build metadata, no leading zeros. It must equal "v" + __version__ in app.py,
# which is what teamspeak_exporter_build_info reports, and CHANGELOG.md next to
# app.py must have a section for that version. See docs/releasing.md.

set -euo pipefail

TAG="${1:?usage: validate-release-tag.sh TAG [APP_PY]}"
APP_PY="${2:-app.py}"
CHANGELOG="$(dirname "$APP_PY")/CHANGELOG.md"

fail() {
  echo "::error::$1"
  exit 1
}

if [[ ! "$TAG" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
  fail "'$TAG' is not a release tag; expected vMAJOR.MINOR.PATCH, e.g. v1.2.3"
fi

VERSION="$(sed -n "s/^__version__ = '\(.*\)'\$/\1/p" "$APP_PY")"
if [[ -z "$VERSION" ]]; then
  fail "no __version__ = '...' line in $APP_PY"
fi
if [[ "$TAG" != "v$VERSION" ]]; then
  fail "tag $TAG does not match __version__ '$VERSION' in $APP_PY; bump it before tagging"
fi

if ! grep -qF "## [$VERSION]" "$CHANGELOG"; then
  fail "$CHANGELOG has no '## [$VERSION]' section"
fi

echo "release tag $TAG matches __version__ and CHANGELOG.md"
