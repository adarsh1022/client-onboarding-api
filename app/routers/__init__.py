"""Routers: the HTTP layer. They parse requests, call a service, and shape the response.

Which routers need an API key is decided here, in one place. Add a new endpoint's router
to `protected_routers` unless it is meant to be open to anyone; a test walks the OpenAPI
schema and fails on any unprotected route that isn't in its short public list.
"""

from fastapi import APIRouter

from app.routers import clients, customers, health, suggestions, uploads

public_routers: list[APIRouter] = [health.router]
protected_routers: list[APIRouter] = [
    clients.router,
    uploads.router,
    suggestions.router,
    customers.router,
]
