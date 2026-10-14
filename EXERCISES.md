# Build guide

The project infrastructure (API, database, file reading, tests, Docker, CI) is
in place. The core logic is yours to write, in three exercises. Each one has a
full spec in the file's docstring and a test file that tells you when you're done.

Right now `pytest` shows 10 passed and 43 failed. The failures are your to-do list.

## Setup (once)

```bash
python -m venv .venv
.venv\Scripts\activate             # Windows (PowerShell / cmd)
pip install -r requirements-dev.txt
pytest -q                          # expect: 43 failed, 10 passed
```

## Exercise 1: Column mapping  (`app/ingest/mapping.py`)

```bash
pytest tests/test_mapping.py -v
```

Implement `normalize_header` and `map_columns`. Small, self-contained, and a
good warm-up for regex and dictionaries.

Interview questions to be ready for:
- Why normalise headers instead of listing every spelling?
- Why does an ambiguous mapping raise an error instead of picking one?

## Exercise 2: Row validation  (`app/ingest/validation.py`)

```bash
pytest tests/test_validation.py -v
```

Implement `validate_row`. The biggest of the three. Read the docstring
rule by rule and make the tests pass one group at a time
(e.g. `pytest tests/test_validation.py -k email -v`).

Interview questions to be ready for:
- Why collect all errors in a row instead of returning at the first one?
- Why does `validate_row` take a `today` argument?
- Where would these rules live if every client needed different ones?

## Exercise 3: The pipeline  (`app/ingest/pipeline.py`)

```bash
pytest tests/test_pipeline.py -v
pytest tests/test_end_to_end.py -v   # full HTTP flow with the sample file
```

Implement `ingest`: map, validate, dedupe, upsert, record errors, set counters.

Interview questions to be ready for:
- How do you avoid one database query per row on a 50,000-row file?
- What happens if two uploads for the same customer run at the same time?
- Why are error row numbers offset by 2?

## Done?

```bash
pytest -q          # 53 passed
ruff check .
uvicorn app.main:app --reload    # then open http://localhost:8000/docs
```

Upload `samples/messy_customers.csv` from the `/docs` page and look at the
results. Then push to GitHub and watch CI go green.

## Stretch goals (pick any, after the three exercises)

1. **API-key auth**: each client gets a key; uploads and customers are scoped per client.
2. **Per-client mapping profiles** stored in the database instead of `HEADER_ALIASES`.
3. **Large files**: process in chunks with a bulk upsert (`INSERT ... ON CONFLICT`) and measure the speed-up.
4. **Error report download**: `GET /uploads/{id}/errors.csv` that the client can open in Excel.
5. **Deploy it** to a free host (Render, Railway, Fly.io) and put the live `/docs` link on your resume.
