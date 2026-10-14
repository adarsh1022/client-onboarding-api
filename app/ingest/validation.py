"""EXERCISE 2 - Row validation and cleaning.

Takes one row (already mapped to canonical field names, raw string values)
and returns either a CleanCustomer or the list of everything wrong with it.

Run the tests for this exercise with:
    pytest tests/test_validation.py -v
"""

from datetime import date

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
    raise NotImplementedError("Exercise 2: implement validate_row")
