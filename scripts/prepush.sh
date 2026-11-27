#!/usr/bin/env bash
# Pre-push check: `make check` and `make e2e` the way GitHub CI runs them.
#
# It exports the committed tree (a clone of HEAD) into a temporary directory and runs both there
# with a fresh environment (UV_PROJECT_ENVIRONMENT=.local/ci-venv, uv sync --frozen), so nothing
# that exists only on this machine can make it pass: uncommitted or ignored files, a stale venv or
# dbt_packages, the checkout's absolute path. Commit first; uncommitted changes are not tested.
#
#   scripts/prepush.sh              # make check, then make e2e
#   scripts/prepush.sh check        # only one of them
#   scripts/prepush.sh e2e
#   ln -s ../../scripts/prepush.sh .git/hooks/pre-push   # run it on every push
set -euo pipefail

root="$(git rev-parse --show-toplevel)"
targets=("${@:-check e2e}")
# shellcheck disable=SC2206
targets=(${targets[@]})

if [ -n "$(git -C "$root" status --porcelain --untracked-files=no)" ]; then
  echo "prepush: uncommitted changes are not tested (only HEAD is); commit or stash them" >&2
fi

work="$(mktemp -d "${TMPDIR:-/tmp}/headway-prepush.XXXXXX")"
trap 'rm -rf "$work"' EXIT
git clone -q --no-hardlinks "$root" "$work/repo"   # a real clone: ruff and dbt honour .gitignore
cd "$work/repo"

export UV_PROJECT_ENVIRONMENT=.local/ci-venv
uv sync --frozen
for target in "${targets[@]}"; do
  echo "prepush: make $target ($(git -C "$root" rev-parse --short HEAD), clean checkout)"
  make "$target"
done
echo "prepush: ok (${targets[*]})"
