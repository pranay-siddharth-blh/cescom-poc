# dcb-ai-app — CESC DCB assistant (typed and voice), dashboard and reports

FastAPI app over the **locked** star schema (`../DCB_Star_Schema/dcb_star_schema.sqlite`) and the **DCB mart**
(`../DCB_Mart/dcb_mart.sqlite`, built from the star schema by `../DCB_Mart/build_mart.py`). Both are opened read-only.

This README covers only how to run this folder. For everything else:
- `../HANDOFF.md` (v3): the complete handoff, including setup on a new machine (§5);
- `../CLAUDE.md`: the START HERE summary, then §12–§17.

## Run (PowerShell)

```powershell
cd "<path>\New folder\dcb-ai-app"
.\.venv\Scripts\python -m uvicorn app.main:app --port 8010
```

Open **http://127.0.0.1:8010**: login (any name) → dashboard. `/chat` is the assistant, with the round **voice** button
beside Ask. Use 127.0.0.1, not the machine's IP: the browser only allows the microphone on a secure address.

**First time on a new machine** (Python 3.11):
```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

**Settings live in the project-root `../.env`** (see `../.env.example`):
- **The model:** the internal gateway, reachable only over the VPN (`SQL_VERIFY_MODEL`, `SQL_VERIFY_BASE_URL`,
  `SQL_VERIFY_API_KEY`). There is no Gemini or other provider.
- **Voice:** `ASR_*` and `TTS_*` on the same gateway. `TTS_TASK_TYPE=Base` with
  `TTS_REF_AUDIO_PATH=voices/officer_serena.wav` clones the officer voice.
- **Off the VPN:** the deterministic routes still answer almost every typed question; the rest, and voice, give a
  clear message.

## Main routes
- **Pages:** `GET /`, `/login`, `/dashboard`, `/insights`, `/reports`, `/about`, `/tenders`, `/chat`.
- **`POST /api/ask`:** SSE with `status` → `followup` → `spoken` (voice mode) → `chart` → `answer_chunk`… → `table` →
  `done`.
- **`POST /api/ask_once`:** JSON, the same pipeline. Body `{question, previous?, mode: "text" | "voice"}`.
- **Voice:** `/api/voice/config`, `/api/voice/transcribe` (WAV in), `/api/voice/speak` and `/api/voice/speak/stream`
  (audio out), `/api/voice/filler`, `/api/voice/health`.
- **Excel reports:** `/api/mis-report[/view]?subdivision=&fy=`, `/api/answer-report/{report_id}[/view]`.
- **`GET /health`:** liveness, data period, model configured, `voice.speech_in` / `speech_out`.
- **`GET /docs`:** only when `DCB_SHOW_TECHNICAL_DETAILS=true`.

## Rebuilding the mart (only after editing `../DCB_Mart/metadata/*`)

Stop the server first (Windows can't replace an open SQLite file), then:
```powershell
.\.venv\Scripts\python ..\DCB_Mart\build_mart.py            # 21 checks; writes nothing unless all pass
.\.venv\Scripts\python ..\DCB_Mart\verify_against_raw.py    # optional: re-checks data facts from the raw workbook
```

## Tests (results of 2026-10-07 in `../CLAUDE.md` §17.3; reports go to `../reports/`)

```powershell
.\.venv\Scripts\python -m pytest tests -q -p no:cacheprovider     # offline: 474 tests
.\.venv\Scripts\python tests\run_bank.py --mode text              # 334-question stress bank, typed  -> STRESS_TEST_QUESTIONS.md
.\.venv\Scripts\python tests\run_bank.py                          # the same in voice mode          -> STRESS_TEST_QUESTIONS_VOICE.md
.\.venv\Scripts\python tests\run_followup_stress.py               # 802 follow-up turns             -> FOLLOWUP_STRESS_TEST.md
.\.venv\Scripts\python tests\run_voice_stress.py                  # 660 spoken trials (VPN)         -> VOICE_STRESS_TEST_QUESTIONS.md
.\.venv\Scripts\python tests\run_voice_followup_stress.py         # 590 spoken turns (VPN)          -> VOICE_FOLLOWUP_STRESS_TEST.md
.\.venv\Scripts\python tests\make_question_docs.py                # chatbot_test_questions.md, chatbot_sample_questions.md
.\.venv\Scripts\python tests\make_demo_questions.py               # DEMO_TEST_QUESTIONS.md (proven questions for a demo)
.\.venv\Scripts\python tests\run_stress.py --base http://127.0.0.1:8010   # live server: 139 questions -> stress_test.md
.\.venv\Scripts\python -m tests.run_regression                    # live model: 49 questions -> regression_kpcl_port.md
```

**Notes on the runs:**
- **In-process:** the bank, follow-up and spoken runners need no server running.
- **Spoken runners:** they need the VPN and the Windows English (India) voices (Ravi, Heera). `--wavs-only` records the
  audio without the VPN.
- **Older scripts** still run: `python -m tests.dry_run_no_llm`, `tests.pipeline_mock_test`,
  `tests.double_count_guard_test`, `tests.definitional_question_test`.
