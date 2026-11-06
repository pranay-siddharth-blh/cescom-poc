# Cescom DCB vs RDPR and KPCL: architecture comparison and improvements

**Date:** 5 October 2026.

**Sources:**
- RDPR POC (`D:\RDPR POC`) and KPCL POC (`D:\KPCL POC`). Both were only read; nothing in them was changed.
- Cescom DCB POC, `New folder/dcb-ai-app`.

## 1. Layer by layer

| Layer | RDPR / KPCL | Cescom before | Verdict |
|---|---|---|---|
| Source → staging → core | Staged warehouse build. Sources hashed, staging flags (never delete), core with CHECK constraints | Locked 4-stage pipeline (staging → star schema) with reconciliation (0.0% deviation), §11 fixes | On a par |
| Physical mart | Pre-summed `mart_*` tables, the only layer the app reads | `DCB_Mart/dcb_mart.sqlite`: family / slab / booklet / sub-division facts | On a par |
| Build checks + publish | Named checks with golden figures; a new build goes live only if every check passes | 21 named checks; built to `.building.sqlite` and swapped in with `os.replace` only on PASS | On a par |
| Semantic contract | Metric registry with synonyms, column docs, capability manifest, known issues | 43-metric registry (298 synonyms), `semantic_docs`, `capability_manifest`, `known_issues`, tariff booklet | On a par |
| Reviewed example SQL | 38 (RDPR) | 20 | Smaller, but it covers the deterministic routes; not a gap in practice (live regression 49/49) |
| Read-only DB guard | `mode=ro`, `query_only`, authorizer, bound parameters | Same | On a par |
| SQL guard | Table allowlist, grain guards, ratio guards, NULLS LAST | Same, plus the Division_Total double-count guard | On a par |
| Deterministic routes | Verified → glossary → knowledge → capability → reports → breakdown → metric → model | Verified → tariff booklet → glossary → knowledge → capability → slabs → reports → metric → model | On a par (Cescom adds the booklet and slab routes) |
| Reviewer | Second model pass, error categories, deterministic veto | Same | On a par |
| **Small talk / abuse** | `small_talk.py`: greetings, thanks, goodbyes, acks; abusive words stripped from real questions | One greeting regex only | **Gap → implemented** |
| **Follow-up questions** | `followup.py`: "and Hassan?", "what about 2023-24?", "and the balance?"; "Taking this as" shown | None; every question stood alone | **Gap → implemented** |
| **False premises** | `premise.py`: stated figures recomputed, correction leads the answer | None | **Gap → implemented** |
| **Answer writer** | Sentences first: the range, latest against first, leader with share and runners-up; crore / lakh in text; notes separated | Terse ("X went from … highest … lowest"); the exact rupee amount on every figure; raw slab descriptions; unlabelled caveats | **Gap → implemented** |
| **Charts** | Readable axes and tooltips, unit-aware ticks | 45°-tilted labels, long category names crammed under bars, percentage lines pinned to zero | **Gap → implemented** |
| Voice mode | ASR / TTS, spoken guards | — | Not ported (needs the gateway's ASR / TTS keys and backend; awaiting decision) |

## 2. Improvements implemented

**1. Small talk and abuse** (`app/small_talk.py`, ported):
- If the whole message is small talk, it gets a friendly fixed reply.
- Abusive words are taken out of a real question before anything else; the question is answered and the words go
  nowhere.
- Sub-division names are never read as abuse.

**2. Follow-up questions** (`app/followup.py`, written for DCB vocabulary):
- **Understood:** sub-divisions with aliases ("hn pura"), the whole division, fiscal years and "this / last year", tariff
  codes (LT-4(a)) and the 298 registry phrases.
- **Rewrites:** "and Konanur?", "what about 2023-24?", "and the demand?", "for the whole division?" and "and HT-2?" become
  the whole question.
- **After a ranking:** "and Konanur?" asks for Konanur's own figure.
- **Plumbing:**
  - the API takes `previous`;
  - the stream sends a `followup` event, and `done` carries `asked_as` and `context`;
  - the chat shows "Taking this as: …" and clears the context on New conversation.

**3. False premises** (`app/premise.py`, ported):
- Rupee, percentage and installation-count claims are recomputed through the reviewed metric lookup.
- Thresholds ("more than 90%"), years and tariff codes are ignored.
- Example: "Of the Rs 500 crore demand …" opens with **"The question says ₹500 crore, but the data shows ₹354.16
  crore."**

**4. Answer writing** (`app/answer.py`, `reports.py`, `slab_lookup.py`):
- **Trends:** "Across the 11 years from FY 2015-16 to FY 2025-26, collection efficiency ranged from 92.36% in FY 2018-19 to
  119.37% in FY 2016-17. The latest, 93.18% in FY 2025-26, is 5.64 points lower than the first."
- **Rankings:** the leader with its share of the total and the runners-up ("together the top three are …").
- **Comparisons over years:** "LT-1 grew 14.3 times; HT-2 grew 6.0 times. LT-1 was higher in 6 of the 11 years."
- **Slab segments:** short names ("Domestic is 3.78 times commercial"), plus a compact "What was counted" list. Slab
  descriptions are tidied ("Comme. Ltng ,heating" → "Commercial Lighting, heating").
- **One polish for every route:**
  - the exact rupee amount is kept once, in the headline (still checked by the stress test); other paragraphs show
    crore / lakh unless the question asks for the exact figure (the table always has every exact figure);
  - caveats are labelled **Note**, and the chat draws them as quiet note boxes.

**5. Charts** (`static/ux.js`):
- **Horizontal bars:** a category chart with more than 6 names, or long names, becomes horizontal bars; the card grows to
  fit one bar per name.
- **Fiscal-year axes:** never tilted; shortened to 15-16 when crowded.
- **Long names:** cut to fit, with the full name in the tooltip title.
- **Value axis:** at most 6 ticks in ₹ Cr / %, with grace above the tallest bar.
- **Percentage lines:** a line of percentages with a narrow range zooms to it instead of hugging zero.
- **Other:** a pointer guide line on both orientations, rounded legend markers and capitalised titles.

## 3. Verification (real runs, 5 October 2026)

- **Offline pytest:** **315 passed** (284 before, plus 31 new in `tests/test_conversation.py`). Two existing expectations
  were updated for the intended changes: the `hello` route is now `small_talk:greeting`, and the trend wording changed.
- **Live stress bank** (`tests/run_stress.py`, server on port 8010): **139/139**, unchanged.
- **Live regression** (`python -m tests.run_regression`, model gateway): **49/49 OK** (previous 49), 0 worse.
- **Headless Edge, chat:**
  - a 16-category answer draws as horizontal bars;
  - year axes are flat, and the percentage trend zooms to its range;
  - follow-ups show "Taking this as";
  - notes render as note boxes;
  - zero console errors.
