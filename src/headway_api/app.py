"""The public read-only API (ADR-023): FastAPI over the public gold Parquet, no credentials.

Every 200 carries `data`, `coverage`, `freshness` and `provenance`. Enum and date errors are 422
with the allowed values; a table the export lacks is a 404 `not_available`.
"""

import json
import logging
import re
import time
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from headway_api import API_VERSION, queries
from headway_api.cache import GoldSource
from headway_api.queries import InputError, NotAvailableError, Result
from headway_api.settings import Settings
from headway_mcp import config, runner
from headway_mcp.catalog import LINES, METRICS_DOC

access_log = logging.getLogger("headway_api.access")
LineName = Literal["Red", "Orange", "Blue", "Green-B", "Green-C", "Green-D", "Green-E", "Mattapan"]
WindowName = Literal["last_24h", "last_7d"]
RULE = (
    f"A value from a row whose coverage is under {config.MIN_COVERAGE_PCT:g}% is withheld: null, "
    "with a reason next to it."
)


class Envelope(BaseModel):
    data: Any
    coverage: dict[str, Any]
    freshness: dict[str, Any]
    provenance: dict[str, Any]


def _freshness(q: queries.Query) -> dict[str, Any]:
    m = q.snap.manifest
    age = None
    try:
        exported = datetime.fromisoformat(str(m["exported_at"]).replace("Z", "+00:00"))
        age = round((datetime.now(UTC) - exported).total_seconds() / 60, 1)
    except (KeyError, ValueError):
        pass
    return {"exported_at": m.get("exported_at"), "metrics_as_of": m.get("metrics_as_of"),
            "age_minutes": age}  # fmt: skip


def _coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cov = [r["coverage_pct"] for r in rows if r.get("coverage_pct") is not None]
    withheld = sum(1 for r in rows if r.get("withheld_reason") or r.get("withheld_reasons"))
    return {
        "rows": len(rows),
        "mean_coverage_pct": round(sum(cov) / len(cov), 1) if cov else None,
        "min_coverage_pct": min(cov) if cov else None,
        "withheld_rows": withheld,
        "rule": RULE,
    }


def _envelope(q: queries.Query, res: Result, settings: Settings) -> Envelope:
    return Envelope(
        data=res.data,
        coverage=_coverage(res.rows),
        freshness=_freshness(q),
        provenance={
            "tables": res.tables,
            "definition": res.definition,
            "metrics_md": f"{METRICS_DOC}#{res.anchor}",
            "row_limit": settings.max_rows,
            "truncated": res.truncated,
            "served_from": q.snap.served_from,
            "sql": res.sql,
        },
    )


