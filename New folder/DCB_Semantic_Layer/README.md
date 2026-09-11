# DCB Semantic Layer

Stage 4 of the pipeline: `DCB_Segregated_By_SubDivision/` →
`DCB_Staging_DB/` → `DCB_Star_Schema/` → **this**. The star schema says
what the tables and columns *are*; this says what they *mean* and how to
query them without getting a wrong answer.

## Files

| File | For |
|---|---|
| `semantic_layer.md` | A person reading the domain vocabulary, tariff-code glossary, and query rules |
| `semantic_layer.json` | A chatbot/LLM prompt-builder loading the same facts as structured data (the module `HANDOFF.md` §6 calls for) |
| `example_queries.sql` | 10 worked NL question → SQL pairs, each actually run against `dcb_star_schema.sqlite` and spot-checked — usable as documentation or as a starting NL2SQL eval set |

## Two more data-quality fixes found while building this

While compiling the tariff-code glossary, found and fixed 2 more
instances of the known text-corruption bug (`ABcess`→`Access`,
`TrABtion`→`Traction`) plus a floating-point precision bug where the same
decimal misc-revenue code (`61.9047`) was splitting into two
`dim_tariff_category` rows depending on which year's cell produced it —
fixed by rounding decimal tariff codes to 4 places in
`DCB_Staging_DB/build_staging.py`. `dim_tariff_category` is now 139 rows
(down from 141 at the end of the star-schema refinement pass);
reconciliation re-checked and still exact (0.0%).

## Next step

This is the last piece the original `HANDOFF.md` called for before the
actual chatbot. From here: FastAPI + Gemini + Neon Postgres (or a local
equivalent), with `semantic_layer.json` loaded into the system prompt
alongside the schema DDL.
