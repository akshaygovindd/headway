#!/usr/bin/env bash
# Invoke a Lambda container image through the Runtime Interface Emulator under Lambda-like
# constraints: read-only root filesystem, writable /tmp only, unprivileged user, HOME unset,
# working dir /var/task, no /dev/shm (--ipc=none, as in real Lambda). Local fixtures only: no AWS access, no network use by the handlers.
#
#   smoke_image.sh transform|curate|ingest
set -euo pipefail

kind=${1:?usage: smoke_image.sh transform|curate|ingest}
root=$(cd "$(dirname "$0")/.." && pwd)
smoke="$root/.local/smoke"
port=$((9200 + RANDOM % 500))
name="headway-smoke-$kind-$$"
moto_pid=
trap 'docker rm -f "$name" >/dev/null 2>&1 || true; [ -z "$moto_pid" ] || kill "$moto_pid" 2>/dev/null || true' EXIT

# invoke <image> <handler> <expected-python-expression-on-result> [docker args...]
invoke() {
    local image=$1 handler=$2 check=$3
    shift 3
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker run -d --name "$name" --platform linux/arm64 --read-only --ipc=none --tmpfs /tmp:exec \
        --user 993:990 -p "$port:8080" \
        -e AWS_DEFAULT_REGION=us-east-1 -e AWS_ACCESS_KEY_ID=smoke -e AWS_SECRET_ACCESS_KEY=smoke \
        "$@" --entrypoint env "$image" -u HOME /lambda-entrypoint.sh "$handler" >/dev/null
    for _ in $(seq 1 30); do
        curl -s -o /dev/null "localhost:$port/" && break
        sleep 0.5
    done
    local out
    out=$(curl -s -XPOST "localhost:$port/2015-03-31/functions/function/invocations" -d '{}')
    if ! SMOKE_OUT="$out" python3 -c "
import json, os, sys
r = json.loads(os.environ['SMOKE_OUT'])
sys.exit(0 if 'errorMessage' not in r and ($check) else 1)"; then
        echo "FAIL $handler: $out" >&2
        docker logs "$name" 2>&1 | tail -40 >&2
        exit 1
    fi
    echo "ok   $handler: $out" | cut -c1-300
    docker rm -f "$name" >/dev/null
}

case "$kind" in
transform)
    rm -rf "$smoke/curated"
    (cd "$root" && uv run headway replay --source samples/boston --target "$smoke/curated" \
        --city boston >/dev/null)
    invoke headway-transform:local headway_transform.handler.lambda_handler \
        "r['smoke'] and r['extension_dir_writable'] and r['tables_exported'] >= 8 and r['rows_exported'] > 0" \
        -v "$smoke/curated:/fixture/curated:ro" \
        -e CITY_ID=boston -e LOOKBACK_DAYS=36500 \
        -e HEADWAY_SMOKE_CURATED_ROOT=/fixture/curated -e HEADWAY_SMOKE_GOLD_DIR=/tmp/gold

    # Same image, but export_gold writes to s3:// on a moto server (host process, reached via
    # host.docker.internal) so the httpfs/secret/COPY-to-S3 path runs under Lambda constraints.
    moto_port=$((9800 + RANDOM % 150))
    (cd "$root" && uv run python -m moto.server -H 0.0.0.0 -p "$moto_port" >/dev/null 2>&1) &
    moto_pid=$!
    for _ in $(seq 1 40); do
        curl -s -o /dev/null "localhost:$moto_port/" && break
        sleep 0.5
    done
    curl -s -o /dev/null -XPUT "localhost:$moto_port/smoke-public" \
        -H "Authorization: AWS4-HMAC-SHA256 Credential=smoke/20260101/us-east-1/s3/aws4_request"
    endpoint="host.docker.internal:$moto_port"
    invoke headway-transform:local headway_transform.handler.lambda_handler \
        "r['smoke'] and r['extension_dir_writable'] and r['tables_exported'] >= 8 and r['rows_exported'] > 0" \
        --add-host=host.docker.internal:host-gateway \
        -v "$smoke/curated:/fixture/curated:ro" \
        -e CITY_ID=boston -e LOOKBACK_DAYS=36500 \
        -e HEADWAY_SMOKE_CURATED_ROOT=/fixture/curated \
        -e HEADWAY_SMOKE_GOLD_DIR=s3://smoke-public/public/boston/gold \
        -e HEADWAY_DBT_TARGET=s3_test -e HEADWAY_S3_ENDPOINT="$endpoint" \
        -e AWS_ENDPOINT_URL="http://$endpoint"
    ;;
curate)
    rm -rf "$smoke/store" "$smoke/gtfs.zip"
    mkdir -p "$smoke/store"
    cp -R "$root/samples/boston/raw" "$root/samples/boston/manifests" "$smoke/store/"
    chmod -R a+rwX "$smoke/store"
    (cd "$root/samples/boston/gtfs" && zip -qr "$smoke/gtfs.zip" .)
    common=(-v "$smoke/store:/fixture/store" -v "$smoke/gtfs.zip:/fixture/gtfs.zip:ro"
        -e CITY_ID=boston -e HEADWAY_STORAGE_DIR=/fixture/store
        -e HEADWAY_GTFS_ZIP=/fixture/gtfs.zip)
    invoke headway-curate:local headway.curate.handler.static_handler \
        "not r['skipped'] and r['rows']['routes'] > 0" "${common[@]}"
    invoke headway-curate:local headway.curate.handler.curate_handler \
        "r['task'] == 'curate' and r['batches'] > 0" "${common[@]}"
    ;;
ingest)
    # The ingest function is a zip, not an image: run it on the Lambda Python base image with the
    # package built by `make lambda-zip` mounted at /var/task, under the function's 128 MB limit.
    # A time-compressed 4-poll cycle with fake feeds; fails when peak RSS exceeds 110 MB.
    rm -rf "$smoke/ingest" && mkdir -p "$smoke/ingest" && chmod a+rwx "$smoke/ingest"
    docker run --rm --name "$name" --platform linux/arm64 --memory=128m --memory-swap=128m \
        --read-only --ipc=none --tmpfs /tmp:exec --user 993:990 \
        -v "$root/build/lambda:/var/task:ro" -v "$root/scripts/smoke_ingest.py:/smoke/smoke_ingest.py:ro" \
        -v "$smoke/ingest:/out" \
        -e PYTHONPATH=/var/task -e HEADWAY_CONFIG_DIR=/var/task/config/cities -e HOME=/tmp \
        -e AWS_DEFAULT_REGION=us-east-1 -e AWS_ACCESS_KEY_ID=smoke -e AWS_SECRET_ACCESS_KEY=smoke \
        --entrypoint /var/lang/bin/python3.12 public.ecr.aws/lambda/python:3.12 \
        /smoke/smoke_ingest.py --out /out | tee "$smoke/ingest/result.json"
    ;;
*) echo "unknown image: $kind" >&2; exit 2 ;;
esac
