"""
The single request pipeline, shared by the SSE endpoint, the plain JSON
endpoint, and the eval harness — so all three exercise exactly the same
code path (design consistency; only the delivery mechanism differs).

Steps, each timed separately (returned in `timing_ms` so latency is
attributable to a stage, not just a total):
  1. router   — deterministic entity extraction + semantic-layer pruning
               (app.semantic_layer) — no LLM call.
  2. llm      — the ONE LLM call (app.llm.generate_query_plan) that
               returns SQL + answer_template together.
  3. safety   — text-level read-only validation (app.sql_safety).
  4. db       — execute against the read-only connection (app.db),
               row-capped and wall-clock-timeout-bounded.
  5. fill     — deterministically fill answer_template from the query
               result + extracted entities. NO second LLM call.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.db import ReadOnlyDB
from app.llm import generate_query_plan
from app.models import LLMQueryPlan
from app.prompts import build_prompt, load_schema_ddl
from app.semantic_layer import RouterIndex, extract_entities, prune_semantic_layer
from app.sql_safety import UnsafeSQLError, validate_readonly_sql


@dataclass
class PipelineResult:
    question: str
    answerable: bool
    sql: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    truncated: bool = False
    answer: str = ""
    clarification_message: str = ""
    entities: dict = field(default_factory=dict)
    timing_ms: dict = field(default_factory=dict)
    error: str = ""


def _format_value(value) -> str:
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e15:
            return f"{int(value):,}"
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    if value is None:
        return "N/A"
    return str(value)


def _fill_template(template: str, columns: list[str], rows: list[tuple], entities: dict) -> str:
    if not rows:
        return "No matching data was found for that question."

    if len(rows) == 1:
        row_dict = {col: _format_value(val) for col, val in zip(columns, rows[0])}
        fill_values = {**entities, **row_dict}
        try:
            return template.format(**fill_values)
        except (KeyError, IndexError):
            pass  # fall through to the generic renderer below

    # Multiple rows, or the template didn't match the single row's
    # columns/entities: fall back to a generic, honest listing rather
    # than silently dropping rows or crashing. Documented limitation —
    # see CLAUDE.md "Known limitations".
    lines = []
    cap = 10
    for i, row in enumerate(rows[:cap]):
        parts = [f"{col}={_format_value(val)}" for col, val in zip(columns, row)]
        lines.append(f"{i + 1}. " + ", ".join(parts))
    suffix = f" (showing {min(cap, len(rows))} of {len(rows)} rows)" if len(rows) > cap or len(rows) > 1 else ""
    return "Results:" + suffix + "\n" + "\n".join(lines)


async def answer_question(
    question: str, db: ReadOnlyDB, router_index: RouterIndex, max_rows: int, timeout_seconds: float
) -> PipelineResult:
    timing: dict[str, float] = {}
    result = PipelineResult(question=question, answerable=False)

    t0 = time.monotonic()
    entities = extract_entities(question, router_index)
    pruned_layer = prune_semantic_layer(question, router_index)
    schema_ddl = load_schema_ddl()
    prompt = build_prompt(question, schema_ddl, pruned_layer, entities)
    timing["router_ms"] = round((time.monotonic() - t0) * 1000, 1)
    result.entities = entities

    t0 = time.monotonic()
    try:
        plan: LLMQueryPlan = await generate_query_plan(prompt)
    except Exception as exc:  # noqa: BLE001 — surfaced to the caller, not swallowed
        timing["llm_ms"] = round((time.monotonic() - t0) * 1000, 1)
        result.timing_ms = timing
        result.error = f"LLM call failed: {exc}"
        result.answer = "Sorry, I couldn't process that question right now (LLM error)."
        return result
    timing["llm_ms"] = round((time.monotonic() - t0) * 1000, 1)

    if not plan.answerable:
        result.answerable = False
        result.clarification_message = plan.clarification_message
        result.answer = plan.clarification_message or "I can't answer that from this dataset."
        result.timing_ms = timing
        return result

    t0 = time.monotonic()
    try:
        safe_sql = validate_readonly_sql(plan.sql)
    except UnsafeSQLError as exc:
        timing["safety_ms"] = round((time.monotonic() - t0) * 1000, 1)
        result.timing_ms = timing
        result.error = f"Rejected unsafe SQL: {exc}"
        result.answer = "Sorry, the generated query didn't pass the read-only safety check."
        result.sql = plan.sql
        return result
    timing["safety_ms"] = round((time.monotonic() - t0) * 1000, 1)
    result.sql = safe_sql

    t0 = time.monotonic()
    try:
        query_result = db.run(safe_sql, max_rows=max_rows, timeout_seconds=timeout_seconds)
    except (TimeoutError, Exception) as exc:  # noqa: BLE001
        timing["db_ms"] = round((time.monotonic() - t0) * 1000, 1)
        result.timing_ms = timing
        result.error = f"SQL execution failed: {exc}"
        result.answer = "Sorry, that query couldn't be executed against the database."
        return result
    timing["db_ms"] = round((time.monotonic() - t0) * 1000, 1)

    result.columns = query_result.columns
    result.rows = query_result.rows
    result.truncated = query_result.truncated

    t0 = time.monotonic()
    result.answer = _fill_template(plan.answer_template, query_result.columns, query_result.rows, entities)
    timing["fill_ms"] = round((time.monotonic() - t0) * 1000, 1)

    result.answerable = True
    result.timing_ms = timing
    return result
