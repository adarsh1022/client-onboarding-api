"""Pydantic models: the shapes of data moving through the app and the API."""

from datetime import date, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict


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
    status: str
    error_message: str | None
    total_rows: int
    valid_rows: int
    invalid_rows: int
    inserted: int
    updated: int
    created_at: datetime
    finished_at: datetime | None


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
