# CESC Holenarasipura DCB Assistant — Project Handoff (v3, 2026-10-07)

**This supersedes HANDOFF v2** (which described only the 4-stage data pipeline). v3 is the complete, portable handoff
for the whole project, as it stands on 2026-10-07:
- the locked data pipeline;
- the checked data mart;
- the chatbot (typed and **voice**);
- the dashboard pages;
- the test suites with their latest live results;
- how to set it up on another machine;
- how to reuse it for another project.

Read this top to bottom once. **`CLAUDE.md`** (same folder) is the working memory: §0 rules, then a dated log of every
pass with root causes and verbatim results. Its "START HERE" section mirrors §2–§8 of this file. Pipeline internals
are only here (§3).

---

## 1. What this project is

**Data:** CESC (Chamundeshwari Electricity Supply Corporation, Karnataka) Demand-Collection-Balance (DCB) statements
for **Holenarasipura Division**, fiscal years **2015-16 to 2025-26**:
- one raw workbook, `HNPURA DIVISION DCB FY 2015 TO 2026.xlsx`, with one sheet per year;
- for every tariff category in every sub-division: installations, load, consumption, opening balance, demand,
  collection (cash and adjustment), closing balance.

**Places:**
- sub-divisions **Arkalgud, Hangarahalli, Holenarasipura, Ramanathapura**;
- **Saligrama**, which closed after 2017-18;
- **Konanur section**;
- the division total;
- `Division_Office`, an administrative unit that exists 2020-21 to 2023-24 with all-zero figures.

**What was built, in order:**
1. **A 4-stage data pipeline** (locked): workbook → split sheets → staging DB → typed star schema → semantic layer.
2. **`DCB_Mart/`:** query-ready tables built from the star schema, with 21 build checks. It holds the metric registry,
   capability manifest, known issues, knowledge base and the CESC tariff booklet.
3. **`dcb-ai-app/`:** a FastAPI app.
   - **Assistant:** typed and voice, English and Kannada. Deterministic routes come first, and the model is only the
     fallback.
   - **Pages:** dashboard, insights, reports, about & glossary, tenders.
   - **Downloads:** Excel MIS and answer reports.
4. **Test suites:** at the length and in the style of the two reference projects (KPCL POC and RDPR POC), with figures
   recomputed from the locked star schema.

**Reference projects** (read-only, never edited):
- `D:\KPCL POC`: the deterministic-route ladder, mart, reviewer and Excel reports were ported from it.
- `D:\RDPR POC`: the front-end design, follow-ups, premise check, small talk and voice mode were ported from it.
- `D:\Meghalaya_voice`: the origin of the voice stack.

---

## 2. Folder layout (everything that matters)

```
New folder/                                   <- the project root; zip THIS folder to move the project
├── CLAUDE.md                                 working memory: rules (§0), dated log §1-§17, START HERE section
├── HANDOFF.md                                this file
├── ARCHITECTURE_WALKTHROUGH.md               plain-language walkthrough of the ORIGINAL (Gemini-era) design; historical
├── .env                                      real settings + keys (git-ignored; never share)
├── .env.example                              every setting, placeholders only
├── HNPURA DIVISION DCB FY 2015 TO 2026.xlsx  raw source workbook
├── CESCOM Tariff Categories.pdf (+ transcription.md)   the CESC tariff booklet FY2025-26..27-28 (source of tariff_booklet.json)
├── Delegation of Financial Power of CESC*.pdf          CESC DoP (used by the tenders page)
├── DCB Test Queries.pdf, Software Details.xlsx         client-supplied material
├── DCB_Segregated_By_SubDivision/            LOCKED stage 1
├── DCB_Staging_DB/                           LOCKED stage 2
├── DCB_Star_Schema/                          LOCKED stage 3 (dcb_star_schema.sqlite = the database of record)
├── DCB_Semantic_Layer/                       LOCKED stage 4
├── DCB_Mart/                                 built (not locked): build_mart.py, dcb_mart.sqlite, metadata/
├── reports/                                  every test report and question document (§7)
├── test_questions.json, run_frontend_tests.py, test_run_results_*/   the older 49-question UI regression (history)
└── dcb-ai-app/
    ├── README.md                             how to run this folder
    ├── requirements.txt                      fastapi, uvicorn, pydantic(-settings), httpx, pyyaml, openpyxl, pypdf, pytest, numpy
    ├── .venv/                                the virtual environment (recreate on a new machine, §5)
    ├── voices/officer_serena.wav             the officer voice the text-to-speech clones
    ├── app/                                  the application (modules in §4)
    │   └── static/                           pages + chat.js, ux.js, report-viewer.js, voice/ (voice panel), vendor/vad/ (speech detector)
    └── tests/                                pytest suites, banks, runners, report generators (§7)
```

