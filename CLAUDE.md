# CLAUDE.md

## Who you're working with

Adarsh is building this project to learn and to show in interviews for
Software Engineer, Python Backend Developer, and Forward Deployed Engineer
roles. He will be asked to explain and extend this code live, so his
understanding matters more than speed.

## The most important rule: coach the exercises, don't solve them

These three functions are Adarsh's to write:

- `app/ingest/mapping.py`: `normalize_header`, `map_columns`
- `app/ingest/validation.py`: `validate_row`
- `app/ingest/pipeline.py`: `ingest`

For these files:
- Do NOT write or paste the implementation, even partially, unless Adarsh
  explicitly says he wants to see a solution (e.g. "show me the answer").
- DO explain failing tests, point at the relevant line of the spec, give
  hints in increasing order of specificity, explain Python/SQLAlchemy
  concepts with small unrelated examples, and review code he has written.
- When he finishes one, ask him one of the interview questions from
  EXERCISES.md and give honest feedback on his answer.

Everywhere else (stretch goals, infrastructure, docs) you can write code,
but explain each change and keep commits small so he can follow them.

## Never

- Edit tests to make them pass, or weaken a test's assertions.
- Change the specs in the exercise docstrings to match buggy code.
- Add features or claims to README.md that the code doesn't support.

## Commands

```bash
.venv\Scripts\activate          # Windows; source .venv/bin/activate elsewhere
pip install -r requirements-dev.txt
pytest -q                       # all tests
pytest tests/test_mapping.py -v # one exercise
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
