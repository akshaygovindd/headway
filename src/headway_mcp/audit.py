"""One audit log line per tool call: question id, tool, arguments, rows, latency."""

import contextvars
import json
import logging

logger = logging.getLogger("headway_mcp.audit")
question_id: contextvars.ContextVar[str] = contextvars.ContextVar("question_id", default="-")


def log_call(
    tool: str, args: dict[str, object], rows: int, latency_ms: float, status: str
) -> dict[str, object]:
    line: dict[str, object] = {
        "question_id": question_id.get(),
        "tool": tool,
        "args": args,
        "rows": rows,
        "latency_ms": round(latency_ms, 1),
        "status": status,
    }
    logger.info(json.dumps(line, default=str, sort_keys=True))
    return line
