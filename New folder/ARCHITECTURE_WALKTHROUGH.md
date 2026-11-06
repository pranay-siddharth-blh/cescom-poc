# CESCOM Holenarasipura DCB Chatbot — Full Architecture Walkthrough

> **Historical: the original 2026-09 design, written when the model was Gemini.** The current architecture is in
> **HANDOFF.md §4** and **CLAUDE.md START HERE, §12 and §17**:
> - the model is the internal VPN gateway;
> - deterministic routes answer most questions before any model call;
> - a mart, a reviewer and voice mode were added.
>
> The plain-language explanations below still describe the core idea (read-only data, guarded SQL, answers written in
> code), but the file list and the "Gemini" wording are out of date.

This document explains the entire project, file by file, in the order data actually flows through the system — so you can read it top to bottom and be ready to explain it to your team lead in plain terms.

---

## 1. The big picture, in one paragraph

This is a chatbot that answers questions about CESCOM's Demand-Collection-Balance (DCB) billing figures for Holenarasipura Division — how many installations, what was billed, what was collected, what's still owed, per sub-division and per tariff category, across 11 fiscal years. It has two halves, built in two separate phases, connected by one boundary:

- **The data pipeline ("the archive")** — a 4-stage process that turned one messy raw Excel report into a clean, typed, queryable database. No AI model involved anywhere in this half — pure Excel parsing, reshaping, and reconciliation logic. It is finished, verified, and **locked** — nobody touches it again.
- **The chatbot ("the desk clerk")** — a FastAPI app that takes a plain-English question, writes one SQL query for it using an AI model (Gemini), runs that query against the locked database, and hands back a plain-English answer. Nothing in this half is allowed to write to the archive; it only ever reads from it.

They meet at exactly one boundary: the chatbot reads `DCB_Star_Schema/dcb_star_schema.sqlite` (the finished database) and `DCB_Semantic_Layer/` (a glossary + rulebook written specifically to be handed to an AI model) — read-only, both enforced in code, not just by convention. This is why, across every session of work on the chatbot half, nothing ever had to touch or re-run the data pipeline.

---

## 2. The data layer — where the "knowledge" actually lives (locked, read-only)

```
New folder/
├── HNPURA DIVISION DCB FY 2015 TO 2026.xlsx   ← the raw source, untouched
├── DCB_Segregated_By_SubDivision/              ← stage 1: raw split, human-readable
├── DCB_Staging_DB/dcb_staging.sqlite           ← stage 2: flat, untyped staging table
├── DCB_Star_Schema/dcb_star_schema.sqlite      ← stage 3: the typed database the app queries
└── DCB_Semantic_Layer/                         ← stage 4: the glossary/rulebook for the AI model
```

**`DCB_Star_Schema/dcb_star_schema.sqlite`** is the single most important file in the whole project — it's the only database the live app ever queries. Three dimension tables (`dim_fiscal_year`, `dim_subdivision`, `dim_tariff_category`), one big fact table (`fact_dcb`, 5,488 rows, always safe to `SUM()`), one rollup table (`fact_dcb_rollups`, 2,232 rows — the report's *own* printed subtotals, kept deliberately separate so summing `fact_dcb` never accidentally double-counts a total the report already computed), and a QA table (`reconciliation_report`) proving the two agree exactly: **0.0% average and max deviation across all 292 checks**.

