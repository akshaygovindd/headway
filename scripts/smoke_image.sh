#!/usr/bin/env bash
# Invoke a Lambda container image through the Runtime Interface Emulator under Lambda-like
# constraints: read-only root filesystem, writable /tmp only, unprivileged user, HOME unset,
# working dir /var/task, no /dev/shm (--ipc=none, as in real Lambda). Local fixtures and a local moto
# S3 only: no AWS access, no network use by the handlers.
#
#   smoke_image.sh transform|curate|ingest|train
set -euo pipefail

# Keep the Mac awake for the run. An idle or maintenance sleep mid-run froze it for up to 15 minutes
# (a 135 s smoke took 1007 s, and a request timeout fired on wake).
if [ -z "${SMOKE_AWAKE:-}" ] && command -v caffeinate >/dev/null 2>&1; then
    SMOKE_AWAKE=1 exec caffeinate -i "$0" "$@"
fi

kind=${1:?usage: smoke_image.sh transform|curate|ingest|train}
root=$(cd "$(dirname "$0")/.." && pwd)
smoke="$root/.local/smoke"
mkdir -p "$smoke"
name="headway-smoke-$kind-$$"
moto_pid=
trap 'docker rm -f "$name" >/dev/null 2>&1 || true; rm -f "$smoke"/moto-$$.log; [ -z "$moto_pid" ] || kill "$moto_pid" 2>/dev/null || true' EXIT

# A port the OS has just confirmed free (bind to 0), not a random pick from a small range: a pick
# can land on a port an earlier run left a server on.
free_port() {
    python3 -c 'import socket; s = socket.socket(); s.bind(("", 0)); print(s.getsockname()[1])'
}
port=$(free_port)

# Preflight: the images are 1 to 2 GB and the runs write more; fail early and say why rather than
# stall or die half way. Needs 6 GB free on the host disk and 3 GB for the Docker VM.
free_kb=$(df -Pk "$root" | awk 'NR==2 {print $4}')
if [ "$free_kb" -lt $((6 * 1024 * 1024)) ]; then
    echo "FAIL preflight: only $((free_kb / 1024)) MB free on the disk holding $root (need 6 GB)" >&2
    docker system df >&2
    exit 1
fi
docker_mem=$(docker info --format '{{.MemTotal}}')
if [ "$docker_mem" -lt $((3 * 1024 * 1024 * 1024)) ]; then
    echo "FAIL preflight: Docker has $((docker_mem / 1048576)) MB of memory (need 3 GB)" >&2
    exit 1
fi

# start_moto: a fresh moto server on a free port, started as the python process itself (not through
# `uv run`, whose wrapper is what the trap used to kill, leaving the server behind on its port).
# Fails unless the server answers and holds no buckets, so a run can never see another run's state.
start_moto() {
    local py
    py=$(cd "$root" && uv run python -c 'import sys; print(sys.executable)')
    moto_port=$(free_port)
    "$py" -m moto.server -H 0.0.0.0 -p "$moto_port" >"$smoke/moto-$$.log" 2>&1 &
    moto_pid=$!
    local up=
    for _ in $(seq 1 40); do
        kill -0 "$moto_pid" 2>/dev/null || break
        curl -s -o /dev/null --max-time 2 "localhost:$moto_port/" && { up=1; break; }
        sleep 0.5
    done
    if [ -z "$up" ]; then
        echo "FAIL: moto did not start on port $moto_port" >&2
        cat "$smoke/moto-$$.log" >&2
        exit 1
    fi
    if curl -s --max-time 5 -H "Authorization: AWS4-HMAC-SHA256 Credential=smoke/20260101/us-east-1/s3/aws4_request" \
        "localhost:$moto_port/" | grep -q "<Name>"; then
        echo "FAIL: moto on port $moto_port already holds buckets (state left by another run)" >&2
        exit 1
    fi
}

