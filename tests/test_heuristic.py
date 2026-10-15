"""HeuristicSuggester: header matching, value detection, conflicts, and no network."""

import socket
from datetime import date

import pytest
from sqlalchemy import select

from app.ingest.heuristic import HeuristicSuggester, tokenize
from app.ingest.pipeline import ingest
from app.ingest.reader import ParsedFile
from app.ingest.suggester import ColumnSample, mask_value
from app.models import Customer, MappingSuggestion, SuggestionStatus, Upload, UploadStatus

FIELDS = ["name", "email", "phone", "country", "signup_date"]


def column(header, *values):
    """A column as the pipeline hands it over: sample values already masked."""
    return ColumnSample(header, [mask_value(v) for v in values])


def suggest_one(header, *values, fields=FIELDS):
    (suggestion,) = HeuristicSuggester().suggest([column(header, *values)], fields)
    return suggestion


def test_tokenize_folds_latin_accents_and_keeps_other_scripts():
    assert tokenize("Teléfono") == ["telefono"]
    assert tokenize("E-mail  Address ") == ["e", "mail", "address"]
    assert tokenize("Straße") == ["strasse"]
    assert tokenize("नाम") == ["नाम"]  # Devanagari is kept intact, combining marks included
    assert tokenize("***") == []


# --- from the header alone ----------------------------------------------------------------


@pytest.mark.parametrize(
    "header, field",
    [
        ("Email", "email"),
        ("E-mail Address", "email"),
        ("Emial", "email"),  # typo
        ("Phone Nr", "phone"),
        ("Telephone Number", "phone"),
        ("Customer Mobile Number", "phone"),
        ("Mob", "phone"),  # abbreviation: not in the synonym list, found by the abbreviation rule
        ("Cntry", "country"),
        ("Date of Joining", "signup_date"),
        ("Full Nmae", "name"),
        ("Customer", "name"),
        ("Teléfono", "phone"),
        ("Fecha de registro", "signup_date"),
        ("Anmeldedatum", "signup_date"),
        ("Land", "country"),
        ("नाम", "name"),
        ("ईमेल", "email"),
        ("फ़ोन", "phone"),
    ],
)
def test_header_alone_is_enough_for_known_spellings(header, field):
    result = suggest_one(header)
    assert result.field == field, result.reason
    assert 0.6 <= result.confidence <= 1.0
    assert header in result.reason


@pytest.mark.parametrize(
    "header",
    ["Remarks", "Notes", "Ref Code", "Product Name", "Customer ID", "Customer No", "X", "***", ""],
)
def test_unrelated_headers_alone_are_none(header):
    result = suggest_one(header)
    assert result.field is None
    assert 0.0 <= result.confidence <= 1.0


# --- from the values ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "values, field",
    [
        (["asha@example.com", "john@y.org"], "email"),
        (["+91 98765-43210", "9123456780"], "phone"),
        (["(020) 7946-0958", "020 7946 0959"], "phone"),
        (["2024-03-01", "01/03/2024", "15-07-2023"], "signup_date"),
        (["01.03.2024", "15.07.2023"], "signup_date"),
        (["India", "us", "Germany"], "country"),
        (["IN", "US", "DE"], "country"),
    ],
)
def test_values_alone_are_enough_for_distinctive_formats(values, field):
    result = suggest_one("Col7", *values)
    assert result.field == field, result.reason
    assert "sample values look like" in result.reason


def test_names_are_not_guessed_from_values_alone():
    # Plenty of columns hold capitalised words, so values alone are too weak for "name"...
    assert suggest_one("Col7", "Asha Rao", "John Smith").field is None
    # ...but they confirm a header that is only loosely a name.
    assert suggest_one("Customer", "Asha Rao", "John Smith").confidence >= 0.95


def test_values_confirm_a_header_and_raise_confidence():
    without = suggest_one("Mob")
    with_values = suggest_one("Mob", "9876543210", "9123456780")
    assert with_values.field == without.field == "phone"
    assert with_values.confidence > without.confidence


def test_samples_are_judged_as_masked_by_the_pipeline():
    # The pipeline masks before suggesting, so an email column arrives as "<email>"
    # and a phone column as digits replaced by 9.
    assert suggest_one("Col7", "<email>").field == "email"
    assert suggest_one("Col7", "+99 99999-99999").field == "phone"


