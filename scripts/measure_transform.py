"""Run the transform image under Lambda conditions and report memory, spill and time per step.

Local only: a moto S3 on the host holds a copy of the curated data (``--curated``, never AWS),
the container runs the real ``lambda_handler`` with the production environment (Release 2 and 3
on, ``LOOKBACK_DAYS``), a hard ``--memory`` equal to the function's, and a disk-backed ``/tmp``
(Lambda's ephemeral storage is not memory; a tmpfs would be charged to the container).

    uv run python scripts/measure_transform.py --curated .local/prod_copy/curated --label real
"""

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
RAW, PUBLIC = "measure-raw", "measure-public"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return int(s.getsockname()[1])


def sh(*cmd: str) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout.strip()


class Sampler(threading.Thread):
    """Samples the container's cgroup: anonymous memory (what the function itself holds), the
    total including page cache, and the spill directory."""

    def __init__(self, name: str) -> None:
        super().__init__(daemon=True)
        self.name_ = name
        self.stop_ = threading.Event()
        self.anon_mb = self.total_mb = self.spill_mb = 0.0

    def run(self) -> None:
        script = (
            "cat /sys/fs/cgroup/memory.current; grep -E '^anon ' /sys/fs/cgroup/memory.stat; "
            "du -sk /tmp/duckdb_spill 2>/dev/null | cut -f1"
        )
        while not self.stop_.is_set():
            out = sh("docker", "exec", self.name_, "sh", "-c", script).split("\n")
            try:
                self.total_mb = max(self.total_mb, int(out[0]) / 1048576)
                self.anon_mb = max(self.anon_mb, int(out[1].split()[1]) / 1048576)
                self.spill_mb = max(self.spill_mb, int(out[2]) / 1024)
            except (IndexError, ValueError):
                pass
            self.stop_.wait(1.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--curated", type=Path, required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--memory-mb", type=int, default=3008)
    ap.add_argument("--lookback-days", type=int, default=7)
    ap.add_argument("--image", default="headway-transform:local")
    ap.add_argument("--no-r2", action="store_true")
    ap.add_argument("--env", action="append", default=[], help="extra KEY=VALUE for the function")
    ap.add_argument("--out", type=Path, default=ROOT / ".local" / "measure")
    args = ap.parse_args()

    work = args.out / args.label
    shutil.rmtree(work, ignore_errors=True)
    tmp = work / "tmp"
    tmp.mkdir(parents=True)
    tmp.chmod(0o777)
    moto_port = free_port()
    moto = subprocess.Popen(
        [sys.executable, "-m", "moto.server", "-H", "0.0.0.0", "-p", str(moto_port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    name = f"headway-measure-{args.label}-{os.getpid()}"
    try:
        endpoint = f"http://localhost:{moto_port}"
        for _ in range(60):
            try:
                socket.create_connection(("localhost", moto_port), 1).close()
                break
            except OSError:
                time.sleep(0.5)
        s3 = boto3.client(
            "s3", endpoint_url=endpoint, region_name="us-east-1",
            aws_access_key_id="smoke", aws_secret_access_key="smoke",
        )  # fmt: skip
        for bucket in (RAW, PUBLIC):
            s3.create_bucket(Bucket=bucket)
        files = [p for p in args.curated.rglob("*") if p.is_file()]
        with ThreadPoolExecutor(16) as pool:
            list(pool.map(
                lambda p: s3.upload_file(
                    str(p), RAW, "curated/" + str(p.relative_to(args.curated))
                ),
                files,
            ))  # fmt: skip
        subprocess.run(
            ["uv", "run", "--project", "ml", "python", "scripts/smoke_serve_seed.py",
             "--endpoint", endpoint, "--bucket", RAW],
            cwd=ROOT, check=True, capture_output=True,
        )  # fmt: skip
        print(f"[{args.label}] uploaded {len(files)} objects", flush=True)  # noqa: T201

        env = {
            "CITY_ID": "boston", "RAW_BUCKET": RAW, "PUBLIC_BUCKET": PUBLIC,
            "LOOKBACK_DAYS": str(args.lookback_days),
            "HEADWAY_ENABLE_R2": "false" if args.no_r2 else "true",
            "HEADWAY_ENABLE_R3": "false" if args.no_r2 else "true",
            "HEADWAY_DBT_TARGET": "s3_test",
            "HEADWAY_S3_ENDPOINT": f"host.docker.internal:{moto_port}",
            "AWS_ENDPOINT_URL": f"http://host.docker.internal:{moto_port}",
            "AWS_DEFAULT_REGION": "us-east-1", "AWS_ACCESS_KEY_ID": "smoke",
            "AWS_SECRET_ACCESS_KEY": "smoke",
            "AWS_LAMBDA_FUNCTION_MEMORY_SIZE": str(args.memory_mb),
        }  # fmt: skip
        for item in args.env:
            key, _, value = item.partition("=")
            env[key] = value
        port = free_port()
        cmd = [
            "docker", "run", "-d", "--name", name, "--platform", "linux/arm64",
            f"--memory={args.memory_mb}m", f"--memory-swap={args.memory_mb}m",
            "--read-only", "--ipc=none", "-v", f"{tmp}:/tmp", "--user", "993:990",
            "-p", f"{port}:8080", "--add-host=host.docker.internal:host-gateway",
            *[x for k, v in env.items() for x in ("-e", f"{k}={v}")],
            "--entrypoint", "env", args.image, "-u", "HOME", "/lambda-entrypoint.sh",
            "headway_transform.handler.lambda_handler",
        ]  # fmt: skip
        subprocess.run(cmd, check=True, capture_output=True)
        for _ in range(60):
            try:
                socket.create_connection(("localhost", port), 1).close()
                break
            except OSError:
                time.sleep(0.5)
        sampler = Sampler(name)
        sampler.start()
        t0 = time.monotonic()
        result = subprocess.run(
            ["curl", "-s", "--max-time", "1800", "-XPOST",
             f"localhost:{port}/2015-03-31/functions/function/invocations", "-d", "{}"],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        wall = time.monotonic() - t0
        sampler.stop_.set()
        sampler.join(5)
        logs = (
            sh("docker", "logs", name)
            + subprocess.run(
                ["docker", "logs", name], capture_output=True, text=True, check=False
            ).stderr
        )
        events = sh("docker", "exec", name, "cat", "/sys/fs/cgroup/memory.events")
        cgroup_peak = sh("docker", "exec", name, "cat", "/sys/fs/cgroup/memory.peak")
        oom = sh("docker", "inspect", name, "--format", "{{.State.OOMKilled}}")
        (work / "container.log").write_text(logs)
        summary = None
        for line in logs.splitlines():
            m = re.search(r"(\{.*\"step_ms\".*\})\s*$", line)
            if m:
                summary = json.loads(m.group(1))
        report = {
            "label": args.label,
            "limit_mb": args.memory_mb,
            "wall_s": round(wall, 1),
            "oom_killed": oom,
            "cgroup_events": events.replace("\n", " "),
            "cgroup_peak_mb_incl_page_cache": round(int(cgroup_peak or 0) / 1048576),
            "sampled_anon_mb": round(sampler.anon_mb),
            "sampled_total_mb": round(sampler.total_mb),
            "sampled_spill_mb": round(sampler.spill_mb),
            "invocation": result.stdout[:400],
            "summary": summary,
        }
        (work / "report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))  # noqa: T201
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        moto.terminate()


if __name__ == "__main__":
    main()
