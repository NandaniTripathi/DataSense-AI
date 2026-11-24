# DataSense AI

An AI data analyst. Upload a CSV or Excel file, then ask questions in plain English.
The LLM (Groq) writes SQL, the backend runs it safely against your data, and you get a verified
answer with the table, an interactive chart, the exact SQL used, and follow-up suggestions.
It also profiles the data, finds anomalies and generates a downloadable report.

**Stack:** React + Vite + Tailwind · FastAPI · Groq API (OpenAI-compatible tool calling) · Pandas / NumPy ·
scikit-learn · SQLite · Plotly

---

## Quick start (Windows, PowerShell)

You need **Python 3.12 or newer**, **Node.js 22 LTS** (20+ also works) and Git.
(`python --version` and `node --version` to check. During the Python install, tick "Add python.exe to PATH".)

### 1. Backend

```powershell
cd datasense-ai\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

If PowerShell blocks the activate script, run once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

Open `backend\.env` in an editor and paste your key after `GROQ_API_KEY=`
(create one at https://console.groq.com/keys). The key lives only in this file on the server;
it is never sent to the browser and `.env` is git-ignored. Never paste it into code, chats or commits.

`GROQ_MODEL` selects the model and **must support tool calling**. The default is `openai/gpt-oss-120b`
(a Groq production model); `openai/gpt-oss-20b` is a faster/cheaper alternative. Groq retires models
from time to time (e.g. `llama-3.3-70b-versatile` was shut down on 2026-08-16), so check what your key
can use and that tool calling works:

```powershell
python -m scripts.check_groq
```

`check_groq` is the only thing that talks to the real Groq API from the command line (it prints no key).
Free-tier keys have per-minute token limits; if you see a "rate limiting" message, wait a moment and retry.

Start the backend (same window, venv active):

```powershell
uvicorn app.main:app --reload --port 8000
```

Check http://localhost:8000/api/health and the API docs at http://localhost:8000/docs.

### 2. Frontend (a second PowerShell window)

```powershell
cd datasense-ai\frontend
npm install
npm run dev
```

Open **http://localhost:5173**. Click **Try the demo dataset** to see everything working in one click.

Shortcut: after the one-time setup above, `.\run-dev.ps1` in the project root opens both servers.

### Single-server mode (what production uses)

```powershell
cd frontend; npm run build; cd ..\backend
uvicorn app.main:app --port 8000
```

FastAPI then serves the built React app too: open http://localhost:8000.

---

## What it does

| Page | Features |
|---|---|
| **Datasets** | Drag-and-drop CSV/TSV/XLSX/XLS upload, type inference (numbers, dates, booleans, leading-zero IDs), one-click demo dataset, delete |
| **Dashboard** | KPIs, auto-generated Plotly charts, key findings (trend, segment concentration, correlations, skew, data quality), **anomaly detection**, optional AI executive summary |
| **Explorer** | Server-side search, sorting and pagination over all rows; per-column profile cards |
| **AI Analyst** | Natural-language questions, multi-step SQL, self-correction on errors, chart + table + SQL tabs, CSV export, follow-up suggestions, conversation history and context-aware follow-ups |
| **Reports** | One-file HTML report (KPIs, findings, anomalies, interactive charts, data dictionary, optional analyst Q&A). Has a Print / Save as PDF button |

### Anomaly detection (no API key needed)
1. **Time series**: the primary metric is aggregated by day/week/month and compared with a rolling median using a robust (MAD-based) z-score.
2. **Per-column outliers**: modified z-score on each numeric column.
3. **Multivariate**: scikit-learn `IsolationForest` across all numeric columns finds unusual *combinations* of values.

---

## Architecture

```
React (Vite, Tailwind, Plotly)  --/api-->  FastAPI
                                             |-- routers/      HTTP only (validation, status codes)
                                             |-- services/
                                             |     ingest.py        Pandas cleaning + type inference
                                             |     profiling.py     column stats -> "schema card"
                                             |     storage.py       one SQLite file per dataset
                                             |     sql_guard.py     sqlglot: single read-only SELECT
                                             |     sqlite_runner.py read-only conn + authorizer + timeout + row cap
                                             |     query_engine.py  LLM tool-use agent loop
                                             |     charts.py        Plotly figures from results
                                             |     insights.py      findings + anomalies (Pandas/sklearn)
                                             |     report.py        HTML report
                                             |-- db/           SQLAlchemy metadata (datasets, chats)
                                             '-- llm.py        the ONLY file that imports the LLM SDK (Groq via the OpenAI client)