# --- things that look like a field but aren't ---------------------------------------------


@pytest.mark.parametrize(
    "header, values",
    [
        ("Ref Code", ["1234567890", "2345678901"]),  # ten digits, but a reference
        ("Date of Birth", ["1990-05-01", "1985-11-23"]),  # a date, but not the signup date
        ("Last Login", ["2025-01-02", "2025-02-03"]),
        ("Remarks", ["Called twice", "Needs Follow Up"]),  # capitalised words, but a note
        ("Product Name", ["Widget Pro", "Gadget Max"]),
        ("Customer ID", ["C-1001", "C-1002"]),
        ("Phone Verified", ["yes", "no"]),
    ],
)
def test_negative_words_stop_value_look_alikes(header, values):
    assert suggest_one(header, *values).field is None


def test_a_none_answer_is_more_confident_the_further_nothing_is_from_a_field():
    far = suggest_one("Zzzz", "qq")
    near = suggest_one("Phone Verified", "yes")  # vaguely phone-like, but a flag
    assert far.field is None and near.field is None
    assert far.confidence >= 0.9
    assert far.confidence > near.confidence


# --- several columns at once --------------------------------------------------------------


def test_each_field_goes_to_at_most_one_column():
    columns = [column("E-mail", "a@x.com"), column("Backup Email", "b@y.com")]
    result = HeuristicSuggester().suggest(columns, FIELDS)

    assert [s.header for s in result] == ["E-mail", "Backup Email"]  # input order kept
    assert [s.field for s in result].count("email") == 1
    assert result[0].field == "email"  # the better match wins
    assert result[1].field is None


def test_only_the_open_fields_are_considered():
    result = HeuristicSuggester().suggest(
        [column("Email", "a@x.com"), column("Mob", "9876543210")], ["phone"]
    )
    assert [s.field for s in result] == [None, "phone"]


def test_one_suggestion_per_column_with_sane_values():
    columns = [column("Mob", "9876543210"), column("Remarks", "hi"), column("Joined", "2024-01-01")]
    result = HeuristicSuggester().suggest(columns, FIELDS)

    assert [s.header for s in result] == ["Mob", "Remarks", "Joined"]
    assert [s.field for s in result] == ["phone", None, "signup_date"]
    assert all(0.0 <= s.confidence <= 1.0 and s.reason for s in result)


def test_no_columns_and_no_samples_are_fine():
    assert HeuristicSuggester().suggest([], FIELDS) == []
    assert suggest_one("Mob").field == "phone"


# --- offline, and inside the real pipeline ------------------------------------------------


def test_needs_no_network(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("the heuristic suggester must not use the network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)

    assert suggest_one("Mob", "9876543210").field == "phone"


def test_heuristic_suggester_inside_ingest_end_to_end(session):
    headers = ["Cust", "Mail ID", "Mob", "Cntry", "Remarks"]
    parsed = ParsedFile(
        headers=headers,
        rows=[
            dict(
                zip(headers, ["Asha Rao", "asha@x.com", "9876543210", "India", "vip"], strict=True)
            ),
            dict(zip(headers, ["John Smith", "john@y.com", "", "us", ""], strict=True)),
        ],
    )
    upload = Upload(filename="clients.csv")
    session.add(upload)
    session.commit()

    ingest(session, upload, parsed, today=date(2026, 1, 15), suggester=HeuristicSuggester())

    assert upload.status == UploadStatus.NEEDS_REVIEW
    found = {
        s.original_header: s
        for s in session.scalars(
            select(MappingSuggestion).where(MappingSuggestion.upload_id == upload.id)
        )
    }
    assert {h: s.suggested_field for h, s in found.items()} == {
        "Cust": "name",
        "Mail ID": "email",
        "Mob": "phone",
        "Cntry": "country",
        "Remarks": None,
    }

    for s in found.values():
        s.status = SuggestionStatus.APPROVED
    session.commit()
    ingest(session, upload, parsed, today=date(2026, 1, 15), suggester=HeuristicSuggester())

    assert upload.status == UploadStatus.COMPLETED
    asha = session.scalar(select(Customer).where(Customer.email == "asha@x.com"))
    assert (asha.name, asha.phone, asha.country_code) == ("Asha Rao", "9876543210", "IN")
