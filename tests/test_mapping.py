"""Tests for header normalization and column mapping."""

import pytest

from app.ingest.mapping import MappingError, map_columns, normalize_header


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Email", "email"),
        ("E-mail Address ", "e_mail_address"),
        ("Phone No.", "phone_no"),
        ("  Cust   Name", "cust_name"),
        ("Sign-Up Date", "sign_up_date"),
        ("__Country__", "country"),
        ("***", ""),
        ("", ""),
    ],
)
def test_normalize_header(raw, expected):
    assert normalize_header(raw) == expected


def test_maps_messy_headers_and_ignores_unknown():
    headers = ["Cust Name", "E-mail", "Mobile", "Country", "Joined On", "Notes"]
    assert map_columns(headers) == {
        "name": "Cust Name",
        "email": "E-mail",
        "phone": "Mobile",
        "country": "Country",
        "signup_date": "Joined On",
    }


def test_optional_columns_may_be_absent():
    assert map_columns(["Name", "Email", "Country"]) == {
        "name": "Name",
        "email": "Email",
        "country": "Country",
    }


def test_missing_required_columns_listed_alphabetically():
    with pytest.raises(MappingError) as exc:
        map_columns(["Name", "Phone"])
    assert "missing required columns: country, email" in str(exc.value)


def test_two_headers_for_one_field_is_ambiguous():
    with pytest.raises(MappingError) as exc:
        map_columns(["Name", "Email", "E-mail Address", "Country"])
    msg = str(exc.value)
    assert "ambiguous" in msg and "email" in msg
