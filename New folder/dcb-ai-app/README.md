# dcb-ai-app — DCB NL2SQL chatbot

FastAPI + Gemini NL2SQL chatbot over `../DCB_Star_Schema/dcb_star_schema.sqlite`
(read-only, never modified). See `../CLAUDE.md` for the full project
handoff — this README is just how to run this one folder.

## Setup

```bash
pip install -r requirements.txt
cp ../.env.example ../.env   # .env lives at the PROJECT root, not here -- then fill in GEMINI_API_KEY / GEMINI_MODEL
```

## Run the server

```bash
uvicorn app.main:app --reload
```

- `POST /api/ask` — SSE streaming endpoint (the one a real UI should use).
- `POST /api/ask_once` — plain JSON, same pipeline, easier for scripts.
- `GET /health` — liveness check.

## Run the tests

```bash
python -m tests.dry_run_no_llm            # no API key needed
python -m tests.pipeline_mock_test        # no API key needed (LLM mocked)
python -m tests.double_count_guard_test   # no API key needed -- code-level Division_Total guard
python -m tests.eval_queries               # needs a real GEMINI_API_KEY in .env
```

`tests/eval_queries.py` runs the 10 questions from
`../DCB_Semantic_Layer/example_queries.sql` (+ 2 adversarial questions)
through the real pipeline and writes `tests/eval_report.md`.
