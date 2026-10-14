"""App entry point.  Run locally with:  uvicorn app.main:app --reload"""

import logging

from fastapi import FastAPI

from app.api.routes import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="Client Data Onboarding API",
    description=(
        "Upload messy client customer files (CSV/Excel). The service maps their "
        "columns, validates every row, loads clean records into SQL, and reports "
        "row-level errors the client can fix."
    ),
    version="0.1.0",
)
app.include_router(router)