---

## 3. The data pipeline (4 locked stages) — internals

**Rule:** the four `DCB_*` pipeline folders are **locked**. Never modify or regenerate anything in them; open the
SQLite files read-only only.

There are two recorded, deliberate exceptions, both explicitly approved by the project owner (`CLAUDE.md` §0):
- additive examples appended to `DCB_Semantic_Layer/example_queries.sql`;
- the §11 pipeline fix of category / rollup classification, with a backup taken first.

### Stage 1 — `DCB_Segregated_By_SubDivision/`
11 `.xlsx` files (one per fiscal year), each split into one sheet per sub-division / section exactly as printed. No
reshaping; for a human to eyeball.

### Stage 2 — `DCB_Staging_DB/dcb_staging.sqlite`
- **`staging_dcb`:** 11,712 rows × 86 TEXT columns, a deliberately "dumb" landing zone.
- **Row types:** every row is tagged `line_item`, `sub_total`, `lt_ht_total`, `misc_total`, `grand_total`,
  `annotation` or `spillover_fragment`, so real data is never mixed with printed subtotals.
- **`staging_column_reference`:** documents which raw column produced each field.

### Stage 3 — `DCB_Star_Schema/dcb_star_schema.sqlite`
| Table | Rows | What it is |
|---|---|---|
| `dim_fiscal_year` | 11 | one per year, integer `start_year` / `end_year` |
| `dim_subdivision` | 8 | `Division_Total`, the 5 sub-divisions incl. Saligrama, `Konanur` (a section), `Division_Office` |
| `dim_tariff_category` | 139 | every distinct (tariff_code, description) pair |
| `fact_dcb` | 5,488 | one row per (year, sub-division, tariff category, description), REAL; `SUM()` is always safe |
| `fact_dcb_rollups` | 2,232 | the report's own printed Sub Total / LT-TOTAL / HT-TOTAL / LT+HT / MISC / GRAND TOTAL rows — **never** sum with `fact_dcb` |
| `reconciliation_report` | 292 | `fact_dcb` summed vs the printed grand total, per block per metric: **0.0 % deviation everywhere** |

The schema also has two views:
- `fact_dcb_subdivisions`: excludes the division rollup;
- `fact_dcb_grand_totals`: the printed grand totals.

### Stage 4 — `DCB_Semantic_Layer/`
- glossary and tariff-code glossary;
- grounding rules (no double-counting, `fiscal_year` is text);
- derived-metric formulas;
- worked NL→SQL pairs (`example_queries.sql`);
- `semantic_layer.json` for the prompt, and `semantic_layer.md` for people.

### The 11 messy-Excel bugs fixed while building it (don't re-litigate them)
1. **3-level header drift between two eras.** 2015-16 to 2017-18 put the Revenue / Interest / Tax / FAC sub-label one
   row lower. Fields are matched on combined header text, not column position.
2. **Forward-fill bled sub-headers** across unrelated columns (e.g. "KVA" into DC installations). Fixed with a
   group-aware forward-fill.
3. **An orphan mini-table** (a lakh-denominated monthly summary) is embedded far to the right in some sheets; it is
   excluded.
4. **A blank spacer column** was mistaken for a duplicate FAC column; forward-fill now resets on columns blank in all 3
   header rows.
