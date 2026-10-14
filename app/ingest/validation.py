"""EXERCISE 2 - Row validation and cleaning.

Takes one row (already mapped to canonical field names, raw string values)
and returns either a CleanCustomer or the list of everything wrong with it.

Run the tests for this exercise with:
    pytest tests/test_validation.py -v
"""

import re
from datetime import date, datetime

from app.schemas import CleanCustomer, FieldError

# Country names clients commonly write -> ISO 3166-1 alpha-2 code.
# Keys are lowercase. Two-letter codes are also accepted (see validate_row).
COUNTRY_ALIASES: dict[str, str] = {
    "india": "IN",
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "united kingdom": "GB",
    "uk": "GB",
    "germany": "DE",
    "france": "FR",
    "japan": "JP",
    "singapore": "SG",
    "australia": "AU",
}
KNOWN_COUNTRY_CODES = set(COUNTRY_ALIASES.values())

# Accepted date formats, tried in this order.
DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"]

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_row(
    row: dict[str, str], today: date | None = None
) -> tuple[CleanCustomer | None, list[FieldError]]:
    """Validate and clean one row.

    `row` has canonical keys ("name", "email", "phone", "country",
    "signup_date"); any key may be missing, which counts the same as "".
    `today` exists so tests can fix the date; use date.today() when it's None.

    Return (CleanCustomer, []) if the row is valid, otherwise (None, errors).
    Collect ALL errors in the row, don't stop at the first one.

    Field rules (the `field` of each FieldError is the name in quotes):

    "name"  (required)
      - Strip, and collapse runs of internal whitespace to one space.
      - Empty after that -> error "required".

    "email"  (required)
      - Strip and lowercase.
      - Empty -> "required".
      - Otherwise must match ^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$ -> else "invalid format".

    "phone"  (optional)
      - Empty -> None.
      - Keep only the digits. If the result has 10-15 digits, store that
        digit string (e.g. "+91 98765-43210" -> "919876543210").
      - Otherwise -> "must have 10-15 digits".

    "country"  (required) -> stored as CleanCustomer.country_code
      - Strip. Empty -> "required".
      - If it's 2 letters and its uppercase form is in KNOWN_COUNTRY_CODES,
        use that ("in" -> "IN").
      - Else look up its lowercase form in COUNTRY_ALIASES ("India" -> "IN").
      - Else -> "unknown country 'X'" where X is the stripped original value.

    "signup_date"  (optional)
      - Empty -> None.
      - Try each format in DATE_FORMATS; first one that parses wins.
      - None parse -> "unrecognized date 'X'" (X = stripped original value).
      - Parsed date after `today` -> "cannot be in the future".
    """
    today = today or date.today()
    errors: list[FieldError] = []

    def fail(field: str, message: str) -> None:
        errors.append(FieldError(field=field, message=message))

    name = " ".join((row.get("name") or "").split())
    if not name:
        fail("name", "required")

    email = (row.get("email") or "").strip().lower()
    if not email:
        fail("email", "required")
    elif not EMAIL_PATTERN.match(email):
        fail("email", "invalid format")

    phone: str | None = None
    raw_phone = (row.get("phone") or "").strip()
    if raw_phone:
        digits = re.sub(r"[^0-9]", "", raw_phone)
        if 10 <= len(digits) <= 15:
            phone = digits
        else:
            fail("phone", "must have 10-15 digits")

    country_code: str | None = None
    raw_country = (row.get("country") or "").strip()
    if not raw_country:
        fail("country", "required")
    elif len(raw_country) == 2 and raw_country.upper() in KNOWN_COUNTRY_CODES:
        country_code = raw_country.upper()
    elif raw_country.lower() in COUNTRY_ALIASES:
        country_code = COUNTRY_ALIASES[raw_country.lower()]
    else:
        fail("country", f"unknown country '{raw_country}'")

    signup_date: date | None = None
    raw_date = (row.get("signup_date") or "").strip()
    if raw_date:
        for date_format in DATE_FORMATS:
            try:
                signup_date = datetime.strptime(raw_date, date_format).date()
                break
            except ValueError:
                continue
        if signup_date is None:
            fail("signup_date", f"unrecognized date '{raw_date}'")
        elif signup_date > today:
            fail("signup_date", "cannot be in the future")

    if errors or country_code is None:
        return None, errors
    return (
        CleanCustomer(
            name=name,
            email=email,
            phone=phone,
            country_code=country_code,
            signup_date=signup_date,
        ),
        [],
    )
