"""Exercise 3 tests. These fail until app/ingest/pipeline.py is implemented
(they also rely on Exercises 1 and 2)."""

import math
import sqlite3
from datetime import date

from sqlalchemy import event, select

from app.ingest.pipeline import LOOKUP_BATCH_SIZE, ingest
from app.ingest.reader import ParsedFile
from app.models import Customer, RowError, Upload, UploadStatus

TODAY = date(2026, 1, 15)
HEADERS = ["Customer Name", "E-mail", "Phone No.", "Country", "Joined"]


def make_file(*rows):
    return ParsedFile(headers=HEADERS, rows=[dict(zip(HEADERS, r, strict=True)) for r in rows])


def new_upload(session):
    upload = Upload(filename="clients.csv")
    session.add(upload)
    session.commit()
    return upload


def errors_for(session, upload):
    return [
        (e.row_number, e.field, e.message)
        for e in session.scalars(
            select(RowError).where(RowError.upload_id == upload.id).order_by(RowError.id)
        )
    ]


def test_happy_path_inserts_customers(session):
    upload = new_upload(session)
    parsed = make_file(
        ["Asha Rao", "asha@x.com", "9876543210", "India", "2024-03-01"],
        ["John Smith", "john@y.com", "", "us", ""],
    )
    ingest(session, upload, parsed, today=TODAY)

    assert upload.status == UploadStatus.COMPLETED
    assert upload.finished_at is not None
    assert (upload.total_rows, upload.valid_rows, upload.invalid_rows) == (2, 2, 0)
    assert (upload.inserted, upload.updated) == (2, 0)

    asha = session.scalar(select(Customer).where(Customer.email == "asha@x.com"))
    assert asha.country_code == "IN"
    assert asha.signup_date == date(2024, 3, 1)
    assert asha.source_upload_id == upload.id


def test_invalid_rows_recorded_with_file_row_numbers(session):
    upload = new_upload(session)
    parsed = make_file(
        ["Asha Rao", "asha@x.com", "", "IN", ""],  # row 2: ok
        ["", "", "", "", ""],  # row 3: blank -> skipped
        ["", "bad-email", "", "Narnia", ""],  # row 4: 3 errors
    )
    ingest(session, upload, parsed, today=TODAY)

    assert (upload.total_rows, upload.valid_rows, upload.invalid_rows) == (2, 1, 1)
    assert sorted(errors_for(session, upload)) == [
        (4, "country", "unknown country 'Narnia'"),
        (4, "email", "invalid format"),
        (4, "name", "required"),
    ]
    assert session.scalar(select(Customer).where(Customer.email == "bad-email")) is None


def test_duplicate_email_in_same_file_first_wins(session):
    upload = new_upload(session)
    parsed = make_file(
        ["Asha Rao", "asha@x.com", "", "IN", ""],
        ["Asha Duplicate", "ASHA@x.com", "", "IN", ""],
    )
    ingest(session, upload, parsed, today=TODAY)

    assert (upload.valid_rows, upload.invalid_rows, upload.inserted) == (1, 1, 1)
    assert errors_for(session, upload) == [(3, "email", "duplicate email in file")]
    assert session.scalar(select(Customer.name)) == "Asha Rao"


def test_existing_customer_is_updated_not_duplicated(session):
    first = new_upload(session)
    ingest(session, first, make_file(["Asha Rao", "asha@x.com", "", "IN", ""]), today=TODAY)

    second = new_upload(session)
    ingest(
        session,
        second,
        make_file(
            ["Asha R. Rao", "asha@x.com", "9876543210", "IN", "2024-03-01"],
            ["New Person", "new@x.com", "", "SG", ""],
        ),
        today=TODAY,
    )

    assert (second.inserted, second.updated) == (1, 1)
    customers = session.scalars(select(Customer).order_by(Customer.id)).all()
    assert len(customers) == 2
    assert customers[0].name == "Asha R. Rao"
    assert customers[0].phone == "9876543210"
    assert customers[0].source_upload_id == second.id


def test_large_file_looks_up_existing_customers_in_batches(session):
    row_count = 2500

    def big_file(label):
        return make_file(
            *[[f"{label} {i}", f"user{i}@x.com", "", "IN", ""] for i in range(row_count)]
        )

    first = new_upload(session)
    ingest(session, first, big_file("First"), today=TODAY)
    assert (first.inserted, first.updated) == (row_count, 0)

    # Cap SQLite's bound parameters at the batch size: one IN (...) holding all
    # 2,500 emails would now fail with "too many SQL variables".
    raw = session.connection().connection.driver_connection
    original_limit = raw.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, LOOKUP_BATCH_SIZE)

    lookups = []

    def count_customer_lookups(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and "FROM customers" in statement:
            lookups.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", count_customer_lookups)
    try:
        second = new_upload(session)
        ingest(session, second, big_file("Second"), today=TODAY)
    finally:
        event.remove(engine, "before_cursor_execute", count_customer_lookups)
        raw.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, original_limit)

    assert (second.inserted, second.updated) == (0, row_count)
    assert len(lookups) == math.ceil(row_count / LOOKUP_BATCH_SIZE)
    assert len(session.scalars(select(Customer)).all()) == row_count
    assert session.scalar(select(Customer.name).where(Customer.email == "user2499@x.com")) == (
        "Second 2499"
    )


def test_unmappable_file_fails_upload(session):
    upload = new_upload(session)
    parsed = ParsedFile(headers=["Name", "Phone"], rows=[{"Name": "A", "Phone": "1"}])
    ingest(session, upload, parsed, today=TODAY)

    assert upload.status == UploadStatus.FAILED
    assert "missing required columns: country, email" in upload.error_message
    assert upload.finished_at is not None
    assert session.scalar(select(Customer)) is None