5. **Character corruption:** `ag`→`AF` (VillAFe, AverAFe, AFriculture, BhAFyajyothi), `C`→`B` (ABtually, FAB), and a
   `Collec tion` typo. Fixed by an explicit typo list.
6. **Tariff code not re-printed** on wrapped rows (~1,046 rows); recovered by forward-fill, flagged
   `tariff_category_forward_filled`.
7. **"LT+HT - TOTAL" vs "GRAND TOTAL (LT+HT+Misc.)"** were conflated; they are distinct rows (the difference is
   `misc_total`).
8. **A grand-total continuation row** forward-filled a fake line item in 2025-26 (+90 % installations). A continuation
   row now needs a text description.
9. **Whitespace and residual corruption** created duplicate tariff categories; whitespace is collapsed and every
   instance of bug 5 swept.
10. **More `ac`→`AB` corruption** (ABcess, TrABtion), fixed the same way.
11. **Floating-point noise** in decimal misc-revenue codes (61.904700000000005) split one code into two; codes are
    rounded to 4 places.

### Data facts that look like bugs but are not (also in `DCB_Mart/metadata/known_issues.json`, KI-01…KI-20)
- **KI-03 / KI-04 — places:** Saligrama has rows only to 2017-18; `Division_Office` exists 2020-21 to 2023-24 with
  zeros.
- **KI-01 — tariff restructuring:** from 2024-25, domestic and lighting slabs move into LT-1 (CESC tariff booklet).
- **KI-02 — division total:** from 2019-20 the division total exceeds the sum of its sub-divisions, by up to 3.475 %
  (miscellaneous revenue booked only at division level).
- **KI-05 — LT-5 subtotal:** the printed "Sub Total of LT-5" is 0 up to 2023-24; the real total is "LT-5 (a+b)".
- **KI-06 — misc revenue:** `demand_revenue_subtotal` ≠ fixed + others + energy for the misc codes (61.xxxx / 62.xxxx),
  which are flat fees.
- **KI-09 — collection efficiency** can exceed 100 % (arrears collected).
- **KI-11 — a blank consumption cell:** Holenarasipura's 2023-24 grand-total consumption cell is blank; the app sums
  the sheet's rows (127,582,101 units).
- **KI-13 — FPPCA refund:** FPPCA was a refund in 2021-22.
- **KI-15 / KI-18 — confirmed by CESC:** DC = direct connection; adjustment collections = the Government subsidy
  (Gruha Jyothi and others).
- **KI-19 — AT&C loss:** computed on the sheet's own billing efficiency (billed / live), because the data has no
  energy input.

---

## 4. The application (`dcb-ai-app/`)

### Request ladder (deterministic first; the model is the fallback)
Implemented in `app/pipeline.py`; every stage is timed and streamed live to the page.

| Layer | What happens | Module(s) |
|---|---|---|
| L0 | Small talk; abuse stripped. Follow-up rewrite ("and Konanur?" → the whole question). Kannada → English. | `small_talk.py`, `followup.py`, `kannada.py` |
| L1 | Screen: empty, too long, code, prompt injection, harmful, write request, personal data → refusal | `guardrails.py` |
| L2 | Greeting / identity; reviewed SQL | `verified_queries.py` |
| L2 | **Tariff booklet** (rates, rules, "which category is a homestay") | `tariff_booklet.py` |
| L2 | **Capability notes** (monthly, consumers, losses, other divisions, payment modes, staff) | `capability.py` |
| L2 | Glossary and **knowledge base** (EN / KN) | `knowledge.py` |
| L3 | Router: sub-divisions by alias, years in any form, families, slabs, measures (registry synonyms + typo repair), intents. Unknown places / tariffs and out-of-range years are refused by name. | `router.py` |
| L3b | Slabs: exact tariff slabs, groups ("IP sets", "domestic"), shares | `slab_lookup.py` |
| L4 | Reports: rankings, comparisons, trends, splits, summaries | `reports.py`, `scope.py` |
| L5 | Metric lookup: 1–3 measures × one place × one year | `metric_lookup.py` |
| L6–L11 | **Model path** (only the leftovers), in five steps below. | `prompts.py`, `llm.py`, `sql_safety.py`, `db.py`, `answer.py`, `review.py` |
| L12 | Premise check: a figure stated in the question is checked against the data | `premise.py` |

