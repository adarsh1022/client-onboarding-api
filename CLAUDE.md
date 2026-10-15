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

- `app/auth.py`: API keys. Every endpoint except `/health` (and `/docs`) needs a valid
  `X-API-Key`; only the SHA-256 hash is stored. New endpoints go on `router` (protected), never
  `public_router`; a test walks the OpenAPI schema and fails on any unprotected route.
  `app/ratelimit.py` limits the upload endpoints per key (in memory). `app/cli.py` manages keys.
- `render.yaml` + `docs/DEPLOY.md`: the free Render + Neon demo. Never commit secrets: they are
  `sync: false` variables set in the Render dashboard.
- `app/api/routes.py`: FastAPI endpoints. `POST /uploads` stores an Upload row
  and schedules `process_upload` as a background task, returning 202.
- `app/ingest/service.py`: background task. Opens its own session, reads
  the file, calls `ingest`, and marks the upload failed on any exception.
- `app/ingest/reader.py`: CSV/XLSX -> `ParsedFile(headers, rows)`, all values strings.
- `app/ingest/mapping.py` -> `validation.py` -> `pipeline.py`: the core flow.
- Column matching order: static `HEADER_ALIASES`, then the client's approved profile plus
  suggestions approved for the upload, then a suggester for what is still unknown. Suggestions
  are stored `pending` and applied only once approved. If a required column can only come from a
  pending suggestion the upload is `needs_review`, not `failed`.
- Suggesters implement the `MappingSuggester` protocol (`app/ingest/suggester.py`) and are chosen
  by `SUGGESTER` in `app/ingest/factory.py`: `heuristic` (default, free, offline rules in
  `heuristic.py`), `ollama` (local model, `ollama.py`) or `claude` (paid; no
  `ANTHROPIC_API_KEY` means no suggester). Emails and phones are masked before samples reach any
  suggester.
- `evals/`: `mapping_cases.jsonl` plus `run_mapping_eval.py`. CI fails if the heuristic's score
  drops below `evals/baseline.json`. After changing the heuristic or the cases, re-run the eval,
  update the baseline deliberately (`--update-baseline`) and record the result in `RESULTS.md`.
- `app/models.py`: SQLAlchemy 2.0 typed models. `Customer.email` is unique. `Upload.content`
  keeps the original file bytes so an upload can be reprocessed.
- Tests use in-memory SQLite (see `tests/conftest.py`); production uses PostgreSQL.
- Tests never call the real Anthropic API or Ollama: use the fakes in `tests/fakes.py`. A conftest
  fixture selects the heuristic suggester, removes any API key and makes a real SDK call raise.

## Conventions

- Python 3.11+, type hints everywhere, SQLAlchemy 2.0 style (`select()`, `Mapped[]`).
- ruff, line length 100.
- Any change to `models.py` needs an Alembic migration.
- New behaviour needs a test.
