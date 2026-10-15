"""A free, offline MappingSuggester: fuzzy header matching plus checks on sample values.

No network, no model, no API key. For every column and every still-open field it scores
two kinds of evidence and combines them:

* the header: how close it is to a field's aliases (HEADER_ALIASES plus the abbreviations
  and a few Hindi, German and Spanish words in SYNONYMS), by exact match, typo tolerance
  (difflib), shared words, or being an abbreviation ("Mob" -> "mobile");
* the values: do the samples look like emails, 10-15 digit phone numbers, dates, known
  countries, or capitalised multi-word names?

It is deliberately cautious: words like "ref", "remarks" or "birth" in a header (see
NEGATIVE_TOKENS) weaken the evidence, and a field is only suggested when the combined
score reaches ACCEPT_THRESHOLD. Otherwise the answer is "none". Each field goes to at
most one column. It knows far fewer words than a language model; see evals/RESULTS.md.

Samples reach it already masked (emails are "<email>", phone digits are "9"), which is
why the checks work on those shapes as well as on real values.
"""

import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher

from app.ingest.mapping import HEADER_ALIASES
from app.ingest.suggester import ColumnSample, ColumnSuggestion
from app.ingest.validation import COUNTRY_ALIASES, DATE_FORMATS, KNOWN_COUNTRY_CODES

ACCEPT_THRESHOLD = 0.6

# Words that mean "this is the field" in other spellings, languages and abbreviations.
# HEADER_ALIASES are always included too. Extend these as new files turn up.
SYNONYMS: dict[str, list[str]] = {
    "name": [
        "customer",
        "client",
        "client name",
        "contact name",
        "person",
        "nombre",
        "nombre completo",
        "nom",
        "naam",
        "kunde",
        "kundenname",
        "नाम",
        "पूरा नाम",
    ],  # fmt: skip
    "email": [
        "mail",
        "e mail",
        "email id",
        "mail id",
        "e mail id",
        "email adresse",
        "e mail adresse",
        "emailadresse",
        "correo",
        "correo electronico",
        "courriel",
        "ईमेल",
        "ई मेल",
    ],  # fmt: skip
    "phone": [
        "ph",
        "tel",
        "telephone",
        "cell",
        "cell phone",
        "mobile no",
        "mobile number",
        "contact number",
        "telephone number",
        "tel no",
        "tel number",
        "cell number",
        "telefon",
        "telefonnummer",
        "handy",
        "handynummer",
        "mobil",
        "mobilnummer",
        "telefono",
        "celular",
        "movil",
        "फोन",
        "फ़ोन",
        "मोबाइल",
        "मोबाइल नंबर",
    ],  # fmt: skip
    "country": [
        "ctry",
        "country of residence",
        "pais",
        "land",
        "pays",
        "देश",
        "desh",
    ],  # fmt: skip
    "signup_date": [
        "date of joining",
        "joining date",
        "date joined",
        "join date",
        "joining",
        "registered",
        "registered on",
        "registration",
        "signup",
        "signed up",
        "member since",
        "anmeldedatum",
        "registrierungsdatum",
        "beitrittsdatum",
        "fecha de registro",
        "fecha de alta",
        "fecha registro",
        "पंजीकरण तिथि",
        "पंजीकरण की तारीख",
    ],  # fmt: skip
}

# A header containing one of these is probably something else (an id, a note, a birth
# date...). It weakens value-based evidence and loose header matches, never exact ones.
NEGATIVE_TOKENS = {
    "id", "ref", "reference", "code", "uuid", "sku", "order", "invoice", "account", "acct",
    "remark", "remarks", "note", "notes", "comment", "comments", "status", "type",
    "category", "gender", "age", "address", "city", "state", "zip", "pin", "pincode",
    "plan", "amount", "price", "product", "company", "manager", "father", "mother",
    "spouse", "guardian", "emergency", "referrer", "agent", "verified", "flag",
    "birth", "dob", "born", "birthday", "anniversary", "expiry", "expires", "due",
    "renewal", "last", "updated", "modified", "login",
    "bemerkung", "bemerkungen", "anmerkung", "kommentar", "notizen", "geburtsdatum",
    "observaciones", "comentarios", "nota", "notas", "nacimiento",
    "टिप्पणी", "पता", "जन्म",
}  # fmt: skip

