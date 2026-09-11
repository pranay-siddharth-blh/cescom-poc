"""
Proves the optional alternative-LLM-provider path fails loudly and safely
when misconfigured -- WITHOUT ever touching the real .env and WITHOUT any
network attempt. app/llm.py::_validate_alt_provider_config() is designed
to raise before any client is constructed and before any network activity
(see its docstring), so these tests only need to flip in-memory settings
fields, call it directly, and confirm a clear RuntimeError -- not a crash,
not a hang.

Run from the dcb-ai-app/ directory:  python -m tests.alt_provider_config_test
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.llm import _validate_alt_provider_config  # noqa: SLF001 -- deliberately testing the internal guard directly


def _reset():
    settings.llm_provider = ""
    settings.llm_base_url = ""
    settings.llm_api_key = ""
    settings.llm_model = ""


def _expect_raises(label: str, **overrides) -> None:
    _reset()
    for k, v in overrides.items():
        setattr(settings, k, v)
    try:
        _validate_alt_provider_config()
        print(f"FAIL (did not raise): {label}")
        raise SystemExit(1)
    except RuntimeError as exc:
        print(f"OK, raised RuntimeError: {label}\n    -> {exc}")


def main() -> None:
    print("--- alt_provider_active is False when LLM_PROVIDER is blank (default/current .env state) ---")
    _reset()
    assert settings.alt_provider_active is False
    print("OK: alt_provider_active == False with LLM_PROVIDER blank\n")

    print("--- Fail-loud cases (must raise RuntimeError, no network touched) ---")
    _expect_raises(
        "LLM_PROVIDER set, everything else blank (the exact scenario the user asked to test: blank LLM_API_KEY)",
        llm_provider="openai",
    )
    _expect_raises(
        "LLM_PROVIDER set, only LLM_API_KEY blank",
        llm_provider="openai",
        llm_base_url="https://example.invalid/v1",
        llm_model="some-model",
    )
    _expect_raises(
        "LLM_PROVIDER set, only LLM_BASE_URL blank",
        llm_provider="openai",
        llm_api_key="sk-fake",
        llm_model="some-model",
    )
    _expect_raises(
        "LLM_PROVIDER set, only LLM_MODEL blank",
        llm_provider="openai",
        llm_api_key="sk-fake",
        llm_base_url="https://example.invalid/v1",
    )
    _expect_raises(
        "LLM_PROVIDER set to an unsupported value, all else filled in",
        llm_provider="anthropic",
        llm_api_key="sk-fake",
        llm_base_url="https://example.invalid/v1",
        llm_model="some-model",
    )

    print("\n--- Fully-configured case must NOT raise (still no network touched -- validation only) ---")
    _reset()
    settings.llm_provider = "openai"
    settings.llm_api_key = "sk-fake"
    settings.llm_base_url = "https://example.invalid/v1"
    settings.llm_model = "some-model"
    _validate_alt_provider_config()
    print("OK: fully-configured alt provider passes validation without raising")

    _reset()
    print("\nALL CASES PASSED. (settings reset to blank -- matches current real .env state)")


if __name__ == "__main__":
    main()
