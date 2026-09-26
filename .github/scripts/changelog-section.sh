#!/bin/sh
# Print one version's section of CHANGELOG.md, for use as GitHub release notes.
#
#   .github/scripts/changelog-section.sh 1.0.0 [CHANGELOG]
#
# Exits non-zero if the version has no section, or if the extracted text would
# render a broken link: a release body is a standalone document, so a
# reference-style link whose definition lives elsewhere in the changelog
# renders as literal `[text][ref]` once it is lifted out.
#
# Adopted from TilmannF/pa2_exporter's tools/changelog-section.sh, where the
# broken-link check was added after a release shipped with one.
set -eu

version="${1:?usage: changelog-section.sh <version> [CHANGELOG]}"
version="${version#v}"
changelog="${2:-CHANGELOG.md}"

section=$(awk -v p="## [$version]" '
    substr($0, 1, length(p)) == p { found = 1; next }
    found && /^## \[/             { exit }
    found {
        # Hold blank lines and link-reference definitions back until a real
        # line follows. A definition in the middle of a section is part of it;
        # the block at the end of the file belongs to the changelog as a whole
        # and must not be tacked onto the oldest release.
        if ($0 ~ /^\[[^]]+\]: / || $0 ~ /^[[:space:]]*$/) {
            held = held $0 "\n"
            next
        }
        if (started) printf "%s", held   # nothing held before the first line
        held = ""
        started = 1
        print
    }
' "$changelog")

if [ -z "$section" ]; then
    echo "no '## [$version]' section in $changelog" >&2
    exit 1
fi

# ][ref] with no matching definition in what we are about to publish.
orphans=$(printf '%s\n' "$section" | grep -o '\]\[[^]]\+\]' || true)
for orphan in $orphans; do
    ref=$(printf '%s' "$orphan" | sed 's/^\]\[//; s/\]$//')
    if ! printf '%s\n' "$section" | grep -q "^ *\[$ref\]: "; then
        echo "section for $version references [$ref], which is defined outside it" >&2
        echo "use an inline link, or move the definition into the section" >&2
        exit 1
    fi
done

printf '%s\n' "$section"
