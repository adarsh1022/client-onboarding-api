"""Pydantic models: the shapes of data moving through the app and the API."""

from datetime import date, datetime
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, StringConstraints


class CleanCustomer(BaseModel):
    """A row that passed validation, ready to be saved."""

    name: str
    email: str
    phone: str | None = None
    country_code: str
    signup_date: date | None = None


class FieldError(BaseModel):
    """One problem with one field of one row."""

    field: str
    message: str


# ---- API responses -------------------------------------------------------


class UploadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    client_id: int | None
    status: str
    error_message: str | None
    total_rows: int
    valid_rows: int
    invalid_rows: int
    inserted: int
    updated: int
    created_at: datetime
    finished_at: datetime | None


class ClientCreate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class ClientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class SuggestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    upload_id: int
    original_header: str
    suggested_field: str | None
    approved_field: str | None
    confidence: float
    reason: str
    status: str


class ApproveIn(BaseModel):
    """Optional body for approving a suggestion: `field` overrides the suggested one."""

    field: str | None = None


class RowErrorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    row_number: int
    field: str
    message: str


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    phone: str | None
    country_code: str
    signup_date: date | None


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int