# invoke <image> <handler> <expected-python-expression-on-result> [docker args...]
# EXPECT_LOG (space-separated grep patterns) must each appear in the container log, which is where
# the embedded-metric lines go; it is reset after every invocation.
EXPECT_LOG=
invoke() {
    local image=$1 handler=$2 check=$3
    shift 3
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker run -d --name "$name" --platform linux/arm64 --read-only --ipc=none --tmpfs /tmp:exec \
        --user 993:990 -p "$port:8080" \
        -e AWS_DEFAULT_REGION=us-east-1 -e AWS_ACCESS_KEY_ID=smoke -e AWS_SECRET_ACCESS_KEY=smoke \
        "$@" --entrypoint env "$image" -u HOME /lambda-entrypoint.sh "$handler" >/dev/null
    local up=
    for _ in $(seq 1 60); do
        curl -s -o /dev/null --max-time 2 "localhost:$port/" && { up=1; break; }
        sleep 0.5
    done
    if [ -z "$up" ]; then
        echo "FAIL $handler: the emulator did not answer on port $port" >&2
        docker logs "$name" 2>&1 | tail -40 >&2
        exit 1
    fi
    local out
    out=$(curl -s --max-time 900 -XPOST "localhost:$port/2015-03-31/functions/function/invocations" -d '{}')
    if ! SMOKE_OUT="$out" python3 -c "
import json, os, sys
r = json.loads(os.environ['SMOKE_OUT'])
sys.exit(0 if 'errorMessage' not in r and ($check) else 1)"; then
        echo "FAIL $handler: $out" >&2
        docker logs "$name" 2>&1 | tail -40 >&2
        exit 1
    fi
    local pattern
    for pattern in $EXPECT_LOG; do
        if ! docker logs "$name" 2>&1 | grep -E "^\{\"_aws\".*$pattern" >/dev/null; then
            echo "FAIL $handler: no embedded-metric line for $pattern in the container log" >&2
            docker logs "$name" 2>&1 | tail -40 >&2
            exit 1
        fi
    done
    EXPECT_LOG=
    echo "ok   $handler: $out" | cut -c1-300
    docker rm -f "$name" >/dev/null
}