The model path, in order:
1. One call returns `{answerable, sql, answer_template, chart_data}`.
2. **SQL guard:** read-only, allowed tables, Division_Total and grain guards.
3. **Execute:** a read-only file, an authorizer, a timeout and a row cap.
4. **Result guards** on what came back.
5. **Answer writer**, then an independent **reviewer** with a deterministic veto.

**Answers are written deterministically** (`answer.py`):
- a bold headline with the place and year;
- rupee figures in crore with the exact amount in brackets;
- % throughout;
- notes for defaults and caveats;
- a table, a chart and a downloadable Excel report.

**Model:** only the internal OpenAI-compatible gateway, reached over the VPN (`SQL_VERIFY_*`; model `qwen4-deploy`).
Without it, almost every question still works; the rest get a clear message.

### Pages (`app/static/`, RDPR/KPCL design in the CESC palette — colours only from the CESC websites)
| Page | URL | Notes |
|---|---|---|
| Login | `/login` | any non-blank username (no password check; HMAC-signed cookie) |
| Dashboard | `/dashboard` (`/` redirects here once signed in) | KPIs, trends, sub-division drill-down, Excel MIS download |
| Insights | `/insights` | filter row and 7 insight cards |
| Reports | `/reports` | series builder with charts |
| About & glossary | `/about` | how it answers, Terms / Tariff categories / Booklet rules |
| Tenders | `/tenders` | GeM-format drafting against CESC DoP, Bid-Assist-style PDF analyser |
| Assistant | `/chat` | typed + **voice** (round button beside Ask), follow-ups, "Taking this as", reports |

### Voice mode (CLAUDE.md §17)
1. **Browser:** Silero VAD (vendored in `static/vendor/vad/`, checksums in `VERSIONS.txt`) records the question.
2. **`POST /api/voice/transcribe`:**
   - **Speech-to-text:** qwen3-asr. Its prompt, `ASR_CONTEXT`, names the subject, the DCB terms and **the division's six
     places** (KPCL style; measured better than leaving them out).
   - **Guards:** silence, fragments (follow-ups and greetings are kept), too long, other script (a short mixed-script
     transcript is spelt out in Latin letters first), the prompt echoed back, and the same question repeated.
   - **Clean-up** (`spoken.clean_transcript_ex`): names by sound and consonant skeleton, spoken years, spoken tariff
     codes, acronyms, the mishearings found in the live tests.
   - **"Did you mean …?"** only for a truly ambiguous year or tariff homophone.
3. **The normal pipeline in voice mode:** `spoken.py` writes 1–3 sentences from the verified answer. Under the voice
   guardrails (`voice_guard.py`):
   - a possible mishearing gets "I heard … say it again";
   - an unchecked model answer is not read out;
   - every spoken figure must be a screen figure with its unit;
   - the place and year are read back.
4. **`/api/voice/speak/stream`:** Qwen3-TTS clones `voices/officer_serena.wav` (24 kHz PCM, persona ≤ 500
   characters). Fillers are cached at startup.
5. **Security:** CSP `'wasm-unsafe-eval'` only; `Permissions-Policy: microphone=(self)`; an audio size cap; a voice
   rate limit.

**The browser only allows the microphone on a secure address**, so open the page as `http://127.0.0.1:8010`, not via
the machine's IP.

### Main routes
- **Pages:** `/`, `/login`, `/dashboard`, `/insights`, `/reports`, `/about`, `/tenders`, `/chat`, `/health` (data
  period, model configured, `voice.speech_in` / `speech_out`).
- **Ask:**
  - `POST /api/ask`: SSE with `status` → `followup` → `spoken` (voice) → `chart` → `answer_chunk` → `table` → `done`;
  - `POST /api/ask_once`: JSON; body `{question, previous?, mode: "text"|"voice"}`.