# Words that carry no meaning of their own, so sharing one doesn't make two headers alike.
STOPWORDS = {"of", "the", "de", "del", "la", "el", "der", "die", "das", "ka", "ki", "on", "at"}

# After a matched word these mean "the identifier of it" ("Customer No", "Country Code" aside),
# which is not the field itself, except for phone where "number" is the field ("Phone Nr").
ID_WORDS = {"id", "no", "nr", "num", "number"}

# How far to trust "the values look like X" on its own. Many things look like a name.
VALUE_WEIGHT = {"email": 0.95, "phone": 0.85, "signup_date": 0.9, "country": 0.85, "name": 0.55}

EXTRA_DATE_FORMATS = ["%d.%m.%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y"]

_SPLIT = re.compile(r"[\s_\-./,:;()\[\]{}#@!?*&+|\\]+")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_CHARS = re.compile(r"^[\d\s().+\-xX]+$")
_NAME_WORD = re.compile(r"^[^\W\d_]+(?:[.'’\-][^\W\d_]+)*\.?$")


def tokenize(text: str) -> list[str]:
    """Lowercase words with Latin accents removed (Teléfono -> telefono); other scripts kept."""
    text = unicodedata.normalize("NFKD", text).casefold()
    text = "".join(ch for ch in text if not "̀" <= ch <= "ͯ")
    return [token for token in _SPLIT.split(text) if token]


PHRASES: dict[str, list[list[str]]] = {
    field: [tokenize(alias) for alias in [*aliases, *SYNONYMS.get(field, [])]]
    for field, aliases in HEADER_ALIASES.items()
}


class HeuristicSuggester:
    """Suggests mappings from header similarity and the look of the sample values."""

    def suggest(self, columns: list[ColumnSample], fields: list[str]) -> list[ColumnSuggestion]:
        open_fields = [f for f in fields if f in PHRASES]
        evidence = {
            (index, field): _score(column, field)
            for index, column in enumerate(columns)
            for field in open_fields
        }

        # Best matches first; each column and each field is used at most once.
        taken_columns: set[int] = set()
        taken_fields: set[str] = set()
        chosen: dict[int, tuple[str, _Evidence]] = {}
        ranked = sorted(evidence.items(), key=lambda item: item[1].score, reverse=True)
        for (index, field), found in ranked:
            if found.score < ACCEPT_THRESHOLD:
                break
            if index in taken_columns or field in taken_fields:
                continue
            taken_columns.add(index)
            taken_fields.add(field)
            chosen[index] = (field, found)

        suggestions = []
        for index, column in enumerate(columns):
            if index in chosen:
                field, found = chosen[index]
                suggestions.append(
                    ColumnSuggestion(column.header, field, round(found.score, 2), found.reason)
                )
                continue
            best = max((evidence[index, f].score for f in open_fields), default=0.0)
            suggestions.append(
                ColumnSuggestion(
                    column.header,
                    None,
                    round(1 - best, 2),
                    "no field matched the header or the sample values well enough",
                )
            )
        return suggestions


class _Evidence:
    def __init__(self, score: float, reason: str):
        self.score = score
        self.reason = reason


def _score(column: ColumnSample, field: str) -> _Evidence:
    tokens = tokenize(column.header)
    negative = any(token in NEGATIVE_TOKENS for token in tokens)

    header, phrase = _header_score(
        tokens, PHRASES[field], negative, number_is_field=field == "phone"
    )
    values = _value_score(field, column.samples)
    values_effective = values * VALUE_WEIGHT[field] * (0.4 if negative else 1.0)

    score = min(1.0, max(header, values_effective) + 0.15 * min(header, values_effective))

    parts = []
    if header >= 0.5:
        parts.append(f"header '{column.header}' resembles '{phrase}' ({header:.2f})")
    if values >= 0.5:
        parts.append(f"{values:.0%} of the sample values look like {_DESCRIPTION[field]}")
    return _Evidence(score, "; ".join(parts) or "weak evidence only")