case "$kind" in
transform)
    rm -rf "$smoke/curated"
    (cd "$root" && uv run headway replay --source samples/boston --target "$smoke/curated" \
        --city boston >/dev/null)
    invoke headway-transform:local headway_transform.handler.lambda_handler \
        "r['smoke'] and r['extension_dir_writable'] and r['tables_exported'] >= 8 and r['rows_exported'] > 0 and not r['r2_enabled'] and r['tests_failed'] == 0" \
        -v "$smoke/curated:/fixture/curated:ro" \
        -e CITY_ID=boston -e LOOKBACK_DAYS=36500 \
        -e HEADWAY_SMOKE_CURATED_ROOT=/fixture/curated -e HEADWAY_SMOKE_GOLD_DIR=/tmp/gold

    # Release 2 switched on: same gold, plus the r2 models and their tests.
    invoke headway-transform:local headway_transform.handler.lambda_handler \
        "r['smoke'] and r['r2_enabled'] and r['tables_exported'] >= 8 and r['tests_failed'] == 0" \
        -v "$smoke/curated:/fixture/curated:ro" \
        -e CITY_ID=boston -e LOOKBACK_DAYS=36500 -e HEADWAY_ENABLE_R2=true -e HEADWAY_ENABLE_R3=true \
        -e HEADWAY_SMOKE_CURATED_ROOT=/fixture/curated -e HEADWAY_SMOKE_GOLD_DIR=/tmp/gold

    # Same image, but export_gold writes to s3:// on a moto server (host process, reached via
    # host.docker.internal) so the httpfs/secret/COPY-to-S3 path runs under Lambda constraints.
    start_moto
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

    # Release 2 end to end on S3 (moto), under the same constraints: run state append and load,
    # the SLO models built over that state, history write and its immutability (a second write
    # changes nothing), the SLO models read back from that history, and the export of the whole
    # R2 allowlist, and with Release 3 on, the closed-day forecast tables written once to the raw bucket
    # (and not again). Also under the same constraints: the run's embedded-metric lines in the
    # container log, Elementary's history persisted and read back, and the edr report rendered and
    # uploaded (no network, read-only root, /tmp only).
    curl -s -o /dev/null -XPUT "localhost:$moto_port/smoke-raw" \
        -H "Authorization: AWS4-HMAC-SHA256 Credential=smoke/20260101/us-east-1/s3/aws4_request"
    EXPECT_LOG="GoldAgeMinutes MissedRunsLastHour ArrivalCoveragePct"
    invoke headway-transform:local headway_transform.handler.lambda_handler \
        "r['smoke'] and r['r2_enabled'] and r['tests_failed'] == 0 and r['r2_tables_exported'] == 16 \
         and r['history_written'] > 0 and r['history_rewritten'] == 0 \
         and r['history_existing_on_rewrite'] == r['history_written'] and r['history_objects_unchanged'] \
         and r['state_rows_loaded'] == 1 and r['slo_daily_rows'] > 0 and r['slo_rollup_rows'] > 0 \
         and r['transform_success_events'] == [1, 1] and r['slo_rows_read_back_from_history'] \
         and r['emf_metrics'] == ['ArrivalCoveragePct', 'GoldAgeMinutes', 'MissedRunsLastHour'] \
         and r['elementary_persisted']['data_monitoring_metrics'] > 0 \
         and r['elementary_restored'] == r['elementary_persisted'] \
         and r['report_key'] == 'public/boston/observability/index.html' and r['report_is_html'] \
         and r['report_bytes'] > 100000 and r['report_content_type'].startswith('text/html') \
         and r['forecast_state_written'] > 0 and r['forecast_state_rewritten'] == 0 \
         and r['forecast_state_existing_on_rewrite'] == r['forecast_state_written'] \
         and r['forecast_serving_status'] == 'baselines_only' and r['forecast_model_rows'] == 0 \
         and r['forecast_baseline_rows'] > 0 and r['forecast_model_version'] is None \
         and r['forecast_cache_files'] == [] and r['r3_tables_exported'] == 4 \
         and not r['r3_backtest_exported'] and r['forecast_reports'] == \
             {'raw_forecast_backtest': 0, 'raw_forecast_gate_status': 0} \
         and r['incident_seeded'] == 2 and r['incident_summary_status'] == 'ok' \
         and r['incident_summaries_template'] == 2 and r['incident_summaries_llm'] == 0 \
         and r['incident_llm_calls'] == 0 and r['incident_llm_rejections'] == 0" \
        --add-host=host.docker.internal:host-gateway \
        -v "$smoke/curated:/fixture/curated:ro" \
        -e CITY_ID=boston -e LOOKBACK_DAYS=36500 -e HEADWAY_ENABLE_R2=true -e HEADWAY_ENABLE_R3=true \
        -e RAW_BUCKET=smoke-raw -e HEADWAY_SMOKE_SEED_INCIDENTS=1 \
        -e HEADWAY_SMOKE_CURATED_ROOT=/fixture/curated \
        -e HEADWAY_SMOKE_GOLD_DIR=s3://smoke-public/public/boston/gold \
        -e HEADWAY_DBT_TARGET=s3_test -e HEADWAY_S3_ENDPOINT="$endpoint" \
        -e AWS_ENDPOINT_URL="http://$endpoint"

    # Release 3 incident summaries run in both transforms here: the first (no key in SSM) writes the
    # template for two seeded incidents; the second, with a fake provider injected, makes two calls,
    # one answer is rejected by the number check (template, counted) and one passes.
    # Release 3 serving with a registered model (model v1 for Red, Orange and Blue under a passed
    # gate, seeded with the training code) under the same constraints: the model is downloaded to
    # /tmp once, LightGBM runs on the read-only image, only the approved lines past the gate get a
    # model forecast, the reports reach the warehouse and the backtest is exported (a line passed).
    (cd "$root" && uv run --project ml python scripts/smoke_serve_seed.py \
        --endpoint "http://localhost:$moto_port" --bucket smoke-raw-model)
    # a fresh public bucket: this run starts without the history the runs above wrote
    curl -s -o /dev/null -XPUT "localhost:$moto_port/smoke-public-model" \
        -H "Authorization: AWS4-HMAC-SHA256 Credential=smoke/20260101/us-east-1/s3/aws4_request"
    invoke headway-transform:local headway_transform.handler.lambda_handler \
        "r['smoke'] and r['tests_failed'] == 0 and r['forecast_serving_status'] == 'model' \
         and r['forecast_model_rows'] > 0 and r['forecast_model_version'] == 1 \
         and r['forecast_model_from_cache'] is False and r['forecast_scored_trip_stops'] > 0 \
         and r['forecast_model_error'] is None \
         and r['forecast_baseline_rows'] > r['forecast_model_rows'] \
         and {'model.txt', 'lines.json', 'features.json', 'meta.json'} <= set(r['forecast_cache_files']) \
         and r['forecast_reports']['raw_forecast_gate_status'] == 3 \
         and r['forecast_reports']['raw_forecast_backtest'] > 0 \
         and r['r3_tables_exported'] == 5 and r['r3_backtest_exported'] \
         and r['incident_seeded'] == 2 and r['incident_summary_status'] == 'ok' \
         and r['incident_llm_calls'] == 2 and r['incident_llm_rejections'] == 1 \
         and r['incident_summaries_llm'] == 1 and r['incident_summaries_template'] == 1 \
         and 'analyst_examples_error' not in r and r['analyst_examples'] > 0 \
         and r['analyst_examples_read_back'] == r['analyst_examples']" \
        --add-host=host.docker.internal:host-gateway \
        -v "$smoke/curated:/fixture/curated:ro" \
        -e CITY_ID=boston -e LOOKBACK_DAYS=36500 -e HEADWAY_ENABLE_R2=true -e HEADWAY_ENABLE_R3=true \
        -e RAW_BUCKET=smoke-raw-model -e HEADWAY_SMOKE_SEED_INCIDENTS=1 -e HEADWAY_SMOKE_FAKE_LLM=1 \
        -e HEADWAY_SMOKE_CURATED_ROOT=/fixture/curated \
        -e HEADWAY_SMOKE_GOLD_DIR=s3://smoke-public-model/public/boston/gold \
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
    # package built by `make lambda-zip` mounted at /var/task, under the function's 256 MB limit.
    # A time-compressed 4-poll cycle with fake feeds; fails when peak RSS exceeds 50% of it (the smoke leaves out runtime overhead).
    rm -rf "$smoke/ingest" && mkdir -p "$smoke/ingest" && chmod a+rwx "$smoke/ingest"
    for polls in 1 4; do
        docker run --rm --name "$name" --platform linux/arm64 --memory=256m --memory-swap=256m \
            --read-only --ipc=none --tmpfs /tmp:exec --user 993:990 \
            -v "$root/build/lambda:/var/task:ro" -v "$root/scripts/smoke_ingest.py:/smoke/smoke_ingest.py:ro" \
            -v "$smoke/ingest:/out" \
            -e PYTHONPATH=/var/task -e HEADWAY_CONFIG_DIR=/var/task/config/cities -e HOME=/tmp \
            -e AWS_DEFAULT_REGION=us-east-1 -e AWS_ACCESS_KEY_ID=smoke -e AWS_SECRET_ACCESS_KEY=smoke \
            --entrypoint /var/lang/bin/python3.12 public.ecr.aws/lambda/python:3.12 \
            /smoke/smoke_ingest.py --out "/out/polls$polls" --polls "$polls" | tee "$smoke/ingest/result-$polls.json"
    done
    ;;
