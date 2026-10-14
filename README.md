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
- **Row validation** that collects every problem in a row, not just the first.
- **Idempotent loading**: re-uploading a file updates existing customers
  instead of duplicating them; duplicates inside one file are reported.
- **Error rows match the client's spreadsheet**, so "row 8" means row 8 in Excel.
- **Async processing**: uploads return immediately; poll the status endpoint.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/uploads` | Upload a `.csv` or `.xlsx` (multipart field `file`) |
| `GET` | `/uploads/{id}` | Status and counts: total, valid, invalid, inserted, updated |
| `GET` | `/uploads/{id}/errors` | Paginated row-level errors |
| `GET` | `/customers` | Paginated customers; filter by `country`, search with `q` |
| `GET` | `/health` | Liveness check |

Interactive docs at `http://localhost:8000/docs` once running.

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
and runs the test suite on every push.

## Tech

Python · FastAPI · SQLAlchemy 2.0 · Alembic · PostgreSQL · Pydantic ·
pytest · Docker Compose · GitHub Actions

## Project layout

```
app/
  api/routes.py        HTTP endpoints
  ingest/reader.py     CSV / XLSX -> rows of strings
  ingest/mapping.py    header normalisation and column mapping
  ingest/validation.py row cleaning and validation rules
  ingest/pipeline.py   map -> validate -> dedupe -> upsert -> record errors
  ingest/service.py    background task wrapper
  models.py            Upload, Customer, RowError tables
migrations/            Alembic migrations
samples/               example messy client file
tests/                 pytest suite
```
