"""How ingest uses mapping suggestions: needs_review, approval, profiles, failures.

A fake suggester stands in for the model, so nothing here touches the network.
"""

from datetime import date

import pytest
from sqlalchemy import select

from app.ingest.mapping import MappingError, find_columns, map_columns
from app.ingest.pipeline import ingest
from app.ingest.reader import ParsedFile
from app.ingest.suggester import SuggestionError
from app.models import (
    Client,
    ClientMappingProfile,
    Customer,
    MappingSuggestion,
    RowError,
    SuggestionStatus,
    Upload,
    UploadStatus,
)
from tests.fakes import FakeSuggester

TODAY = date(2026, 1, 15)
HEADERS = ["Customer", "Mail ID", "Country"]  # "Country" is known; the other two aren't
ROWS = [["Asha Rao", "asha@x.com", "IN"], ["John Smith", "john@y.com", "us"]]


def make_file(headers=HEADERS, rows=ROWS):
    return ParsedFile(headers=headers, rows=[dict(zip(headers, r, strict=True)) for r in rows])


def new_upload(session, client_id=None):
    upload = Upload(filename="clients.csv", client_id=client_id)
    session.add(upload)
    session.commit()
    return upload


def suggestions_for(session, upload):
    return {
        s.original_header: s
        for s in session.scalars(
            select(MappingSuggestion).where(MappingSuggestion.upload_id == upload.id)
        )
    }


def approve(session, suggestion, field=None):
    suggestion.status = SuggestionStatus.APPROVED
    suggestion.approved_field = field
    session.commit()


# --- mapping order and the helpers behind it ---------------------------------------------


def test_extra_aliases_come_after_static_ones():
    extra = {"customer": "name", "email": "name"}  # the second one tries to override a static alias
    assert map_columns(["Customer", "Email", "Country"], extra_aliases=extra) == {
        "name": "Customer",
        "email": "Email",  # the static alias still decides "Email"
        "country": "Country",
    }


def test_find_columns_does_not_raise():
    assert find_columns(["Name", "E-mail", "Email", "Notes"]) == {
        "name": ["Name"],
        "email": ["E-mail", "Email"],
    }


def test_missing_error_lists_the_missing_fields_but_ambiguity_does_not():
    with pytest.raises(MappingError) as missing:
        map_columns(["Name", "Phone"])
    assert missing.value.missing == ["country", "email"]

    with pytest.raises(MappingError) as ambiguous:
        map_columns(["Name", "Email", "E-mail", "Country"])
    assert ambiguous.value.missing == []


# --- needs_review -------------------------------------------------------------------------


def test_required_field_only_matched_by_a_suggestion_needs_review(session):
    upload = new_upload(session)
    suggester = FakeSuggester()
    ingest(session, upload, make_file(), today=TODAY, suggester=suggester)

    assert upload.status == UploadStatus.NEEDS_REVIEW
    assert "missing required columns: email, name" in upload.error_message
    assert f"/uploads/{upload.id}/suggestions" in upload.error_message
    assert upload.finished_at is None
    assert session.scalar(select(Customer)) is None

    found = suggestions_for(session, upload)
    assert {h: (s.suggested_field, s.status) for h, s in found.items()} == {
        "Customer": ("name", SuggestionStatus.PENDING),
        "Mail ID": ("email", SuggestionStatus.PENDING),
    }
    assert found["Customer"].confidence == 0.95
    assert found["Customer"].reason == "holds people's names"


def test_suggester_is_only_asked_about_unknown_headers_and_unmapped_fields(session):
    suggester = FakeSuggester()
    ingest(session, new_upload(session), make_file(), today=TODAY, suggester=suggester)

    ((columns, fields),) = suggester.calls
    assert [c.header for c in columns] == ["Customer", "Mail ID"]  # not "Country"
    assert fields == ["name", "email", "phone", "signup_date"]  # not "country"
    assert columns[1].samples == ["<email>"]  # masked before it reaches the suggester


def test_suggestions_are_never_applied_until_approved(session):
    headers = ["Name", "Email", "Country", "Tel"]
    rows = [["Asha", "asha@x.com", "IN", "9876543210"]]
    suggester = FakeSuggester({"Tel": ("phone", 0.99, "phone numbers")})
    upload = new_upload(session)
    ingest(session, upload, make_file(headers, rows), today=TODAY, suggester=suggester)

    assert upload.status == UploadStatus.COMPLETED  # nothing required was missing
    assert suggestions_for(session, upload)["Tel"].status == SuggestionStatus.PENDING
    assert session.scalar(select(Customer)).phone is None


