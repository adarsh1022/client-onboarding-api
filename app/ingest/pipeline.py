"""EXERCISE 3 - The ingestion pipeline.

Ties mapping (Exercise 1) and validation (Exercise 2) together and writes
the results to the database.

Run the tests for this exercise with:
    pytest tests/test_pipeline.py -v
"""

from datetime import date

from sqlalchemy.orm import Session

from app.ingest.mapping import MappingError, map_columns  # noqa: F401
from app.ingest.reader import ParsedFile
from app.ingest.validation import validate_row  # noqa: F401
from app.models import Customer, RowError, Upload, UploadStatus, utcnow  # noqa: F401


def ingest(session: Session, upload: Upload, parsed: ParsedFile, today: date | None = None) -> None:
    """Process a parsed file into Customer rows and RowError rows.

    `upload` is already saved in the database. Update it in place and commit
    once at the end.

    Steps:
      1. Map the headers with map_columns(parsed.headers).
         On MappingError: set upload.status = FAILED, upload.error_message =
         str(error), upload.finished_at = utcnow(), commit, and return.

      2. For each row in parsed.rows (row_number starts at 2, because row 1 of
         the file is the header):
           - Skip rows where every value is blank (after strip). Skipped rows
             are not counted anywhere. Keep row_number in step with the file
             anyway, so error row numbers match what the client sees in Excel.
           - Build {canonical_field: raw_value} using the mapping.
           - Call validate_row(that_dict, today=today).
           - If it has errors: add one RowError per FieldError for this row.
           - If it's valid but its email was already seen EARLIER IN THIS FILE:
             add RowError(field="email", message="duplicate email in file").
             The first occurrence wins.
           - Otherwise, upsert by email:
               existing Customer with that email -> update name, phone,
               country_code, signup_date, source_upload_id; count as updated.
               no existing Customer -> insert a new one; count as inserted.

      3. Set the counters on the upload:
           total_rows   = rows processed (not counting skipped blank rows)
           invalid_rows = rows with at least one RowError (count the row once)
           valid_rows   = total_rows - invalid_rows
           inserted / updated as counted above
         Then status = COMPLETED, finished_at = utcnow(), and commit.

    Think about: how you look up existing customers. One query per row works,
    but how would you avoid that for a 50,000-row file?
    """
    raise NotImplementedError("Exercise 3: implement ingest")