**`DCB_Semantic_Layer/`** is what makes the AI model usable at all. `semantic_layer.json` is a hand-written glossary (what "DCB" / "LT" / "HT" / "DC/MNR" / "FAC" actually mean), a tariff-code glossary (LT-1 = domestic lighting, HT-2 = industrial, etc.), and — most importantly — a `rules` list that exists specifically to stop the AI model from producing a wrong number (never sum `fact_dcb` and `fact_dcb_rollups` together; don't double-count the `Division_Total` rollup row against the real sub-divisions it already sums; `fiscal_year` is text, not a number). `example_queries.sql` holds 10 pre-verified, hand-checked NL question → SQL pairs — these get loaded into *every single prompt*, in full, because one of them (the DC/MNR installations query) demonstrates a COALESCE pattern the AI model has no other way of knowing it needs.

Getting this database right was genuinely hard — the source Excel report has multi-level merged headers that shift position between two different eras, forward-fill bugs that bled one column's header into the next, an embedded orphan mini-table nobody asked for, and real character-corruption typos (`ag`→`AF`, `C`→`B`) scattered through the text. 11 separate bugs like this were found and fixed while building this pipeline — full list in `CLAUDE.md` §4 and `HANDOFF.md` §4. None of that is this document's concern; by the time the chatbot half starts, all of it is already resolved and frozen.

---

## 3. `dcb-ai-app/app/` — the chatbot itself, file by file

| File | What it does | Plain-English job |
|---|---|---|
| `config.py` | Loads `.env` once at startup (the VPN gateway's `SQL_VERIFY_MODEL` / `_BASE_URL` / `_API_KEY`, DB path, timeout, row cap) via pydantic-settings — Gemini was removed (CLAUDE.md §13.6) | The settings drawer — one source of truth, no scattered `os.getenv()` calls |
| `db.py` | Opens the locked database **once**, in read-only mode (`?mode=ro` in the connection URI), and reuses that one connection for every request | The archivist — holds the one key to the archive, and that key physically cannot unlock a write |
| `semantic_layer.py` | Loads `semantic_layer.json`; for each question, does plain keyword/substring matching (no AI, no embeddings) to pull out which sub-division/year/tariff-code the question names, and trims the glossary down to just the relevant entries | The pre-screener — reads your question fast and pulls only the relevant reference material before anyone bothers the AI model |
| `prompts.py` | Assembles everything the AI model gets to see: the hard rules, the database schema, the trimmed glossary, all 10 worked examples, and the question itself | The briefing packet — one clean document handed to the AI model, nothing extra, nothing missing |
| `llm.py` | The **only** place in the whole app that calls Gemini — one call per question, asking for structured JSON back (SQL + a fill-in-the-blank answer template, together, in one shot) | The one phone call to the AI model — and it only ever gets made once per question |
| `sql_safety.py` | Two independent checks on whatever SQL the AI model wrote, *before* it's allowed to run: (1) is this even a safe read-only statement, and (2) even if it's safe, could it silently produce a *wrong* number by double-counting the division-wide total? | The two security guards at the archive door — one checks you're not carrying anything dangerous in, the other checks your math before you leave |
| `models.py` | Defines the exact shape of what the AI model must return (`LLMQueryPlan`) and what the API sends back to a browser (`AskRequest`/`AskResponse`) | The paperwork templates everyone has to use |
| `pipeline.py` | The one function (`answer_question()`) that runs all of the above in order, and afterward fills the answer template from the real query result — **no second AI call** | The assembly line — every request, from every entry point, runs down this exact same line |
| `main.py` | The FastAPI app: defines every URL a browser or script can hit | The front desk |
| `static/chat.html` | The actual chat interface — sidebar, message bubbles, a live response timer, a collapsible "show me the SQL" panel | The waiting room and service counter, visually |

**The design point most worth explaining to your lead directly: there is exactly one AI model call per question, and it does two jobs at once.** `llm.py::generate_query_plan()` asks Gemini for a single structured JSON object containing *both* the SQL query *and* a natural-language answer template with blanks in it (e.g. `"The collection efficiency in {subdivision_name} was {collection_efficiency_pct}%."`). Python fills those blanks in afterward, from the real query result — the AI model never sees the query result and never gets asked to phrase the final sentence. That's one network round-trip removed from every single question, on purpose, because that round-trip is the single biggest thing that would otherwise make the app feel slow.

**The second design point worth explaining: there are two independent safety layers, and they catch two completely different kinds of mistake.** `sql_safety.py`'s first check is generic — is this SQL actually a safe, read-only `SELECT`, with no sneaky semicolon-separated second statement, no `DELETE`/`DROP`/etc.? The *second* check is much narrower and specific to this data: even a perfectly safe, perfectly legal `SELECT` could still produce a *wrong* answer if it sums the raw `fact_dcb` table without excluding the `Division_Total` row — which is already the sum of every real sub-division, so including it too would roughly double the true figure. This second check has nothing to do with security in the usual sense; it exists purely so the app can never silently lie about a number. Both checks fail *closed* — anything they can't positively prove safe gets refused, not guessed at. And underneath both of those, the database connection itself is opened read-only at the OS/SQLite level, so even SQL that somehow slipped past both text-level checks physically cannot write anything.

---

## 4. `dcb-ai-app/tests/` — how all of the above gets proven, not just claimed

| File | Job |
|---|---|
| `questions.py` | The shared list of 10 real questions (mirroring `example_queries.sql`, in the same order) + 2 deliberately adversarial questions (a nonexistent tariff code; an explicit attempt to trigger the double-counting bug) |
| `dry_run_no_llm.py` | Proves the DB layer, the safety layer, and the router all work correctly — without spending an API call. Confirms the read-only connection truly can't be written to, even bypassing the text-level check |
| `pipeline_mock_test.py` | Proves the FastAPI wiring itself (routing, streaming, template-filling) is correct, with the AI model's response faked to a known value — so this test can run without a real API key |
| `double_count_guard_test.py` | 13 hand-built SQL shapes (6 dangerous, 7 safe) run directly against the safety layer, no AI model involved — proves the double-counting guard blocks what it should and never blocks a legitimate query |
| `eval_queries.py` | The real thing — runs all 12 questions through a real Gemini call and a real database query, prints everything verbatim, and writes `eval_report.md` |

This matters because the project's own standing rule (see `CLAUDE.md`'s opening line) is: no "done" claim without real, verbatim proof. Every one of the numbers quoted elsewhere in this document — 93.62% collection efficiency in Arkalgud, Konanur leading at 97.46%, LT-4(a) topping 2023-24 demand — was produced by an actual run of `eval_queries.py`, not typed in by hand.

---

## 5. `CLAUDE.md` and `HANDOFF.md` — the project's memory

`HANDOFF.md` is the deeper, canonical record of the data-pipeline half — every one of the 11 Excel-parsing bugs, exactly how each was found and fixed, and the caveats that are real facts about the data (not bugs) — Saligrama closed as a sub-division after 2017-18, `Division_Office` only exists for four specific years, one revenue-subtotal formula legitimately doesn't hold for two flat-fee tariff codes.

`CLAUDE.md` is the single most important document for continuity on the chatbot half — it's what a fresh session reads first. It contains the full architecture (the same material as this document, in more granular form), every bug found and fixed while building the chatbot (a crash from a format-spec collision, a wrong refusal on a two-tariff-code comparison, a silent all-`NULL` result from a missing worked example, unresolved `{placeholder}` tokens leaking into SQL, and the Division_Total double-counting gap), the current verified test results, honest known limitations, and — critically — a section marked explicitly **NOT STARTED** (a UI redesign that was only discussed, never built) so nobody mistakes discussion notes for finished work.

---

## 6. End-to-end: what happens when you type a question into the chat box

1. Browser sends your question to `POST /api/ask` (or `/api/ask_once` for a script) in `main.py`.
2. `main.py` calls `pipeline.answer_question()` — the one function every entry point shares.
3. `semantic_layer.py` reads your question, pulls out any sub-division/year/tariff-code it recognizes by plain text matching, and trims the glossary down to what's relevant. No AI model involved yet — this step measures at 0.00ms.
4. `prompts.py` assembles the full briefing: the hard rules, the database schema, the trimmed glossary, all 10 worked examples, your extracted entities, and your question.
5. `llm.py` sends that as **one** call to Gemini and gets back structured JSON: a SQL query and a fill-in-the-blank answer template, together.
6. If the model says the question can't be answered, that's the end of the line — it explains why, no SQL ever runs.
7. Otherwise, `sql_safety.py` checks the SQL twice: is it safely read-only, and could it double-count the Division_Total row? Either check failing stops everything right there, with a clear refusal message — no SQL reaches the database.
8. `db.py` runs the query against the one persistent, read-only database connection, capped at 200 rows and an 8-second timeout.
9. `pipeline.py` fills in the answer template's blanks using the real query result — no second AI call.
10. `main.py` sends the finished answer back — as a live SSE stream (status → the SQL that ran → the answer, word by word → timing) if you used the chat UI, or as one plain JSON object if a script called `/api/ask_once`.

**Nowhere in this chain does the AI model see the whole database** — it only ever sees the schema definition and the glossary, never the actual rows, until *after* it has already committed to a SQL query. The query result comes back to Python, not back to the model. That's the same core discipline as retrieval-augmented generation in spirit, even though this system writes SQL rather than searching documents: the model is given just enough structured context to do its one job correctly, and is never shown — and therefore can never leak or misremember — the underlying data itself.

---

## 7. The one-sentence version, if your lead only has 30 seconds

*"We built and verified a clean, typed database from a messy government Excel report first — with an exact, checked reconciliation to the source — then connected a chatbot to it that writes one SQL query per question using Gemini, checked twice before it's allowed to run (once for basic safety, once specifically so it can't double-count the division-wide total), so the system can only ever answer from real numbers it actually queried, never from something the model invented or misremembered."*