def test_a_suggestion_for_an_already_mapped_field_is_stored_as_unknown(session):
    headers = ["Name", "Email", "Country", "Alias"]
    suggester = FakeSuggester({"Alias": ("name", 0.8, "also a name")})  # name is taken
    upload = new_upload(session)
    ingest(session, upload, make_file(headers, [["A", "a@x.com", "IN", "Al"]]), suggester=suggester)

    assert "name" not in suggester.calls[0][1]
    assert suggestions_for(session, upload)["Alias"].suggested_field is None


def test_no_suggester_call_when_nothing_is_unknown_or_every_field_is_mapped(session):
    suggester = FakeSuggester()
    standard = make_file(["Name", "Email", "Country"], [["A", "a@x.com", "IN"]])
    ingest(session, new_upload(session), standard, today=TODAY, suggester=suggester)

    every_field = ["Name", "Email", "Phone", "Country", "Joined", "Notes"]
    row = [["A", "b@x.com", "", "IN", "", "hi"]]
    ingest(session, new_upload(session), make_file(every_field, row), suggester=suggester)

    assert suggester.calls == []


def test_without_a_suggester_behaviour_is_unchanged(session):
    upload = new_upload(session)
    ingest(session, upload, make_file(), today=TODAY)

    assert upload.status == UploadStatus.FAILED
    assert "missing required columns: email, name" in upload.error_message
    assert suggestions_for(session, upload) == {}


def test_a_failing_suggester_does_not_crash_the_upload(session):
    for error in (SuggestionError("boom"), RuntimeError("unexpected")):
        upload = new_upload(session)
        ingest(session, upload, make_file(), suggester=FakeSuggester(error=error))

        assert upload.status == UploadStatus.FAILED
        assert "missing required columns: email, name" in upload.error_message
        assert suggestions_for(session, upload) == {}


def test_suggestions_that_do_not_cover_a_required_field_still_fail_the_upload(session):
    suggester = FakeSuggester({"Customer": (None, 0.1, "no idea"), "Mail ID": (None, 0.1, "?")})
    upload = new_upload(session)
    ingest(session, upload, make_file(), suggester=suggester)

    assert upload.status == UploadStatus.FAILED
    assert len(suggestions_for(session, upload)) == 2  # kept, so a reviewer can still choose


# --- approve, then reprocess --------------------------------------------------------------


def test_reprocessing_without_approval_keeps_waiting_for_review(session):
    upload = new_upload(session)
    suggester = FakeSuggester()
    for _ in range(2):
        ingest(session, upload, make_file(), today=TODAY, suggester=suggester)

    assert upload.status == UploadStatus.NEEDS_REVIEW  # pending suggestions are not applied
    assert session.scalar(select(Customer)) is None
    found = suggestions_for(session, upload)
    assert [s.status for s in found.values()] == [SuggestionStatus.PENDING] * 2  # not duplicated
    assert len(suggester.calls) == 1


def test_reprocessing_does_not_apply_a_pending_optional_suggestion(session):
    headers = ["Name", "Email", "Country", "Tel"]
    rows = [["Asha", "asha@x.com", "IN", "9876543210"]]
    suggester = FakeSuggester({"Tel": ("phone", 0.99, "phone numbers")})
    upload = new_upload(session)
    for _ in range(2):
        ingest(session, upload, make_file(headers, rows), today=TODAY, suggester=suggester)

    assert session.scalar(select(Customer)).phone is None
    assert suggestions_for(session, upload)["Tel"].status == SuggestionStatus.PENDING

    approve(session, suggestions_for(session, upload)["Tel"])
    ingest(session, upload, make_file(headers, rows), today=TODAY, suggester=suggester)
    assert session.scalar(select(Customer)).phone == "9876543210"  # applied once approved


def test_approved_suggestions_are_used_when_the_upload_is_reprocessed(session):
    upload = new_upload(session)
    suggester = FakeSuggester()
    ingest(session, upload, make_file(), today=TODAY, suggester=suggester)
    assert upload.status == UploadStatus.NEEDS_REVIEW

    for suggestion in suggestions_for(session, upload).values():
        approve(session, suggestion)
    ingest(session, upload, make_file(), today=TODAY, suggester=suggester)

    assert upload.status == UploadStatus.COMPLETED
    assert upload.error_message is None and upload.finished_at is not None
    assert (upload.total_rows, upload.valid_rows, upload.inserted) == (2, 2, 2)
    assert session.scalar(select(Customer).where(Customer.email == "asha@x.com")).name == "Asha Rao"
    assert len(suggester.calls) == 1  # already-suggested headers are not asked about again


