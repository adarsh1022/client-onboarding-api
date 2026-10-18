"""Services: the business rules of the API.

A service takes a Session and plain values, uses repositories for data, raises the errors
in `app.errors` when a rule is broken, and commits when its unit of work is done. It
knows nothing about HTTP.
"""
