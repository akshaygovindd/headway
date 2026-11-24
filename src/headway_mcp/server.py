"""The FastMCP server: the five tools and nothing else (no resources, no prompts, no free SQL)."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from headway_mcp.catalog import GRANULARITIES, LINES, METRICS
from headway_mcp.tools import Tools

INSTRUCTIONS = (
    "Read-only reliability tools over Headway's public gold tables. Every result carries its SQL, "
    "metric definition, comparison period, coverage and freshness. Values from hours under 50% "
    "arrival coverage are withheld."
)


def build_server(tools: Tools) -> FastMCP:
    mcp = FastMCP("headway", instructions=INSTRUCTIONS)

    @mcp.tool()
    def get_line_reliability(line: str, metric: str, window: str) -> dict[str, Any]:
        """One metric for one line over a window (last_24h or last_7d)."""
        return tools.get_line_reliability(line, metric, window)

    @mcp.tool()
    def compare_lines(lines: list[str], metric: str, window: str) -> dict[str, Any]:
        """One metric for 2 to 8 lines over a window; delta is against the first line."""
        return tools.compare_lines(lines, metric, window)

    @mcp.tool()
    def get_delay_trends(
        line: str, metric: str, granularity: str, date_range: dict[str, str]
    ) -> dict[str, Any]:
        """A metric over time for one line (hourly or daily), at most 31 days."""
        return tools.get_delay_trends(line, metric, granularity, date_range)

    @mcp.tool()
    def get_service_alerts(line: str, date_range: dict[str, str]) -> dict[str, Any]:
        """Service alerts active on a line, range {start, end}, at most 7 days."""
        return tools.get_service_alerts(line, date_range)

    @mcp.tool()
    def get_pipeline_health(date_range: dict[str, str]) -> dict[str, Any]:
        """Pipeline runs, polls, duplicates and quarantined records, at most 7 days."""
        return tools.get_pipeline_health(date_range)

    return mcp


def tool_specs() -> list[dict[str, Any]]:
    """The tool definitions in the function-calling format of the LLM clients (enums included)."""
    rng = {
        "type": "object",
        "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
        "required": ["start", "end"],
    }
    line = {"type": "string", "enum": list(LINES)}
    metric = {"type": "string", "enum": list(METRICS)}
    window = {"type": "string", "enum": ["last_24h", "last_7d"]}

    def spec(name: str, desc: str, props: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": desc,
                "parameters": {"type": "object", "properties": props, "required": list(props)},
            },
        }

    return [
        spec("get_line_reliability", "One metric for one line over a window.",
             {"line": line, "metric": metric, "window": window}),
        spec("compare_lines", "One metric for several lines over a window.",
             {"lines": {"type": "array", "items": line, "minItems": 2}, "metric": metric,
              "window": window}),
        spec("get_delay_trends", "A metric over time for one line (range of at most 31 days).",
             {"line": line, "metric": metric,
              "granularity": {"type": "string", "enum": list(GRANULARITIES)}, "date_range": rng}),
        spec("get_service_alerts", "Service alerts on a line (range of at most 7 days).",
             {"line": line, "date_range": rng}),
        spec("get_pipeline_health", "Pipeline health (range of at most 7 days).",
             {"date_range": rng}),
    ]  # fmt: skip
