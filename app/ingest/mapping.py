"""Column mapping.

Clients never send the same column names. One sends "E-mail Address", another
"email", another "Mail". This module maps whatever headers arrive onto our
canonical field names.
"""

import re

# Canonical field -> accepted header spellings, already normalized
# (see normalize_header). Extend these lists if a client sends something new.
HEADER_ALIASES: dict[str, list[str]] = {
    "name": ["name", "customer_name", "cust_name", "full_name"],
    "email": ["email", "e_mail", "email_address", "e_mail_address", "mail"],
    "phone": ["phone", "phone_no", "phone_number", "mobile", "contact_no"],
    "country": ["country", "country_code", "nation"],
    "signup_date": ["signup_date", "sign_up_date", "joined", "joined_on", "registration_date"],
}

REQUIRED_FIELDS = {"name", "email", "country"}


class MappingError(ValueError):
    """The file's headers can't be mapped. The message is shown to the client."""


def normalize_header(header: str) -> str:
    """Turn a raw header into a comparable key.

    Rules:
      1. Lowercase it and strip surrounding whitespace.
      2. Replace every run of characters that are not a-z or 0-9 with ONE "_".
      3. Strip "_" from both ends.

    Examples:
      "E-mail Address "  -> "e_mail_address"
      "Phone No."        -> "phone_no"
      "  Cust   Name"    -> "cust_name"
      "Sign-Up Date"     -> "sign_up_date"
      "***"              -> ""

    Hint: the `re` module.
    """
    lowered = header.lower().strip()
    return re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")


def map_columns(headers: list[str]) -> dict[str, str]:
    """Map canonical field names to the ORIGINAL header that holds them.

    Example:
      map_columns(["Cust Name", "E-mail", "Country", "Notes"])
      -> {"name": "Cust Name", "email": "E-mail", "country": "Country"}

    Rules:
      - Compare using normalize_header(header) against HEADER_ALIASES.
      - Headers that match no alias are ignored ("Notes" above).
      - If two headers map to the same field, raise MappingError with a message
        that contains the word "ambiguous" and the field name.
      - If any REQUIRED_FIELDS are missing, raise MappingError whose message
        contains "missing required columns:" followed by the missing field
        names, sorted alphabetically and joined by ", ".
        e.g. "missing required columns: country, email"
      - Check ambiguity first, then missing columns.
    """
    alias_to_field = {
        alias: field for field, aliases in HEADER_ALIASES.items() for alias in aliases
    }

    matches: dict[str, list[str]] = {}
    for header in headers:
        field = alias_to_field.get(normalize_header(header))
        if field is not None:
            matches.setdefault(field, []).append(header)

    for field, originals in matches.items():
        if len(originals) > 1:
            raise MappingError(f"ambiguous column for '{field}': {', '.join(originals)}")

    missing = sorted(REQUIRED_FIELDS - matches.keys())
    if missing:
        raise MappingError(f"missing required columns: {', '.join(missing)}")

    return {field: originals[0] for field, originals in matches.items()}