_DESCRIPTION = {
    "name": "names",
    "email": "email addresses",
    "phone": "10-15 digit phone numbers",
    "signup_date": "dates",
    "country": "countries",
}


def _header_score(
    tokens: list[str], phrases: list[list[str]], negative: bool, number_is_field: bool
) -> tuple[float, str]:
    best, best_phrase = 0.0, ""
    for phrase in phrases:
        score, exact = _phrase_score(tokens, phrase, number_is_field)
        if negative and not exact:
            score *= 0.5
        if score > best:
            best, best_phrase = score, " ".join(phrase)
    return best, best_phrase


def _phrase_score(
    header: list[str], phrase: list[str], number_is_field: bool
) -> tuple[float, bool]:
    """How well `header` matches `phrase`, and whether that was an exact/typo-level match."""
    if not header or not phrase:
        return 0.0, False
    header_text, phrase_text = " ".join(header), " ".join(phrase)
    if header_text == phrase_text:
        return 1.0, True

    if len(header) == len(phrase):  # a typo or small variation: "emial", "phone nr"
        ratios = [SequenceMatcher(None, a, b).ratio() for a, b in zip(header, phrase, strict=True)]
        if min(ratios) >= 0.75:  # every word must match: "customer id" is not "customer name"
            return sum(ratios) / len(ratios), True

    scores = [0.0]
    meaningful_header = set(header) - STOPWORDS
    meaningful_phrase = set(phrase) - STOPWORDS
    shared = meaningful_header & meaningful_phrase
    leftover = meaningful_header - meaningful_phrase
    is_identifier = bool(leftover) and leftover <= ID_WORDS and not number_is_field
    if shared and not is_identifier:
        scores.append(2 * len(shared) / (len(meaningful_header) + len(meaningful_phrase)))
        if meaningful_phrase <= meaningful_header:  # "customer mobile number" contains "mobile"
            scores.append(0.7)
    if len(header) == 1 and any(_is_abbreviation(header[0], word) for word in phrase):
        scores.append(0.75)
    return max(scores), False


def _is_abbreviation(short: str, word: str) -> bool:
    """True for "mob" -> "mobile" or "cntry" -> "country": same first letter, letters in order."""
    if len(short) < 3 or len(word) < 4 or short == word or short[0] != word[0]:
        return False
    if len(short) / len(word) < 0.4:
        return False
    letters = iter(word)
    return all(ch in letters for ch in short)  # each `in` consumes the iterator up to the match


def _value_score(field: str, samples: list[str]) -> float:
    """The share of samples that look like `field` (0 when there are no samples)."""
    values = [s.strip() for s in samples if s.strip()]
    if not values:
        return 0.0
    check = _VALUE_CHECKS[field]
    return sum(check(value) for value in values) / len(values)


def _is_date(value: str) -> bool:
    for date_format in [*DATE_FORMATS, *EXTRA_DATE_FORMATS]:
        try:
            datetime.strptime(value, date_format)
        except ValueError:
            continue
        return True
    return False


def _is_email(value: str) -> bool:
    return value == "<email>" or bool(_EMAIL.match(value))


def _is_phone(value: str) -> bool:
    digits = sum(ch.isdigit() for ch in value)
    return bool(_PHONE_CHARS.match(value)) and 10 <= digits <= 15 and not _is_date(value)


def _is_country(value: str) -> bool:
    return value.lower() in COUNTRY_ALIASES or (
        len(value) == 2 and value.upper() in KNOWN_COUNTRY_CODES
    )


def _name_likeness(value: str) -> float:
    """1 for a capitalised multi-word name, half for a single capitalised word, else 0."""
    words = value.split()
    if not 1 <= len(words) <= 4 or not all(_NAME_WORD.match(word) for word in words):
        return 0.0
    first = words[0][0]
    capitalised = first.isupper() or first.upper() == first.lower()  # caseless scripts pass
    if not capitalised:
        return 0.0
    return 1.0 if len(words) >= 2 else 0.5


_VALUE_CHECKS = {
    "email": _is_email,
    "phone": _is_phone,
    "signup_date": _is_date,
    "country": _is_country,
    "name": _name_likeness,
}
