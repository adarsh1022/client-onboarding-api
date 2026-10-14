"""API behaviour that does not depend on the exercises."""

from app.models import Customer


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_rejects_unsupported_file_type(client):
    r = client.post("/uploads", files={"file": ("data.txt", b"hello", "text/plain")})
    assert r.status_code == 415


def test_rejects_empty_file(client):
    r = client.post("/uploads", files={"file": ("data.csv", b"", "text/csv")})
    assert r.status_code == 400


def test_rejects_oversized_file(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "max_upload_bytes", 10)
    r = client.post("/uploads", files={"file": ("data.csv", b"x" * 11, "text/csv")})
    assert r.status_code == 413


def test_unknown_upload_is_404(client):
    assert client.get("/uploads/999").status_code == 404
    assert client.get("/uploads/999/errors").status_code == 404


def test_list_customers_filters_and_paginates(client, session):
    session.add_all(
        [
            Customer(name="Asha Rao", email="asha@x.com", country_code="IN"),
            Customer(name="Ravi Kumar", email="ravi@x.com", country_code="IN"),
            Customer(name="John Smith", email="john@y.com", country_code="US"),
        ]
    )
    session.commit()

    body = client.get("/customers").json()
    assert body["total"] == 3

    body = client.get("/customers", params={"country": "in"}).json()
    assert body["total"] == 2
    assert {c["email"] for c in body["items"]} == {"asha@x.com", "ravi@x.com"}

    body = client.get("/customers", params={"q": "SMITH"}).json()
    assert [c["email"] for c in body["items"]] == ["john@y.com"]

    body = client.get("/customers", params={"limit": 1, "offset": 1}).json()
    assert body["total"] == 3 and len(body["items"]) == 1
    assert body["items"][0]["email"] == "ravi@x.com"
