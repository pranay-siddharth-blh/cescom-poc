"""Builds the single prompt sent to the LLM per question."""
from __future__ import annotations

import json
from pathlib import Path

from app.config import PROJECT_ROOT

_SCHEMA_SQL_PATH = PROJECT_ROOT / "DCB_Star_Schema" / "schema.sql"
_schema_ddl_cache: str | None = None


def load_schema_ddl() -> str:
    global _schema_ddl_cache
    if _schema_ddl_cache is None:
        _schema_ddl_cache = Path(_SCHEMA_SQL_PATH).read_text(encoding="utf-8")
    return _schema_ddl_cache


SYSTEM_INSTRUCTIONS = """\
You are the SQL-generation engine for a read-only NL2SQL chatbot over a \
CESCOM (Karnataka electricity board) Demand-Collection-Balance (DCB) \
billing database for Holenarasipura Division.

You get ONE turn. In that turn you must return a single JSON object \
(matching the given response schema) containing BOTH:
  1. A single read-only SQLite SELECT (or WITH...SELECT) statement that \
answers the question.
  2. A natural-language answer_template with {placeholder} tokens that \
Python will fill in AFTER running your SQL — you will NOT see the query \
result and will NOT get a second turn to phrase the final answer. Every \
placeholder must be either a column alias you gave in your own SELECT \
(use AS on every column), or one of the pre-extracted entities listed \
below (only if that entity is relevant to the question).

If the question can't be safely/correctly answered from this schema \
(ambiguous, references a tariff code or sub-division that doesn't exist, \
would require a write, etc.), set answerable=false and explain briefly \
in clarification_message instead of guessing — do not invent data.

Hard rules (violating any of these makes the answer wrong, not just \
imperfect):
- Read-only. SELECT / WITH...SELECT only. Never INSERT/UPDATE/DELETE/DROP/etc.
- fiscal_year is a TEXT column like '2015-16' — never compare it as a number.
"""


def build_prompt(question: str, schema_ddl: str, pruned_semantic_layer: dict, entities: dict) -> str:
    parts = [
        SYSTEM_INSTRUCTIONS,
        "\n## Database schema (authoritative column names — use exactly these)\n",
        "```sql\n" + schema_ddl + "\n```\n",
        "\n## Business semantic layer (glossary, grounding rules, worked patterns)\n",
        "```json\n" + json.dumps(pruned_semantic_layer, indent=2) + "\n```\n",
    ]
    if entities:
        parts.append(
            "\n## Entities already extracted from the question (reuse these exact "
            "values in SQL literals and in answer_template placeholders — do not "
            "re-derive or reformat them)\n"
            + json.dumps(entities, indent=2)
            + "\n"
        )
    parts.append(f"\n## Question\n{question}\n")
    return "".join(parts)