- **Voice:** `/api/voice/config`, `transcribe`, `health`, `speak`, `speak/stream`, `filler`.
- **Data and reports:** `/api/dashboard_summary[/{sub}]`, `/api/insights_summary`, `/api/reports_options`,
  `/api/reports_series`, `/api/glossary`, `/api/mis-report[/view]`, `/api/answer-report/{id}[/view]`, `/api/tender/*`.

---

## 5. Setting it up on another machine (Windows)

1. **Copy the whole `New folder/`**, not just `dcb-ai-app/`: the app reads `../.env`, `../DCB_Star_Schema`,
   `../DCB_Semantic_Layer` and `../DCB_Mart` by relative path. Copy `.env` separately and securely, or recreate it from
   `.env.example`; it holds the gateway keys.
2. **Python 3.11:**
   ```powershell
   cd "<path>\New folder\dcb-ai-app"
   python -m venv .venv
   .\.venv\Scripts\python -m pip install -r requirements.txt
   ```
3. **`.env`** (project root): set `SQL_VERIFY_MODEL / _BASE_URL / _API_KEY`, `ASR_*`, `TTS_*` (`TTS_ENABLED=true`,
   `TTS_TASK_TYPE=Base`, `TTS_REF_AUDIO_PATH=voices/officer_serena.wav`) and `SESSION_SECRET`. The gateway is
   `https://10.48.242.4/openai/v1`, **reachable only on the VPN**.
4. **Run:**
   ```powershell
   cd "<path>\New folder\dcb-ai-app"
   .\.venv\Scripts\python -m uvicorn app.main:app --port 8010
   ```
   Open **http://127.0.0.1:8010**: login (any name) → dashboard; the assistant is at `/chat`. Port 8010 is used because
   8000 is often taken by other POCs.
5. **Rebuilding the mart** (only after editing `DCB_Mart/metadata/*`): stop the server first, then
   `.\.venv\Scripts\python ..\DCB_Mart\build_mart.py`. It runs 21 checks and writes nothing unless all pass. Then
   re-run pytest; the integrity test needs the rebuild.
6. **Spoken tests only:** install Windows' **English (India)** speech voices (Settings → Time & language → Speech),
   which provide Ravi and Heera. `tests/make_wavs.ps1` uses them.

---

## 6. Rules to keep (also CLAUDE.md §0 and memory)
1. **The four `DCB_*` pipeline folders are locked.** Read-only always.
2. **`D:\KPCL POC`, `D:\RDPR POC` and `D:\Meghalaya_voice` are strictly read-only references.** Read them; copy from
   them only with the owner's permission. Never edit, run code from, or import their modules (importing writes
   `__pycache__` there). Copied so far, with permission: the RDPR voice front end, VAD files, officer voice clip, and
   the gateway / voice `.env` lines.
3. **UI:** the RDPR/KPCL layout in the CESC palette (navy `#072577`, gold `#ffcd00`, red `#d82221`). Colours come only
   from the CESC websites.
4. **No "done" without verbatim output.** Every figure in a test is recomputed from the locked star schema by SQL that
   is independent of the chatbot.
5. **Long test suites:** run them in the background and report briefly. The owner stops long blocking commands.

---

## 7. Tests and the latest verified results (2026-10-07, live on the VPN, final code)

