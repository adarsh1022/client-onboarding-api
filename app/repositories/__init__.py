"""Repositories: the only place that writes database queries.

Each function takes a Session, runs one query, and returns models. They never commit;
the service that called them decides when a unit of work is done.
"""
