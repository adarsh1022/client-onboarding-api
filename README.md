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
- **AI-assisted mapping (optional)**: headers the aliases don't recognise can be
  matched by Claude. Suggestions need a human's approval and are remembered per
  client. See [AI-assisted column mapping](#ai-assisted-column-mapping).
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

Interactive docs at `http://localhost:8000/docs` once running.

## AI-assisted column mapping

Headers are matched in this order:

1. the built-in aliases (`HEADER_ALIASES`);
2. the client's approved mapping profile (when the upload has a `client_id`), plus any
   suggestions already approved for that upload;
3. Claude, for headers still unrecognised while some field is still unmapped.

Suggestions are stored as `pending` and are never applied until someone approves them.
If a **required** column can only be matched by a pending suggestion, the upload ends as
`needs_review` instead of `failed`:

```bash
curl -F "file=@file.csv" -F "client_id=1" http://localhost:8000/uploads   # -> needs_review
curl http://localhost:8000/uploads/1/suggestions                          # what Claude proposes
curl -X POST http://localhost:8000/suggestions/1/approve                  # or send {"field": "email"}
curl -X POST http://localhost:8000/suggestions/2/reject
curl -X POST http://localhost:8000/uploads/1/reprocess                    # -> completed
```

Approving also saves the mapping to the upload's client profile, so that client's next
file with the same header maps with no model call. An upload with no client only benefits
itself.

**Setup.** It is off unless `ANTHROPIC_API_KEY` is set; without a key the service behaves
as if the feature didn't exist, and uploads with unknown headers simply fail. Settings:

| Variable | Default | |
|---|---|---|
| `ANTHROPIC_API_KEY` | unset | Enables suggestions |
| `LLM_MODEL` | `claude-haiku-5-5` | Model used for suggestions |
| `LLM_TIMEOUT_SECONDS` | `20` | Per-request timeout |
| `LLM_MAX_RETRIES` | `2` | SDK retries on timeouts, rate limits and 5xx |

**What is sent to the model.** Only the unrecognised headers, up to 3 sample values per
such column, and the names of the fields still unmapped. Emails become `<email>` and
phone-like numbers have every digit replaced by `9` before leaving the process. Other
values, such as names, are sent as they appear in the file. A model error, timeout or
refusal never fails an upload: the suggestions are skipped.

**Stored files.** Each upload's original bytes are kept in the database so it can be
reprocessed. Uploads from before this feature have no stored file and can't be reprocessed.

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
curl -F "file=@samples/messy_customers.csv" http://localhost:8000/uploads
curl http://localhost:8000/uploads/1
curl http://localhost:8000/uploads/1/errors
```

## Tests

```bash
pytest -v
ruff check .
```

CI (GitHub Actions) lints, applies migrations to a real PostgreSQL instance,
and runs the test suite on every push. The tests never call the Anthropic API:
they use a fake suggester, and a test fixture makes any real call fail.

## Tech

Python · FastAPI · SQLAlchemy 2.0 · Alembic · PostgreSQL · Pydantic ·
Anthropic SDK (Claude) · pytest · Docker Compose · GitHub Actions

## Project layout

```
app/
  api/routes.py        HTTP endpoints
  ingest/reader.py     CSV / XLSX -> rows of strings
  ingest/mapping.py    header normalisation and column mapping
  ingest/validation.py row cleaning and validation rules
  ingest/pipeline.py   map -> validate -> dedupe -> upsert -> record errors
  ingest/suggester.py  MappingSuggester protocol, PII masking, Claude suggester
  ingest/service.py    background task wrapper
  models.py            Upload, Customer, RowError, Client, profile and suggestion tables
migrations/            Alembic migrations
samples/               example messy client file
tests/                 pytest suite
```