def test_the_reviewer_can_override_the_suggested_field(session):
    suggester = FakeSuggester({"Customer": (None, 0.2, "unsure"), "Mail ID": ("email", 0.9, "")})
    upload = new_upload(session)
    ingest(session, upload, make_file(), today=TODAY, suggester=suggester)
    assert upload.status == UploadStatus.NEEDS_REVIEW  # "Mail ID" -> email is pending, name missing

    found = suggestions_for(session, upload)
    approve(session, found["Customer"], field="name")
    approve(session, found["Mail ID"])
    ingest(session, upload, make_file(), today=TODAY, suggester=suggester)

    assert upload.status == UploadStatus.COMPLETED
    assert (
        session.scalar(select(Customer).where(Customer.email == "john@y.com")).name == "John Smith"
    )


def test_rejected_suggestions_are_not_applied_or_asked_again(session):
    upload = new_upload(session)
    suggester = FakeSuggester()
    ingest(session, upload, make_file(), today=TODAY, suggester=suggester)

    for suggestion in suggestions_for(session, upload).values():
        suggestion.status = SuggestionStatus.REJECTED
    session.commit()
    ingest(session, upload, make_file(), today=TODAY, suggester=suggester)

    assert upload.status == UploadStatus.FAILED
    assert "missing required columns" in upload.error_message
    assert len(suggester.calls) == 1


def test_an_override_that_creates_a_duplicate_column_fails_as_ambiguous(session):
    headers = ["Name", "Customer", "Email", "Country"]
    suggester = FakeSuggester({"Customer": (None, 0.1, "unsure")})
    upload = new_upload(session)
    row = [["Asha", "Asha R", "a@x.com", "IN"]]
    ingest(session, upload, make_file(headers, row), suggester=suggester)
    assert upload.status == UploadStatus.COMPLETED  # "Name" already covers the name field

    # A reviewer overrides the suggestion and says "Customer" is the name too.
    approve(session, suggestions_for(session, upload)["Customer"], field="name")
    ingest(session, upload, make_file(headers, row), suggester=suggester)

    assert upload.status == UploadStatus.FAILED
    assert "ambiguous" in upload.error_message and "name" in upload.error_message


def test_reprocessing_replaces_the_previous_results(session):
    headers = ["Name", "Email", "Country"]
    rows = [["Asha", "asha@x.com", "IN"], ["", "bad", "IN"]]
    upload = new_upload(session)
    for _ in range(2):
        ingest(session, upload, make_file(headers, rows), today=TODAY)

    assert (upload.total_rows, upload.invalid_rows, upload.inserted, upload.updated) == (2, 1, 0, 1)
    errors = session.scalars(select(RowError).where(RowError.upload_id == upload.id)).all()
    assert len(errors) == 2  # not 4: the first run's errors were cleared


# --- client profiles ----------------------------------------------------------------------


def make_client(session, name="Acme"):
    client = Client(name=name)
    session.add(client)
    session.commit()
    return client


def test_a_clients_approved_profile_is_used_without_calling_the_model(session):
    client = make_client(session)
    session.add_all(
        [
            ClientMappingProfile(
                client_id=client.id, normalized_header="customer", canonical_field="name"
            ),
            ClientMappingProfile(
                client_id=client.id, normalized_header="mail_id", canonical_field="email"
            ),
        ]
    )
    session.commit()

    suggester = FakeSuggester()
    upload = new_upload(session, client_id=client.id)
    ingest(session, upload, make_file(), today=TODAY, suggester=suggester)

    assert upload.status == UploadStatus.COMPLETED
    assert upload.inserted == 2
    assert suggester.calls == []
    assert suggestions_for(session, upload) == {}


def test_another_clients_profile_is_not_used(session):
    acme = make_client(session, "Acme")
    other = make_client(session, "Other")
    session.add(
        ClientMappingProfile(
            client_id=acme.id, normalized_header="customer", canonical_field="name"
        )
    )
    session.add(
        ClientMappingProfile(
            client_id=acme.id, normalized_header="mail_id", canonical_field="email"
        )
    )
    session.commit()

    upload = new_upload(session, client_id=other.id)
    ingest(session, upload, make_file(), today=TODAY, suggester=FakeSuggester())

    assert upload.status == UploadStatus.NEEDS_REVIEW  # fell through to the model


def test_static_aliases_beat_the_clients_profile(session):
    client = make_client(session)
    # A profile entry claiming "Email" is the name must not override the static alias.
    session.add(
        ClientMappingProfile(client_id=client.id, normalized_header="email", canonical_field="name")
    )
    session.add(
        ClientMappingProfile(
            client_id=client.id, normalized_header="customer", canonical_field="name"
        )
    )
    session.commit()

    headers = ["Customer", "Email", "Country"]
    upload = new_upload(session, client_id=client.id)
    ingest(session, upload, make_file(headers, [["Asha", "a@x.com", "IN"]]), today=TODAY)

    assert upload.status == UploadStatus.COMPLETED
    assert session.scalar(select(Customer)).email == "a@x.com"
