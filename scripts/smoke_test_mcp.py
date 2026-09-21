#!/usr/bin/env python
"""
Drive the MCP server over a real stdio MCP session and print what an agent sees.

This is not a unit test of the Python functions — it launches the server as a
subprocess, speaks the MCP protocol to it, and calls the tools the way a model
would. If this passes, an MCP client will work.

Run it with the project's virtualenv:

    .venv/bin/python scripts/smoke_test_mcp.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

PROJECT_DIR = Path(__file__).resolve().parent.parent


def _content_to_obj(result) -> object:
    """Pull the structured payload out of a tool result."""
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
    return None


def _show(title: str, payload: object, limit: int = 1400) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    text = json.dumps(payload, indent=2, default=str) if not isinstance(payload, str) else payload
    print(text[:limit] + ("\n… (truncated)" if len(text) > limit else ""))


async def main() -> int:
    env = dict(os.environ)
    env.setdefault("DBT_PROFILES_DIR", str(PROJECT_DIR))
    env.setdefault("GA4_DBT_PROJECT_DIR", str(PROJECT_DIR))
    if not env.get("DBT_BIGQUERY_PROJECT"):
        print("DBT_BIGQUERY_PROJECT is not set — source .env first.", file=sys.stderr)
        return 1

    params = StdioServerParameters(
        command=str(PROJECT_DIR / ".venv" / "bin" / "python"),
        args=["-m", "mcp_server.server"],
        cwd=str(PROJECT_DIR),
        env=env,
    )

    failures: list[str] = []

    async with Client(params, raise_exceptions=True) as client:
        tools = await client.list_tools()
        names = sorted(t.name for t in tools.tools)
        _show("Tools the agent can see", names)
        expected = {
            "list_metrics",
            "describe_metric",
            "list_semantic_models",
            "list_dimensions",
            "query_metrics",
            "explain_metric_sql",
            "health_check",
        }
        if set(names) != expected:
            failures.append(f"tool set mismatch: {set(names) ^ expected}")
        if any("sql" == n or n.startswith("run_sql") for n in names):
            failures.append("a raw-SQL tool is exposed; the governance claim is void")

        health = _content_to_obj(await client.call_tool("health_check", {}))
        _show("health_check", health)
        if not (isinstance(health, dict) and health.get("ready")):
            failures.append("health_check reports not ready")

        metrics = _content_to_obj(await client.call_tool("list_metrics", {"search": "retention"}))
        _show("list_metrics(search='retention')", metrics)

        described = _content_to_obj(
            await client.call_tool("describe_metric", {"metric": "d7_retention_rate"})
        )
        _show("describe_metric('d7_retention_rate')", described)
        gov = (described or {}).get("governance", {}) if isinstance(described, dict) else {}
        for field in ("definition", "assumption", "tradeoff", "breaks_if"):
            if not gov.get(field):
                failures.append(f"governance field missing: {field}")

        dims = _content_to_obj(
            await client.call_tool("list_dimensions", {"metrics": "d7_retention_rate"})
        )
        _show("list_dimensions('d7_retention_rate')", dims)

        # The headline demo: the question from the brief, answered by traversing
        # metrics rather than by writing SQL.
        answer = _content_to_obj(
            await client.call_tool(
                "query_metrics",
                {
                    "metrics": "d7_retention_rate,d7_eligible_users",
                    "group_by": "metric_time__month",
                    "where": "{{ Dimension('user__first_device_category') }} = 'mobile'",
                    "order_by": "metric_time__month",
                },
            )
        )
        _show("query_metrics — D7 retention for mobile users, by acquisition month", answer)
        if not (isinstance(answer, dict) and answer.get("ok") and answer.get("rows")):
            failures.append("the headline query returned no rows")
        if isinstance(answer, dict) and not answer.get("governance"):
            failures.append("query_metrics returned numbers without governance")

        sql = _content_to_obj(
            await client.call_tool(
                "explain_metric_sql",
                {"metrics": "activation_rate", "group_by": "user__first_device_category"},
            )
        )
        if isinstance(sql, dict) and sql.get("sql"):
            _show("explain_metric_sql('activation_rate') — compiled SQL", sql["sql"], limit=900)
        else:
            failures.append("explain_metric_sql returned no SQL")

        # Governance: an invented metric must be refused, not approximated.
        bogus_result = await client.call_tool("query_metrics", {"metrics": "made_up_metric"})
        bogus = _content_to_obj(bogus_result)
        refused = bool(getattr(bogus_result, "isError", False)) or (
            isinstance(bogus, dict) and bogus.get("refused") is True
        )
        _show("query_metrics('made_up_metric') — must be refused", bogus, limit=700)
        if not refused:
            failures.append("an unknown metric was not refused")

    print(f"\n{'=' * 78}")
    if failures:
        print("SMOKE TEST FAILED")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SMOKE TEST PASSED — all tools reachable over MCP, governance travels with every number.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
