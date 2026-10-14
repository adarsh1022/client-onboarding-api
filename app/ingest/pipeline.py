"""The ingestion pipeline.

Ties mapping and validation together and writes the results to the database.

Run the tests with:
    pytest tests/test_pipeline.py -v
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingest.mapping import MappingError, map_columns
from app.ingest.reader import ParsedFile
from app.ingest.validation import validate_row
from app.models import Customer, RowError, Upload, UploadStatus, utcnow
from app.schemas import CleanCustomer, FieldError

# Emails per existing-customer lookup; keeps each IN (...) under DB parameter limits.
LOOKUP_BATCH_SIZE = 1000


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

    Existing customers are loaded in batches of LOOKUP_BATCH_SIZE emails, one
    query per batch instead of one per row. Batching also keeps each query under
    the database's bound-parameter limit, so a 50,000-row file works.
    """
    try:
        columns = map_columns(parsed.headers)
    except MappingError as error:
        upload.status = UploadStatus.FAILED
        upload.error_message = str(error)
        upload.finished_at = utcnow()
        session.commit()
        return

    total_rows = 0
    invalid_rows = 0
    to_save: list[tuple[int, CleanCustomer]] = []
    seen_emails: set[str] = set()

    # Pass 1: validate every row and decide which ones are candidates to save.
    for row_number, raw_row in enumerate(parsed.rows, start=2):
        if all(not (value or "").strip() for value in raw_row.values()):
            continue
        total_rows += 1

        mapped = {field: raw_row.get(header, "") for field, header in columns.items()}
        clean, field_errors = validate_row(mapped, today=today)

        if clean is not None and clean.email in seen_emails:
            clean = None
            field_errors = [FieldError(field="email", message="duplicate email in file")]

        if clean is None:
            invalid_rows += 1
            for field_error in field_errors:
                session.add(
                    RowError(
                        upload_id=upload.id,
                        row_number=row_number,
                        field=field_error.field,
                        message=field_error.message,
                    )
                )
            continue

        seen_emails.add(clean.email)
        to_save.append((row_number, clean))

    # Pass 2: look up existing customers in batches (not one query per row), then upsert in memory.
    existing: dict[str, Customer] = {}
    emails = list(seen_emails)
    for start in range(0, len(emails), LOOKUP_BATCH_SIZE):
        batch = emails[start : start + LOOKUP_BATCH_SIZE]
        for customer in session.scalars(select(Customer).where(Customer.email.in_(batch))):
            existing[customer.email] = customer

    inserted = 0
    updated = 0
    for _, clean in to_save:
        customer = existing.get(clean.email)
        if customer is None:
            session.add(
                Customer(
                    email=clean.email,
                    name=clean.name,
                    phone=clean.phone,
                    country_code=clean.country_code,
                    signup_date=clean.signup_date,
                    source_upload_id=upload.id,
                )
            )
            inserted += 1
        else:
            customer.name = clean.name
            customer.phone = clean.phone
            customer.country_code = clean.country_code
            customer.signup_date = clean.signup_date
            customer.source_upload_id = upload.id
            updated += 1

    upload.total_rows = total_rows
    upload.invalid_rows = invalid_rows
    upload.valid_rows = total_rows - invalid_rows
    upload.inserted = inserted
    upload.updated = updated
    upload.status = UploadStatus.COMPLETED
    upload.finished_at = utcnow()
    session.commit()
