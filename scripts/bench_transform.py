"""Time the transform build + test against ~N days of manifests on an S3-compatible server.

Run from the root environment (`uv run python scripts/bench_transform.py`); the dbt part runs in a
child process in the dbt environment. The sample is replayed once, then replicated as `--days` days
with `--per-day` ingest runs per day (production writes one manifest per minute, ~1,440/day).
`--backfill` first builds the curated manifest/quarantine Parquet tables, as after a deploy.

moto cannot serve this: its server closes every connection, so a few hundred dbt tests re-scanning
thousands of objects exhaust the local ephemeral ports ("Could not connect to server"). The data is
therefore served by a small read-only S3 look-alike (ListObjectsV2, HEAD, ranged GET) with HTTP/1.1
keep-alive and an optional fixed per-request latency, which is what makes S3 scans slow in Lambda.
"""

import argparse
import copy
import functools
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
BUCKET = "bench-raw"
CITY = "boston"
SAMPLE_DAY = "2026-11-22"
CHECK_TABLES = ["agg_line_reliability_hourly", "agg_pipeline_health", "agg_quality_checks_hourly"]
PAGE = 1000


@functools.cache
def _etag(path: Path) -> str:
    return f'"{hashlib.md5(path.read_bytes()).hexdigest()}"'  # noqa: S324


def _iso(path: Path) -> str:
    return datetime.fromtimestamp(int(path.stat().st_mtime), UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class S3Handler(BaseHTTPRequestHandler):
    """GET list / GET object (optionally ranged) / HEAD object over a directory tree."""

    protocol_version = "HTTP/1.1"
    root: Path
    latency: float
    requests = 0

    def log_message(self, *args: Any) -> None:
        pass

    def _send(self, status: int, body: bytes, headers: dict[str, str], head_only: bool) -> None:
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _handle(self, head_only: bool) -> None:
        S3Handler.requests += 1
        time.sleep(self.latency)
        url = urlsplit(self.path)
        path = unquote(url.path).lstrip("/")
        if path.rstrip("/") == BUCKET:
            return self._list(parse_qs(url.query))
        file = self.root / path
        if not path.startswith(BUCKET + "/") or not file.is_file():
            return self._send(404, b"<Error><Code>NoSuchKey</Code></Error>", {}, head_only)
        data = file.read_bytes()
        headers = {
            "Last-Modified": formatdate(int(file.stat().st_mtime), usegmt=True),
            "ETag": _etag(file),
            "Accept-Ranges": "bytes",
        }
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            lo, _, hi = rng[6:].partition("-")
            start = int(lo)
            end = min(int(hi), len(data) - 1) if hi else len(data) - 1
            headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
            return self._send(206, data[start : end + 1], headers, head_only)
        self._send(200, data, headers, head_only)

    def _list(self, query: dict[str, list[str]]) -> None:
        prefix = query.get("prefix", [""])[0]
        after = query.get("continuation-token", [""])[0]
        base = self.root / BUCKET
        keys = sorted(p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file())
        keys = [k for k in keys if k.startswith(prefix) and k > after]
        page, more = keys[:PAGE], len(keys) > PAGE
        items = "".join(
            f"<Contents><Key>{quote(k)}</Key><LastModified>{_iso(base / k)}</LastModified>"
            f"<ETag>{_etag(base / k)}</ETag><Size>{(base / k).stat().st_size}</Size>"
            "<StorageClass>STANDARD</StorageClass></Contents>"
            for k in page
        )
        token = f"<NextContinuationToken>{page[-1]}</NextContinuationToken>" if more else ""
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            f"<Name>{BUCKET}</Name><Prefix>{quote(prefix)}</Prefix><KeyCount>{len(page)}</KeyCount>"
            f"<MaxKeys>{PAGE}</MaxKeys><EncodingType>url</EncodingType>"
            f"<IsTruncated>{'true' if more else 'false'}</IsTruncated>{token}{items}"
            "</ListBucketResult>"
        )
        self._send(200, xml.encode(), {"Content-Type": "application/xml"}, False)

    def do_GET(self) -> None:  # noqa: N802
        self._handle(False)

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle(True)