| Suite (from `dcb-ai-app/`) | Report (`reports/`) | Result |
|---|---|---|
| `python -m pytest tests -q -p no:cacheprovider` (offline; 159 of them voice) | — | **474 passed** |
| `python tests/run_bank.py --mode text`: 334-question stress bank (RDPR 318, KPCL 213) | `STRESS_TEST_QUESTIONS.md` | **334/334** |
| `python tests/run_bank.py`: the same bank in voice mode | `STRESS_TEST_QUESTIONS_VOICE.md` | **334/334** |
| `python tests/run_followup_stress.py`: 152 conversations, 802 turns | `FOLLOWUP_STRESS_TEST.md` | **802/802** (fresh seeds 4242, 99 also 100 %) |
| `python tests/run_voice_stress.py`: 660 spoken trials | `VOICE_STRESS_TEST_QUESTIONS.md` | **634/660 (96.1 %)**; every miss passes typed |
| `python tests/run_voice_followup_stress.py`: 590 spoken turns | `VOICE_FOLLOWUP_STRESS_TEST.md` | **549/590 (93.1 %)**; every miss passes typed |
| `python tests/make_question_docs.py` | `chatbot_test_questions.md`, `chatbot_sample_questions.md` | 46/46 answered, 10/10 refused |
| `python tests/make_demo_questions.py` | **`DEMO_TEST_QUESTIONS.md`** | 310 proven (typed + voice mode + both voices), 23 demo picks |
| `python tests/run_stress.py --base http://127.0.0.1:8010` (live server) | `stress_test.md` | **139/139** |
| `python -m tests.run_regression` (live model) | `regression_kpcl_port.md` | **49/49** |

The two reference projects' spoken results, for comparison: RDPR 92.5 % (spoken stress) and 91.3 % (spoken follow-ups);
KPCL 99.2 % (spoken stress).

**Run order for a full re-verification (VPN on):**
1. pytest;
2. `run_bank.py --mode text`, then `run_bank.py`;
3. `run_followup_stress.py`, then `run_voice_stress.py` and `run_voice_followup_stress.py` (they pre-record WAVs to
   `%TEMP%\cescom_voice_wavs`; `--wavs-only` works without the VPN);
4. the doc generators;
5. start the server on 8010, then `run_stress.py` and `run_regression`.

**How the banks are built:**
- **Banks:** `tests/stress_bank.py` (139 typed) is extended by `tests/voice_bank.py` (to 334), and
  `tests/followup_bank.py` holds a seeded random walk plus hand-written conversations.
- **Checks:** `N` (figure), `T` (text), `REFUSE`, `ANSWER`, `name`.
- **The spoken runners re-ask every failure as typed**, to separate a hearing miss from a pipeline miss.

---

## 8. Open items / known limits
- **Data scope:** yearly figures only (monthly questions are refused with a note). No consumers, feeders, energy input
  or other divisions.
- **Voice hearing:** an unclear spoken name can be snapped to the wrong one of the six places; the place is always read
  back. Residual spoken misses are all hearing ("Konanur" → "Continuous").
- **Kannada:** questions are understood (deterministic rewrite) and knowledge answers come back in Kannada. Spoken
  output is English.
- **Hosting:** stays on local SQLite; a Postgres port is mostly DDL, and not started.
- **MNR replacement tracking:** on hold (owner's decision).

---

## 9. Reusing this for another project (checklist)
1. **Keep the shape:** locked pipeline → a checked mart with hand-written metadata (metric registry, capability
   manifest, known issues, knowledge base) → the deterministic ladder → the model as fallback → the reviewer.
2. **Replace the vocabulary:**
   - `router._SUB_ALIASES` (places);
   - `DCB_Mart/metadata/*` (measures, capabilities, issues, knowledge);
   - `tariff_booklet.json` (rates);
   - `spoken._PHRASES` / `_ORDINARY` and `main.ASR_CONTEXT` (voice);
   - `config.dcb_voice_instructions` (TTS persona, ≤ 500 characters).
3. **Voice:** name the places in `ASR_CONTEXT` when there are only a few, and leave them out when there are thousands.
   Measure it with `run_voice_stress.py` either way.
4. **Tests:** port `stress_bank.py` / `voice_bank.py` / `followup_bank.py` with SQL against your own locked source.
   Re-ask spoken failures as typed. Generate the demo list from the reports, never by hand.
5. **Read CLAUDE.md §12–§17** for every pitfall already hit:
   - a regex `\b` written through a shell heredoc becomes a backspace character;
   - Windows can't replace an open SQLite file;
   - the TTS persona limit is 500 characters;
   - CSP needs `'wasm-unsafe-eval'` for the VAD;
   - the microphone only works on 127.0.0.1 or https.
