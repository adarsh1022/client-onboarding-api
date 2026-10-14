"""Tests for row validation and cleaning."""

from datetime import date

import pytest

from app.ingest.validation import validate_row

TODAY = date(2026, 1, 15)


def row(**overrides):
    base = {
        "name": "Asha Rao",
        "email": "asha@example.com",
        "phone": "",
        "country": "IN",
        "signup_date": "",
    }
    base.update(overrides)
    return base


def errors_by_field(errors):
    return {e.field: e.message for e in errors}


def test_valid_row_is_cleaned():
    clean, errors = validate_row(
        row(
            name="  Asha    Rao ",
            email=" Asha@Example.COM ",
            phone="+91 98765-43210",
            country=" india ",
            signup_date="01/03/2024",
        ),
        today=TODAY,
    )
    assert errors == []
    assert clean.name == "Asha Rao"
    assert clean.email == "asha@example.com"
    assert clean.phone == "919876543210"
    assert clean.country_code == "IN"
    assert clean.signup_date == date(2024, 3, 1)


def test_missing_keys_count_as_blank():
    clean, errors = validate_row({"name": "A", "email": "a@b.co", "country": "us"}, today=TODAY)
    assert errors == []
    assert clean.phone is None and clean.signup_date is None and clean.country_code == "US"


def test_collects_all_errors_not_just_first():
    clean, errors = validate_row(row(name="  ", email="not-an-email", country=""), today=TODAY)
    assert clean is None
    assert errors_by_field(errors) == {
        "name": "required",
        "email": "invalid format",
        "country": "required",
    }


@pytest.mark.parametrize("email", ["", "   "])
def test_email_required(email):
    _, errors = validate_row(row(email=email), today=TODAY)
    assert errors_by_field(errors) == {"email": "required"}


@pytest.mark.parametrize("email", ["a@b", "a b@c.com", "@c.com", "a@@c.com"])
def test_email_invalid(email):
    _, errors = validate_row(row(email=email), today=TODAY)
    assert errors_by_field(errors) == {"email": "invalid format"}


@pytest.mark.parametrize(
    "phone, expected",
    [("98765 43210", "9876543210"), ("(020) 7946-0958x", "02079460958"), ("", None)],
)
def test_phone_cleaning(phone, expected):
    clean, errors = validate_row(row(phone=phone), today=TODAY)
    assert errors == [] and clean.phone == expected


@pytest.mark.parametrize("phone", ["12345", "1" * 16])
def test_phone_wrong_length(phone):
    _, errors = validate_row(row(phone=phone), today=TODAY)
    assert errors_by_field(errors) == {"phone": "must have 10-15 digits"}


@pytest.mark.parametrize(
    "country, code", [("in", "IN"), ("USA", "US"), ("United Kingdom", "GB"), ("uk", "GB")]
)
def test_country_codes_and_names(country, code):
    clean, errors = validate_row(row(country=country), today=TODAY)
    assert errors == [] and clean.country_code == code


def test_unknown_country():
    _, errors = validate_row(row(country=" Narnia "), today=TODAY)
    assert errors_by_field(errors) == {"country": "unknown country 'Narnia'"}


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2024-03-01", date(2024, 3, 1)),
        ("01/03/2024", date(2024, 3, 1)),
        ("01-03-2024", date(2024, 3, 1)),
    ],
)
def test_date_formats(raw, expected):
    clean, errors = validate_row(row(signup_date=raw), today=TODAY)
    assert errors == [] and clean.signup_date == expected


def test_unrecognized_date():
    _, errors = validate_row(row(signup_date=" March 1st "), today=TODAY)
    assert errors_by_field(errors) == {"signup_date": "unrecognized date 'March 1st'"}


def test_future_date_rejected_but_today_allowed():
    _, errors = validate_row(row(signup_date="2026-01-16"), today=TODAY)
    assert errors_by_field(errors) == {"signup_date": "cannot be in the future"}
    clean, errors = validate_row(row(signup_date="2026-01-15"), today=TODAY)
    assert errors == []
