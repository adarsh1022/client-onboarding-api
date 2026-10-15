"""The ingestion pipeline.

Ties mapping and validation together and writes the results to the database.
"""

import logging
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.ingest.mapping import (
    HEADER_ALIASES,
    MappingError,
    find_columns,
    map_columns,
    normalize_header,
)
from app.ingest.reader import ParsedFile
from app.ingest.suggester import MappingSuggester, sample_columns
from app.ingest.validation import validate_row
from app.models import (
    ClientMappingProfile,
    Customer,
    MappingSuggestion,
    RowError,
    SuggestionStatus,
    Upload,
    UploadStatus,
    utcnow,
)
from app.schemas import CleanCustomer, FieldError

log = logging.getLogger(__name__)

# Emails per existing-customer lookup; keeps each IN (...) under DB parameter limits.
LOOKUP_BATCH_SIZE = 1000

# Longest header we store a suggestion for (the column is String(255)).
MAX_HEADER_CHARS = 255


def ingest(
    session: Session,
    upload: Upload,
    parsed: ParsedFile,
    today: date | None = None,
    suggester: MappingSuggester | None = None,
) -> None:
    """Process a parsed file into Customer rows and RowError rows.

    `upload` is already saved in the database. Update it in place and commit
    at the end. (When a `suggester` is called, one earlier commit ends the open
    transaction first, so the database isn't locked during the network call.)

    Running it again for the same upload (a reprocess) starts clean: the upload's
    old RowErrors and counters are discarded first.

    Steps:
      1. Map the headers with map_columns(parsed.headers).
         On MappingError: set upload.status = FAILED, upload.error_message =
         str(error), upload.finished_at = utcnow(), commit, and return.

         Headers are matched in this order: the static HEADER_ALIASES, then the
         upload's client's approved mapping profile and the suggestions already
         approved for this upload, then `suggester` for any header still unknown
         (only while some field is still unmapped). Suggestions are saved as
         pending MappingSuggestion rows and are never applied until approved.
         If the only problem is missing required columns and a pending suggestion
         covers one of them, the upload gets status NEEDS_REVIEW instead of FAILED.

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
    _reset_upload(session, upload)
    approved = _approved_aliases(session, upload)
    _save_suggestions(session, upload, parsed, approved, suggester)

    try:
        columns = map_columns(parsed.headers, extra_aliases=approved)
    except MappingError as error:
        if error.missing and _pending_suggestion_covers(session, upload, error.missing):
            upload.status = UploadStatus.NEEDS_REVIEW
            upload.error_message = (
                f"needs review: {error}; review the suggested mappings at "
                f"/uploads/{upload.id}/suggestions"
            )
            session.commit()
            return
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


def _reset_upload(session: Session, upload: Upload) -> None:
    """Forget the results of any earlier run of this upload (a reprocess starts clean)."""
    session.execute(delete(RowError).where(RowError.upload_id == upload.id))
    upload.total_rows = 0
    upload.valid_rows = 0
    upload.invalid_rows = 0
    upload.inserted = 0
    upload.updated = 0
    upload.error_message = None
    upload.finished_at = None


def _approved_aliases(session: Session, upload: Upload) -> dict[str, str]:
    """{normalized header -> canonical field} that a reviewer has approved.

    That is the upload's client's profile plus the suggestions approved for this very
    upload (so approving works for an upload that has no client too). Approvals made
    for this upload win over the client's older profile entries.
    """
    aliases: dict[str, str] = {}
    if upload.client_id is not None:
        profile = session.scalars(
            select(ClientMappingProfile).where(ClientMappingProfile.client_id == upload.client_id)
        )
        aliases.update({entry.normalized_header: entry.canonical_field for entry in profile})

    approved = session.scalars(
        select(MappingSuggestion).where(
            MappingSuggestion.upload_id == upload.id,
            MappingSuggestion.status == SuggestionStatus.APPROVED,
        )
    )
    for suggestion in approved:
        chosen = suggestion.approved_field or suggestion.suggested_field
        if chosen:
            aliases[normalize_header(suggestion.original_header)] = chosen
    return {alias: field for alias, field in aliases.items() if field in HEADER_ALIASES}


def _save_suggestions(
    session: Session,
    upload: Upload,
    parsed: ParsedFile,
    approved: dict[str, str],
    suggester: MappingSuggester | None,
) -> None:
    """Ask `suggester` about headers nothing else recognised, and store its answers as pending.

    Skips headers that already have a suggestion for this upload (approved, rejected or
    still pending), so a reprocess never re-asks, and does nothing once every field is
    mapped. A suggester that fails is logged and ignored: it must never fail the upload.
    """
    if suggester is None:
        return

    matches = find_columns(parsed.headers, approved)
    matched = {header for headers in matches.values() for header in headers}
    open_fields = [field for field in HEADER_ALIASES if field not in matches]
    already_suggested = set(
        session.scalars(
            select(MappingSuggestion.original_header).where(
                MappingSuggestion.upload_id == upload.id
            )
        )
    )
    unknown = [
        header
        for header in dict.fromkeys(parsed.headers)
        if normalize_header(header)
        and len(header) <= MAX_HEADER_CHARS
        and header not in matched
        and header not in already_suggested
    ]
    if not unknown or not open_fields:
        return

    columns = sample_columns(unknown, parsed.rows)
    session.commit()  # end the open transaction so no database lock is held during the call
    try:
        suggestions = suggester.suggest(columns, open_fields)
    except Exception:  # whatever went wrong, carry on without suggestions
        log.warning("mapping suggester failed for upload %s", upload.id, exc_info=True)
        return

    saved: set[str] = set()
    for suggestion in suggestions:
        if suggestion.header not in unknown or suggestion.header in saved:
            continue
        saved.add(suggestion.header)
        session.add(
            MappingSuggestion(
                upload_id=upload.id,
                original_header=suggestion.header,
                suggested_field=suggestion.field if suggestion.field in open_fields else None,
                confidence=min(1.0, max(0.0, suggestion.confidence)),
                reason=suggestion.reason,
                status=SuggestionStatus.PENDING,
            )
        )
    session.flush()  # sessions here don't autoflush, and the next step queries them


def _pending_suggestion_covers(session: Session, upload: Upload, missing: list[str]) -> bool:
    """True if a pending suggestion proposes one of the `missing` required fields."""
    found = session.scalar(
        select(MappingSuggestion.id)
        .where(
            MappingSuggestion.upload_id == upload.id,
            MappingSuggestion.status == SuggestionStatus.PENDING,
            MappingSuggestion.suggested_field.in_(missing),
        )
        .limit(1)
    )
    return found is not None