```

**Core idea: the LLM decides *what to compute*; our code does the computing.**
The LLM never sees the full dataset, only a compact schema card (column names, types, stats,
a few sample values). It answers by calling two tools:

* `run_sql(sql, purpose)` runs a query; the result (first 40 rows) or the error is returned to the LLM, which can fix and retry (up to 6 steps).
* `final_answer(summary, display_query_id, chart, follow_up_questions)` ends the loop.

Every number in the answer therefore comes from an executed query, not from the model's memory.

### Why the AI Analyst is defensive with Groq (`openai/gpt-oss-120b`)
Groq does not force gpt-oss to call a tool, even with `tool_choice="required"`: if the model answers in prose,
Groq returns HTTP 400 *"Tool choice is required, but model did not call a tool"*, and a reasoning model can also
return an empty message when its hidden "thinking" uses up the token budget. The engine therefore:

* uses `tool_choice="auto"`, keeps gpt-oss reasoning short (`reasoning_effort="low"`), and retries a malformed tool call once;
* salvages prose from Groq's `failed_generation` field instead of discarding it;
* validates every `final_answer` (non-empty summary, real `display_query_id`, list-typed follow-ups) and every SQL statement (sqlglot + sandbox);
* never shows prose that contains numbers when no query has produced them (it asks for `run_sql` once, then says it could not build a query);
* if the model returns nothing usable after a query ran, **finalises from the real result** with a tool-free JSON-mode call
  (summary + chart + follow-ups), then a plain-text call, and as a last resort still shows the real table with an honest message;
* caps the loop at 6 steps and turns Groq errors (bad key, 429, retired model, timeout, no connection) into clear messages.

Backend logs (`datasense.llm`) record `Groq tool-call failure ... failed_generation=...` and
`Groq returned an empty message (finish_reason=..., usage=...)`, which is what to look at if an answer ever looks wrong.

### Security layers for AI-generated SQL
1. `sqlglot` parses the SQL: exactly one statement, a SELECT/CTE only, only the `data` table.
2. SQLite connection opened read-only (`mode=ro`, `PRAGMA query_only`).
3. SQLite *authorizer* callback denies everything except SELECT/READ and non-dangerous functions.
4. Progress handler aborts queries after 10 s; at most 5,000 rows are fetched.
5. Prompt-injection stance: dataset text is treated as data, not instructions (system prompt), and the layers above still hold even if the model is tricked.

Other hardening: upload size/type limits, sanitised column names, whitelisted sort columns and
bound parameters in the Explorer, per-IP chat rate limit, API key only on the server, CORS allow-list.

---

## Tests

```powershell
# backend (from backend\, venv active)
pip install -r requirements-dev.txt
pytest -q

# frontend (from frontend\)
npm test
```

```powershell
# production build check (from frontend\)
npm run build
```

**No automated test calls Groq.** Two kinds of fakes are used:

* `tests/test_llm_groq.py` replaces the OpenAI-compatible client with a scripted fake, so Groq request/response
  translation, error mapping (400/401/403/404/429/500/timeout/connection), tool-call failures, empty replies and the whole
  agent loop run against a real uploaded dataset with real SQL validation and execution. It also drives `/api/chat`
  end to end (missing values, follow-ups, Groq failures).
* The other backend tests use `LLM_PROVIDER=mock` (`app/services/mock_llm.py`), which is never active unless that variable is set.

To verify the real API with your own key, run `python -m scripts.check_groq` (model list, tool call, JSON mode).

## Troubleshooting

| Symptom | Fix |
|---|---|
| Sidebar says "Groq key missing" | Put `GROQ_API_KEY=...` in `backend\.env`, restart `uvicorn` |
| "The Groq API key was rejected" | Key is wrong or revoked: create a new one at console.groq.com/keys |
| "Model ... was not found" | Groq retired it: run `python -m scripts.check_groq`, set `GROQ_MODEL` to a listed tool-calling model |
| "Groq is rate limiting requests" | Free-tier tokens/minute: wait ~30-60 s; each question uses 2-3 model calls |
| Answer says "I couldn't finish writing the explanation" | The model returned nothing usable; the real table is shown. Check the `datasense.llm` log lines above |
| "Cannot reach the DataSense backend" | Backend not running on port 8000 |

## Deploy

The included `Dockerfile` builds the frontend and serves everything from one FastAPI container.

* **Render:** push to GitHub, create a *Blueprint* from `render.yaml`, then set `GROQ_API_KEY` in the dashboard.
* **Anywhere with Docker:** `docker compose up --build` (reads `backend/.env`).

Uploaded datasets and the metadata DB live in `DATA_DIR` (`/data`, a persistent disk in the Render blueprint).

## Known limitations / next steps
* Single-user: there is no login. Add JWT auth and a `user_id` on datasets before exposing it publicly with real data.
* Only the first sheet of an Excel file is loaded; one table per dataset (no joins across files).
* SQLite is right for a demo and mid-sized files (tested at 4,000 rows; designed for hundreds of thousands). Swap to Postgres/DuckDB for large scale.
* In-memory rate limiter is per process. Use Redis if you run several workers.
* Answers stream in one piece (no token streaming yet).

