#!/usr/bin/env bash
#
# Coordinate a version bump and create the release tag.
#
# The release build keys off a v<version> tag (see .github/workflows/release.yml),
# and the AppImage filename comes from that tag while the app reports the version
# in pyproject.toml. This script keeps the two in lockstep and performs the steps
# in the only safe order: bump pyproject -> commit -> annotated tag.
#
# It does NOT push by default -- pushing the tag is what fires the public release
# build, so that stays an explicit, opt-in step (--push).
#
# Usage:
#   scripts/bump-version.sh <patch|minor|major|X.Y.Z> [--push] [--dry-run]
#
# Examples:
#   scripts/bump-version.sh patch            # 0.1.0 -> 0.1.1, commit + tag locally
#   scripts/bump-version.sh 0.2.0            # set an exact version
#   scripts/bump-version.sh minor --push     # bump, commit, tag, and push (triggers release)
#   scripts/bump-version.sh patch --dry-run  # show what would happen, change nothing
set -euo pipefail

RELEASE_BRANCH="main"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

usage() {
    sed -n '/^# Usage:/,/^set -euo/p' "$0" | sed '$d;s/^# \{0,1\}//'
}
die() {
    echo "error: $*" >&2
    exit 1
}

# --- parse args ---------------------------------------------------------------
BUMP=""
PUSH=0
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --push) PUSH=1 ;;
        --dry-run) DRY_RUN=1 ;;
        -h | --help)
            usage
            exit 0
            ;;
        -*) die "unknown option: $arg (see --help)" ;;
        *)
            [ -z "$BUMP" ] || die "unexpected extra argument: $arg"
            BUMP="$arg"
            ;;
    esac
done
[ -n "$BUMP" ] || {
    usage
    exit 2
}

# In a dry run, precondition failures are reported but do not abort, so the full
# plan is still printed. Real runs treat the same conditions as hard errors.
fail() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  would abort: $*" >&2
    else
        die "$*"
    fi
}

# --- read current version -----------------------------------------------------
CURRENT="$(sed -nE 's/^version = "(.*)"/\1/p' pyproject.toml | head -1)"
[ -n "$CURRENT" ] || die "could not read current version from pyproject.toml"

# --- resolve target version ---------------------------------------------------
case "$BUMP" in
    major | minor | patch)
        [[ "$CURRENT" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]] \
            || die "current version '$CURRENT' is not X.Y.Z; pass an explicit version instead of '$BUMP'"
        MAJOR=${BASH_REMATCH[1]}
        MINOR=${BASH_REMATCH[2]}
        PATCH=${BASH_REMATCH[3]}
        case "$BUMP" in
            major)
                MAJOR=$((MAJOR + 1))
                MINOR=0
                PATCH=0
                ;;
            minor)
                MINOR=$((MINOR + 1))
                PATCH=0
                ;;
            patch) PATCH=$((PATCH + 1)) ;;
        esac
        NEW="${MAJOR}.${MINOR}.${PATCH}"
        ;;
    *)
        [[ "$BUMP" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]] \
            || die "'$BUMP' is not a bump keyword (patch|minor|major) or a valid X.Y.Z version"
        NEW="$BUMP"
        ;;
esac

TAG="v${NEW}"
[ "$NEW" != "$CURRENT" ] || die "version is already $CURRENT -- nothing to bump"

# --- preconditions ------------------------------------------------------------
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[ "$BRANCH" = "$RELEASE_BRANCH" ] \
    || fail "on branch '$BRANCH'; releases are cut from '$RELEASE_BRANCH' -- switch to it first"
git diff --quiet && git diff --cached --quiet \
    || fail "working tree has uncommitted changes; commit or stash them before bumping"
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
    fail "tag $TAG already exists"
fi

echo "==> Release bump: $CURRENT -> $NEW  (tag $TAG)"

if [ "$DRY_RUN" -eq 1 ]; then
    echo "  [dry run] would set pyproject.toml version to $NEW"
    echo "  [dry run] would commit: chore: release $TAG"
    echo "  [dry run] would create annotated tag $TAG"
    [ "$PUSH" -eq 1 ] && echo "  [dry run] would push $RELEASE_BRANCH and $TAG"
    exit 0
fi

# --- apply --------------------------------------------------------------------
# Anchored at line start so ruff's `target-version` and friends are untouched;
# only the single [project] `version = "..."` line matches.
sed -i -E "s/^version = \".*\"/version = \"$NEW\"/" pyproject.toml
grep -qE "^version = \"$NEW\"$" pyproject.toml || die "failed to update version in pyproject.toml"

git add pyproject.toml
git commit -q -m "chore: release $TAG"
git tag -a "$TAG" -m "Release $TAG"
echo "==> Committed version bump and created annotated tag $TAG"

# --- push or hand off ---------------------------------------------------------
if [ "$PUSH" -eq 1 ]; then
    echo "==> Pushing $RELEASE_BRANCH and $TAG (this triggers the release build)"
    git push origin "$RELEASE_BRANCH"
    git push origin "$TAG"
    echo "==> Done. Watch the Release workflow on GitHub Actions."
else
    cat <<EOF
==> Not pushed. To trigger the release build:
      git push origin $RELEASE_BRANCH && git push origin $TAG
    To undo this bump locally:
      git tag -d $TAG && git reset --hard HEAD~1
EOF
fi
