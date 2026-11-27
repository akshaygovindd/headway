# Contributing

## Before you push

```sh
scripts/prepush.sh        # make check, then make e2e (or: make prepush)
scripts/prepush.sh e2e    # one of them
ln -s ../../scripts/prepush.sh .git/hooks/pre-push   # optional: run it on every push
```

It tests the committed tree (`git archive HEAD`) in a temporary directory with a fresh environment
(`UV_PROJECT_ENVIRONMENT=.local/ci-venv uv sync --frozen`), the way `.github/workflows/ci.yml`
does. Commit first; uncommitted changes are not tested. Running `make check` in your working copy
is not enough: it passes on files and paths that exist only on your machine. CI was red for three
pushes (27 Nov 2026) because a fixture held the writer's absolute path and the e2e expected
tables were not regenerated after a dbt change; both passed or were never run locally.

Also run the parts of CI that `prepush.sh` leaves out when you touch them: `make app-test`, and
`cd web && npm run ci` (with `set -o pipefail` in your shell if you pipe its output).

## Changing a dbt model

`make e2e` compares marts with `tests/e2e/expected/`. When a change moves them, update with
`uv run --project dbt python scripts/e2e.py --update` only after you can explain every changed
column, and say so in the commit message.