def create_app(settings: Settings | None = None, source: GoldSource | None = None) -> FastAPI:
    st = settings or Settings.from_env()
    src = source or GoldSource(st.data_url, st.cache_dir, queries.API_TABLES, st.recheck_s)
    app = FastAPI(
        title="Headway metric API",
        version=API_VERSION,
        description="Read-only MBTA subway reliability metrics from Headway's public gold tables. "
        f"{RULE} Definitions: docs/METRICS.md.",
        docs_url="/docs",
        openapi_url="/openapi.json",
        redoc_url=None,
    )

    @app.middleware("http")
    async def hygiene(request: Request, call_next: Any) -> Response:
        started = time.perf_counter()
        too_big = int(request.headers.get("content-length") or 0) > st.max_request_bytes
        if too_big or len(str(request.url)) > st.max_request_bytes:
            response: Response = JSONResponse(
                {"error": "request_too_large", "limit_bytes": st.max_request_bytes},
                status_code=413,
            )
        else:
            response = await call_next(request)
        if request.url.path.startswith("/v1"):
            ok = response.status_code == 200
            response.headers["Cache-Control"] = (
                f"public, max-age={st.cache_max_age_s}" if ok else "no-store"
            )
        access_log.info(json.dumps({
            "method": request.method, "path": request.url.path, "status": response.status_code,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "rows": getattr(request.state, "rows", None),
            "served_from": getattr(request.state, "served_from", None),
            "origin": request.headers.get("origin"),
        }, sort_keys=True))  # fmt: skip
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(st.cors_origins),
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["Content-Type"],
        max_age=600,
    )

    # --- errors ---------------------------------------------------------------------------

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        detail = []
        for e in exc.errors():
            allowed = re.findall(r"'([^']*)'", str(e.get("ctx", {}).get("expected", "")))
            detail.append({"loc": list(e["loc"]), "message": e["msg"], "allowed": allowed or None})
        return JSONResponse({"error": "validation_error", "detail": detail}, status_code=422)

    @app.exception_handler(InputError)
    async def _input(_: Request, exc: InputError) -> JSONResponse:
        detail = [{"loc": [], "message": str(exc), "allowed": exc.allowed}]
        return JSONResponse({"error": "validation_error", "detail": detail}, status_code=422)

    @app.exception_handler(NotAvailableError)
    async def _na(_: Request, exc: NotAvailableError) -> JSONResponse:
        return JSONResponse({"error": "not_available", "reason": exc.reason,
                             "tables_in_export": exc.tables}, status_code=404)  # fmt: skip

    @app.exception_handler(runner.ToolTimeoutError)
    async def _timeout(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"error": "timeout", "reason": str(exc)}, status_code=504)

    @app.exception_handler(runner.DataError)
    async def _data(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"error": "data_unavailable", "reason": "the gold export could not "
                             "be read"}, status_code=502)  # fmt: skip

    # --- endpoints ------------------------------------------------------------------------

    def serve(request: Request, call: Any) -> Envelope:
        q = queries.Query(src, st.max_rows)
        res: Result = call(q)
        request.state.rows = len(res.rows)
        request.state.served_from = q.snap.served_from
        return _envelope(q, res, st)

    v1 = "/v1"
    tag: list[str | Enum] = ["metrics"]

    @app.get(f"{v1}/lines", response_model=Envelope, tags=tag, summary="Lines and their coverage")
    def lines(request: Request) -> Envelope:
        return serve(request, lambda q: q.lines())

    def line_endpoint(name: str, summary: str) -> None:
        def handler(
            request: Request,
            line: LineName,
            window: Annotated[WindowName, Query(description="Rolling window")] = "last_24h",
        ) -> Envelope:
            return serve(request, lambda q: q.sections(name, line, window))

        handler.__name__ = f"line_{name}"
        app.get(f"{v1}/lines/{{line}}/{name}", response_model=Envelope, tags=tag,
                summary=summary)(handler)  # fmt: skip

    for name, summary in (
        ("reliability", "Predicted and actual schedule deviation, on-time share"),
        ("accuracy", "Accuracy of MBTA's published predictions by horizon"),
        ("headways", "Headway adherence: within band, bunched, gaps"),
        ("countdown", "Countdown accuracy by station"),
    ):
        line_endpoint(name, summary)

    @app.get(f"{v1}/lines/{{line}}/segments", response_model=Envelope, tags=tag,
             summary="Headway health per segment of a line")  # fmt: skip
    def line_segments(
        request: Request,
        line: LineName,
        window: Annotated[WindowName, Query(description="Rolling window")] = "last_24h",
        direction: Annotated[
            int | None, Query(ge=0, le=1, description="direction_id: 0 or 1")
        ] = None,
    ) -> Envelope:
        return serve(request, lambda q: q.segments(line, window, direction))

    @app.get(f"{v1}/lines/{{line}}/marey", response_model=Envelope, tags=tag,
             summary="Reconstructed arrivals of the last complete hour")  # fmt: skip
    def line_marey(
        request: Request,
        line: LineName,
        direction: Annotated[
            int | None, Query(ge=0, le=1, description="direction_id: 0 or 1")
        ] = None,
    ) -> Envelope:
        return serve(request, lambda q: q.marey(line, direction))

    @app.get(f"{v1}/incidents", response_model=Envelope, tags=tag, summary="Incidents in a range")
    def incidents(
        request: Request,
        line: LineName | None = None,
        from_: Annotated[str | None, Query(alias="from", description="YYYY-MM-DD, local")] = None,
        to: Annotated[str | None, Query(description="YYYY-MM-DD, local date")] = None,
    ) -> Envelope:
        value = line
        return serve(request, lambda q: q.incidents(value, from_, to))

    @app.get(f"{v1}/forecast/{{line}}", response_model=Envelope, tags=tag,
             summary="Next-hour delay forecast and training gate")  # fmt: skip
    def forecast(request: Request, line: LineName) -> Envelope:
        return serve(request, lambda q: q.forecast(line))

    @app.get(f"{v1}/pipeline/health", response_model=Envelope, tags=tag, summary="Pipeline health")
    def pipeline_health(
        request: Request, days: Annotated[int, Query(description="1 to 30")] = 7
    ) -> Envelope:
        return serve(request, lambda q: q.health(days))

    @app.get(f"{v1}/pipeline/heartbeat", response_model=Envelope, tags=tag,
             summary="Hourly capture heartbeat")  # fmt: skip
    def pipeline_heartbeat(
        request: Request,
        hours: Annotated[int, Query(description=f"1 to {queries.MAX_HEARTBEAT_HOURS}")] = 24,
    ) -> Envelope:
        return serve(request, lambda q: q.heartbeat(hours))

    @app.get(f"{v1}/slo", response_model=Envelope, tags=tag, summary="Service level objectives")
    def slo(request: Request) -> Envelope:
        return serve(request, lambda q: q.slo())

    @app.get(f"{v1}/metrics", response_model=Envelope, tags=tag, summary="Metric definitions")
    def metrics(request: Request) -> Envelope:
        def call(q: queries.Query) -> Result:
            return Result(queries.metrics_catalog(), [], [], "", "Metric definitions", [])

        return serve(request, call)

    @app.get(f"{v1}/meta", response_model=Envelope, tags=tag, summary="Freshness and rules")
    def meta(request: Request) -> Envelope:
        def call(q: queries.Query) -> Result:
            data = {
                "api_version": API_VERSION,
                "lines": list(LINES),
                "windows": list(queries.WINDOWS),
                "tables_in_export": sorted(q.tables),
                "coverage_rules": {"min_coverage_pct": config.MIN_COVERAGE_PCT, "rule": RULE},
                "limits": {"max_rows": st.max_rows, "incident_range_days": 31,
                           "health_days": 30, "max_request_bytes": st.max_request_bytes},
                "cache_control": f"public, max-age={st.cache_max_age_s}",
                "metrics_md": METRICS_DOC,
            }  # fmt: skip
            return Result(
                data, [], [], "", "Export freshness and the rules every value follows", []
            )

        return serve(request, call)

    return app
