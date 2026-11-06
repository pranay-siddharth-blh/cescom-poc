"""Shared fixtures. Every pytest test runs without the LLM gateway (no VPN needed): the gateway is switched off here
(environment variables win over .env), and tests that exercise the model path mock it.

    python -m pytest tests -q -p no:cacheprovider      (from dcb-ai-app/)
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["SQL_VERIFY_BASE_URL"] = ""                   # offline: the model is never called for real
os.environ.setdefault("DCB_RATE_LIMIT_PER_MINUTE", "100000")   # the limit itself has its own test
os.environ.setdefault("DCB_SHOW_TECHNICAL_DETAILS", "false")

from app.config import settings  # noqa: E402
from app.db import ReadOnlyDB  # noqa: E402
from app.models import ChartSpec, LLMQueryPlan  # noqa: E402


@pytest.fixture(scope="session")
def db():
    d = ReadOnlyDB(settings.db_path)
    yield d
    d.close()


@pytest.fixture(scope="session")
def router_index(db):
    from app.semantic_layer import build_router_index
    return run(build_router_index(db))


def run(coro):
    return asyncio.run(coro)


def plan(sql: str, template: str = "", chart: ChartSpec | None = None, answerable: bool = True,
         message: str = "") -> LLMQueryPlan:
    return LLMQueryPlan(answerable=answerable, sql=sql, answer_template=template, clarification_message=message,
                        chart_data=chart)


def star_one(db, sql: str, params: tuple = ()):
    """An independent figure straight from the locked star schema (not the mart the app answers from)."""
    return db.run_sync(sql, 5, params=params).rows[0][0]