train)
    # The training image against a moto S3 on the host: synthetic closed-day partitions, then the
    # handler's three paths. Gate not passed: baselines logged, no model, no latest.json. Gate
    # passed: model files and latest.json at v1. A second run on the same bucket downloads the
    # MLflow database from S3 again: two runs in it, model v2.
    start_moto
    (cd "$root" && uv run --project ml python scripts/smoke_train_seed.py \
        --endpoint "http://localhost:$moto_port" --bucket smoke-train-closed --gate-days 5)
    (cd "$root" && uv run --project ml python scripts/smoke_train_seed.py \
        --endpoint "http://localhost:$moto_port" --bucket smoke-train-open --gate-days 21)
    endpoint="host.docker.internal:$moto_port"
    train_env=(--add-host=host.docker.internal:host-gateway -e AWS_ENDPOINT_URL="http://$endpoint")
    invoke headway-train:local headway_ml.train_handler.lambda_handler \
        "r['status'] == 'baselines_only' and r['model_version'] is None and r['mlflow_runs'] == 1 \
         and r['gate_passed_lines'] == [] and sorted(r['gate_reasons']) == ['Blue', 'Red'] \
         and r['model_objects'] == [] and any('no line passed the training gate' in n for n in r['notes'])" \
        "${train_env[@]}" -e RAW_BUCKET=smoke-train-closed
    invoke headway-train:local headway_ml.train_handler.lambda_handler \
        "r['status'] == 'trained' and r['model_version'] == 1 and r['mlflow_runs'] == 1 \
         and r['gate_passed_lines'] == ['Blue', 'Red'] \
         and r['model_objects'] == ['models/forecast/latest.json'] + ['models/forecast/v1/' + n for n in \
             ['features.json', 'lines.json', 'meta.json', 'metrics.json', 'model.txt', 'window.json']]" \
        "${train_env[@]}" -e RAW_BUCKET=smoke-train-open
    invoke headway-train:local headway_ml.train_handler.lambda_handler \
        "r['status'] == 'trained' and r['model_version'] == 2 and r['mlflow_runs'] == 2" \
        "${train_env[@]}" -e RAW_BUCKET=smoke-train-open
    ;;
*) echo "unknown image: $kind" >&2; exit 2 ;;
esac
