# CESCOM POC

CESCOM Demand-Collection-Balance (DCB) for the Hunsur (HNPURA) division. It has a staging DB, a star schema and a semantic layer, with an AI chat + dashboard app on top.
Everything lives in **`New folder/`**. Start with [New folder/HANDOFF.md](New%20folder/HANDOFF.md) and [New folder/CLAUDE.md](New%20folder/CLAUDE.md).

| Path | What |
|---|---|
| `New folder/DCB_Staging_DB/` | staging SQLite + build script |
| `New folder/DCB_Star_Schema/` | star schema SQLite + build script |
| `New folder/DCB_Semantic_Layer/` | semantic layer + example queries |
| `New folder/dcb-ai-app/` | the AI app (see its README) |
| `Claude outputs/` | reports produced during the build |

## Run
```bat
cd "New folder"
copy .env.example .env          :: fill in the keys
cd dcb-ai-app
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt
```
Then follow `New folder/dcb-ai-app/README.md`.

> Private: client data.
