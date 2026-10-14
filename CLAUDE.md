# CLAUDE.md

## Working on this project

This is a portfolio project for Adarsh (Software Engineer, Python Backend
Developer, and Forward Deployed Engineer roles). He will walk through and
extend the code live, so keep it readable and keep changes easy to explain.

Implement features directly. Keep tests passing, never weaken tests or specs, and explain what you changed in a short summary after each task.

## Never

- Edit tests to make them pass, or weaken a test's assertions.
- Change the specs in docstrings to match buggy code.
- Add features or claims to README.md that the code doesn't support.

## Commands

```bash
.venv\Scripts\activate          # Windows; source .venv/bin/activate elsewhere
pip install -r requirements-dev.txt
pytest -q                       # all tests
pytest tests/test_mapping.py -v # one test file
ruff check . && ruff format .   # lint + format
alembic upgrade head            # create/upgrade the local database
alembic revision --autogenerate -m "describe change"   # after editing models.py
uvicorn app.main:app --reload   # run the API, docs at /docs
docker compose up --build       # run with PostgreSQL
```

## Architecture

- `app/api/routes.py`: FastAPI endpoints. `POST /uploads` stores an Upload row
  and schedules `process_upload` as a background task, returning 202.
- `app/ingest/service.py`: background task. Opens its own session, reads
  the file, calls `ingest`, and marks the upload failed on any exception.
- `app/ingest/reader.py`: CSV/XLSX -> `ParsedFile(headers, rows)`, all values strings.
- `app/ingest/mapping.py` -> `validation.py` -> `pipeline.py`: the core flow.
- `app/models.py`: SQLAlchemy 2.0 typed models. `Customer.email` is unique.
- Tests use in-memory SQLite (see `tests/conftest.py`); production uses PostgreSQL.

## Conventions

- Python 3.11+, type hints everywhere, SQLAlchemy 2.0 style (`select()`, `Mapped[]`).
- ruff, line length 100.
- Any change to `models.py` needs an Alembic migration.
- New behaviour needs a test.