def serve(root: Path, latency: float) -> tuple[ThreadingHTTPServer, str]:
    handler = type("Bound", (S3Handler,), {"root": root, "latency": latency})
    # The listen backlog is applied at bind time, so it must be a class attribute: the default of
    # 5 refuses connections as soon as the scans open them in bursts.
    server_cls = type("Server", (ThreadingHTTPServer,), {"request_queue_size": 512})
    server = server_cls(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    return server, f"{host}:{port}"


def _manifest_variant(
    sample: list[dict[str, Any]], day: str, hour: int, minute: int, n: int
) -> tuple[str, bytes]:
    """One synthetic ingest run at day/hour:minute, derived from a sample manifest."""
    doc = copy.deepcopy(sample[n % len(sample)])
    stamp = f"{day.replace('-', '')}T{hour:02d}{minute:02d}00Z"
    run_id = f"BENCH{n:021d}"
    doc["run_id"] = run_id
    new = datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00+00:00")
    for e in doc["entries"]:
        old = datetime.fromisoformat(e["scheduled_at"].replace("Z", "+00:00"))
        for field in ("scheduled_at", "fetched_at", "feed_header_ts"):
            if e.get(field):
                value = datetime.fromisoformat(e[field].replace("Z", "+00:00")) + (new - old)
                e[field] = value.astimezone(UTC).isoformat().replace("+00:00", "Z")
        e["run_id"] = run_id
        suffix = hashlib.sha256(f"{n}{e['feed']}".encode()).hexdigest()[:8]
        e["capture_id"] = f"{e['feed']}-{stamp}-{suffix}"
        if e.get("s3_key"):
            e["s3_key"] = (
                f"raw/{CITY}/feed={e['feed']}/dt={day}/hour={hour:02d}/{e['capture_id']}.pb.gz"
            )
    key = f"manifests/{CITY}/dt={day}/hour={hour:02d}/{run_id}.json"
    return key, json.dumps(doc, indent=2, sort_keys=True).encode()


def populate(bucket_dir: Path, replay: Path, days: int, per_day: int) -> int:
    """Write the replicated dataset under bucket_dir; returns the number of manifest objects."""
    today = datetime.now(UTC).date()
    day_list = [(today - timedelta(days=k)).isoformat() for k in range(days)]
    uploads: list[tuple[str, bytes]] = []
    for path in sorted(replay.rglob("*")):
        if not path.is_file():
            continue
        key = path.relative_to(replay).as_posix()
        # Manifests are synthesized below; the replay's own curated copy would double-count them.
        if key.startswith(("raw/", "manifests/")) or "/table=manifests/" in key:
            continue
        if f"dt={SAMPLE_DAY}" in key:
            uploads.extend((key.replace(SAMPLE_DAY, d), path.read_bytes()) for d in day_list)
        else:
            uploads.append((key, path.read_bytes()))
    sample = [
        json.loads(p.read_text()) for p in sorted((replay / "manifests" / CITY).rglob("*.json"))
    ]
    n = 0
    for day in day_list:
        for hour in range(24):
            for i in range(max(1, per_day // 24)):
                uploads.append(_manifest_variant(sample, day, hour, i % 60, n))
                n += 1
    for key, body in uploads:
        target = bucket_dir / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    return n


def checksum(db: str) -> dict[str, str]:
    import duckdb

    con = duckdb.connect(db, read_only=True)
    out = {}
    for table in CHECK_TABLES:
        row = con.execute(
            "select md5(string_agg(cast(t as varchar), '|' order by cast(t as varchar))) "
            f"from {table} t"  # noqa: S608
        ).fetchone()
        out[table] = row[0][:12] if row else ""
    con.close()
    return out


def run_child() -> int:
    sys.path.insert(0, str(ROOT / "dbt"))
    from headway_transform import handler

    timings: list[tuple[str, float]] = []
    real_run_dbt = handler.run_dbt

    def timed_run_dbt(a: list[str]) -> Any:
        start = time.monotonic()
        try:
            return real_run_dbt(a)
        finally:
            timings.append((" ".join(a[:3]), time.monotonic() - start))

    handler.run_dbt = timed_run_dbt  # type: ignore[assignment]
    variables = json.dumps(
        {"curated_root": f"s3://{BUCKET}", "city_prefix": CITY, "lookback_days": 7}
    )
    t0 = time.monotonic()
    outcome = handler._build_and_test(variables)
    total = time.monotonic() - t0
    for label, secs in timings:
        print(f"  dbt {label:<40} {secs:8.1f}s")
    print(
        f"build+test total {total:.1f}s  tests passed={outcome.tests_passed} "
        f"failed={outcome.tests_failed}"
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--per-day", type=int, default=1440)
    ap.add_argument("--latency-ms", type=float, default=10.0, help="fixed delay per S3 request")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--db-out", type=Path, help="copy the resulting DuckDB file here")
    ap.add_argument("--child", action="store_true")
    args = ap.parse_args()
    if args.child:
        return run_child()

    env = {k: v for k, v in os.environ.items() if k not in {"VIRTUAL_ENV", "UV_PROJECT"}}
    with tempfile.TemporaryDirectory(prefix="headway-bench-") as tmp:
        replay = Path(tmp) / "replay"
        subprocess.run(
            ["uv", "run", "headway", "replay", "--source", "samples/boston", "--target",
             str(replay), "--city", CITY],
            cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL,
        )  # fmt: skip
        bucket_dir = Path(tmp) / "s3" / BUCKET
        t0 = time.monotonic()
        manifests = populate(bucket_dir, replay, args.days, args.per_day)
        print(
            f"{manifests} manifest objects over {args.days} day(s), "
            f"latency {args.latency_ms:g} ms/request "
            f"(populated in {time.monotonic() - t0:.0f}s)",
            flush=True,
        )
        if args.backfill:
            from headway.config import load_city_config
            from headway.curate.meta import backfill_manifests
            from headway.storage import LocalStorage

            t0 = time.monotonic()
            result = backfill_manifests(LocalStorage(bucket_dir), load_city_config(CITY))
            print(f"backfill {result} in {time.monotonic() - t0:.1f}s", flush=True)

        server, endpoint = serve(Path(tmp) / "s3", args.latency_ms / 1000)
        env.update(
            AWS_ACCESS_KEY_ID="test", AWS_SECRET_ACCESS_KEY="test", AWS_DEFAULT_REGION="us-east-1",
            HEADWAY_S3_ENDPOINT=endpoint, HEADWAY_DUCKDB_PATH=str(Path(tmp) / "bench.duckdb"),
            HEADWAY_DBT_TARGET="s3_test", DBT_PROJECT_DIR=str(ROOT / "dbt" / "headway"),
            DBT_SEND_ANONYMOUS_USAGE_STATS="False",
        )  # fmt: skip
        # The dbt environment has no boto3 (the Lambda runtime supplies it), hence --with.
        subprocess.run(
            ["uv", "run", "--project", "dbt", "--with", "boto3", "python", __file__, "--child"],
            cwd=ROOT, env=env, check=True,
        )  # fmt: skip
        print(f"s3 requests: {S3Handler.requests}")
        print("checksums", json.dumps(checksum(env["HEADWAY_DUCKDB_PATH"])))
        if args.db_out:
            shutil.copy(env["HEADWAY_DUCKDB_PATH"], args.db_out)
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
