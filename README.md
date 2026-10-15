# Client Data Onboarding API

[![CI](https://github.com/adarsh1022/client-onboarding-api/actions/workflows/ci.yml/badge.svg)](https://github.com/adarsh1022/client-onboarding-api/actions/workflows/ci.yml)

A FastAPI service that takes the messy customer files clients actually send
(CSV or Excel, inconsistent column names, mixed date formats, typos, duplicates),
maps and validates every row, loads the clean records into PostgreSQL, and
reports row-level errors the client can fix and re-upload.

This is the unglamorous problem at the start of most enterprise integrations:
before any system can use a client's data, someone has to get it in cleanly.

## What it does

```
client file ──► POST /uploads ──► 202 Accepted (upload id)
                     │
                     ▼  background task
              read CSV / XLSX
                     │
              map columns        "E-mail Address", "Mail", "email" ──► email
                     │
              validate rows      trim, normalise phones, parse dates,
                     │           resolve country names ──► ISO codes
                     ▼
         ┌───────────┴────────────┐
   valid rows                invalid rows
   upsert by email           RowError(row_number, field, message)
         │                         │
         ▼                         ▼
 GET /customers           GET /uploads/{id}/errors
```

- **Column mapping** with configurable header aliases; fails fast with a clear
  message when required columns are missing or ambiguous.
- **Suggested mappings, free by default**: headers the aliases don't recognise get a
  suggested field from offline rules, a local Ollama model, or (optionally, paid) Claude.
  Suggestions need a human's approval and are remembered per client. See
  [Column suggestions](#column-suggestions).
- **Row validation** that collects every problem in a row, not just the first.
- **Idempotent loading**: re-uploading a file updates existing customers
  instead of duplicating them; duplicates inside one file are reported.
- **Error rows match the client's spreadsheet**, so "row 8" means row 8 in Excel.
- **Async processing**: uploads return immediately; poll the status endpoint.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/clients` | Register a client: `{"name": "Acme"}` |
| `POST` | `/uploads` | Upload a `.csv` or `.xlsx` (multipart field `file`, optional `client_id`) |
| `GET` | `/uploads/{id}` | Status (`completed`, `failed`, `needs_review`, ...) and counts: total, valid, invalid, inserted, updated |
| `GET` | `/uploads/{id}/errors` | Paginated row-level errors |
| `GET` | `/uploads/{id}/suggestions` | AI-suggested mappings for headers that weren't recognised |
| `POST` | `/suggestions/{id}/approve` | Accept a suggestion; optional body `{"field": "..."}` picks a different field |
| `POST` | `/suggestions/{id}/reject` | Dismiss a suggestion |
| `POST` | `/uploads/{id}/reprocess` | Run the stored file through the pipeline again |
| `GET` | `/customers` | Paginated customers; filter by `country`, search with `q` |
| `GET` | `/health` | Liveness check |

Every endpoint except `/health` needs an API key in the `X-API-Key` header; see
[Authentication](#authentication). Interactive docs are at `http://localhost:8000/docs` once
running: click **Authorize** there and paste a key to try the endpoints.

## Authentication

Create a key, then send it with every request:

```bash
python -m app.cli create-key --name me          # prints the key once: copy it now
export KEY=ob_...                               # PowerShell:  $env:KEY = "ob_..."
curl -H "X-API-Key: $KEY" http://localhost:8000/customers
```

Only a SHA-256 hash of each key is stored, so a lost key can't be recovered: make a new one.
`python -m app.cli list-keys` shows them (never the keys themselves) and
`python -m app.cli revoke-key <id>` stops one working. A missing, unknown or revoked key all
get the same `401`.

`POST /uploads` and `POST /uploads/{id}/reprocess` share a per-key limit of 10 per minute
(`RATE_LIMIT_UPLOADS`, `RATE_LIMIT_WINDOW_SECONDS`; 0 turns it off) and answer `429` with a
`Retry-After` header over it. The counters are in memory, so they are per process and reset
on restart: fine for one small instance, but several instances would each count separately.

On a host with no shell, set `BOOTSTRAP_API_KEY` (32+ characters) and the app creates that key,
hashed, when it starts. Changing the value rotates it.

## Column suggestions

Headers are matched in this order:

1. the built-in aliases (`HEADER_ALIASES`);
2. the client's approved mapping profile (when the upload has a `client_id`), plus any
   suggestions already approved for that upload;
3. a **suggester**, for headers still unrecognised while some field is still unmapped.

Suggestions are stored as `pending` and are never applied until someone approves them.
If a **required** column can only be matched by a pending suggestion, the upload ends as
`needs_review` instead of `failed`:

```bash
H="X-API-Key: $KEY"
curl -H "$H" -F "file=@file.csv" -F "client_id=1" http://localhost:8000/uploads   # -> needs_review
curl -H "$H" http://localhost:8000/uploads/1/suggestions                          # what was suggested
curl -H "$H" -X POST http://localhost:8000/suggestions/1/approve                  # or send {"field": "email"}
curl -H "$H" -X POST http://localhost:8000/suggestions/2/reject
curl -H "$H" -X POST http://localhost:8000/uploads/1/reprocess                    # -> completed
```

Approving also saves the mapping to the upload's client profile, so that client's next
file with the same header maps with no suggester call. An upload with no client only
benefits itself.

**Choosing a suggester** with `SUGGESTER`:

| `SUGGESTER` | Cost | What it does |
|---|---|---|
| `heuristic` (default) | free | Offline rules: fuzzy header matching against the aliases (plus some abbreviations and Hindi, German and Spanish words) combined with checks on the sample values (emails, 10-15 digit numbers, dates, countries, names). No network, no setup. |
| `ollama` | free | A local open model served by [Ollama](https://ollama.com), default `qwen2.5:3b`. Needs `ollama serve` running and the model pulled. |
| `claude` | paid | Claude through the Anthropic API. Needs `ANTHROPIC_API_KEY`; without one, suggestions are skipped. |

How well they do is measured on a small eval set, see [Evals](#evals). The heuristic gets
66 of 67 cases right on the repo's own set, but the cases were written by the same person as
the rules, so treat that as a regression guard rather than a promise: its first run on the
holdout cases, before any fixes, was 86.7%. `OllamaSuggester` is tested against a stand-in
server but has not been measured with a real model yet.

| Variable | Default | |
|---|---|---|
| `SUGGESTER` | `heuristic` | `heuristic`, `ollama` or `claude` |
| `OLLAMA_URL` | `http://localhost:11434` | Where Ollama is listening |
| `OLLAMA_MODEL` | `qwen2.5:3b` | Any model you have pulled |
| `OLLAMA_TIMEOUT_SECONDS` | `60` | Per-request timeout (small models can be slow on a CPU) |
| `OLLAMA_MAX_RETRIES` | `2` | Retries with backoff when Ollama is unreachable or returns 5xx |
| `ANTHROPIC_API_KEY` | unset | Needed for `SUGGESTER=claude` |
| `LLM_MODEL` | `claude-haiku-5-5` | Claude model used for suggestions |
| `LLM_TIMEOUT_SECONDS` | `20` | Per-request timeout for Claude |
| `LLM_MAX_RETRIES` | `2` | SDK retries on timeouts, rate limits and 5xx |

**What the suggesters see.** Only the unrecognised headers, up to 3 sample values per such
column, and the names of the fields still unmapped. Emails become `<email>` and phone-like
numbers have every digit replaced by `9` first. Other values, such as names, are kept as they
appear in the file. The heuristic never sends anything anywhere; Ollama sends it to your own
`OLLAMA_URL`; only `claude` leaves your machine. A suggester that errors, times out or
refuses never fails an upload: its suggestions are skipped.

**Stored files.** Each upload's original bytes are kept in the database so it can be
reprocessed. Uploads from before this feature have no stored file and can't be reprocessed.

## Evals

[`evals/mapping_cases.jsonl`](evals/mapping_cases.jsonl) has 67 messy columns (a header, a few
samples, and the field it should map to or `none`), including `Mob`, `Cntry`, `Date of
Joining`, `Ref Code`, `Remarks` and Hindi, German and Spanish headers. The runner scores any
suggester and lists its mistakes:

```bash
python evals/run_mapping_eval.py --suggester heuristic   # free, offline
python evals/run_mapping_eval.py --suggester ollama      # free, needs Ollama running
python evals/run_mapping_eval.py --suggester claude      # paid: one request per case
```

CI runs the heuristic eval against [`evals/baseline.json`](evals/baseline.json) and fails if
accuracy drops. Results, with dates, models and caveats, are in
[`evals/RESULTS.md`](evals/RESULTS.md).

## Run it

**With Docker (PostgreSQL):**

```bash
docker compose up --build
```

**Locally (SQLite, no Docker needed):**

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate      macOS/Linux:  source .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload
```

Try it with the sample file:

```bash
python -m app.cli create-key --name me      # once; copy the key it prints
H="X-API-Key: ob_..."
curl -H "$H" -F "file=@samples/messy_customers.csv" http://localhost:8000/uploads
curl -H "$H" http://localhost:8000/uploads/1
curl -H "$H" http://localhost:8000/uploads/1/errors
```

## Free public demo

The repo includes a [`render.yaml`](render.yaml) for a zero-cost deployment: a Render free web
service plus a free Neon PostgreSQL database, with `SUGGESTER=heuristic` so nothing paid is
ever called and no secret in the repo. [`docs/DEPLOY.md`](docs/DEPLOY.md) walks through it step
by step. Free-tier trade-offs: the service sleeps after 15 idle minutes and takes about a
minute to wake, and Neon's database suspends after 5 idle minutes. Once it is up,
`python scripts/smoke_test.py https://your-service.onrender.com` checks it end to end.

## Tests

```bash
pytest -v
ruff check .
```

CI (GitHub Actions) lints, applies migrations to a real PostgreSQL instance,
runs the test suite, and runs the mapping eval on every push. The tests never call the
Anthropic API or Ollama: they use fake suggesters, and a test fixture makes any real
Anthropic call fail.

## Tech

Python · FastAPI · SQLAlchemy 2.0 · Alembic · PostgreSQL · Pydantic ·
Ollama or Claude (optional) · pytest · Docker Compose · GitHub Actions

## Project layout

```
app/
  api/routes.py        HTTP endpoints
  ingest/reader.py     CSV / XLSX -> rows of strings
  ingest/mapping.py    header normalisation and column mapping
  ingest/validation.py row cleaning and validation rules
  ingest/pipeline.py   map -> validate -> dedupe -> upsert -> record errors
  ingest/suggester.py  MappingSuggester protocol, PII masking, Claude suggester
  ingest/heuristic.py  free offline suggester (the default)
  ingest/ollama.py     local Ollama suggester
  ingest/factory.py    picks the suggester from SUGGESTER
  ingest/service.py    background task wrapper
  models.py            Upload, Customer, RowError, Client, profile and suggestion tables
migrations/            Alembic migrations
samples/               example messy client file
evals/                 mapping eval cases, runner, baseline and results
tests/                 pytest suite
```
