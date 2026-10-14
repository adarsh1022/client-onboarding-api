"""Full flow through the HTTP API, using the sample file in samples/."""

from pathlib import Path

SAMPLE = Path(__file__).parent.parent / "samples" / "messy_customers.csv"


def test_upload_sample_file_end_to_end(client):
    r = client.post("/uploads", files={"file": (SAMPLE.name, SAMPLE.read_bytes(), "text/csv")})
    assert r.status_code == 202
    upload_id = r.json()["id"]

    # TestClient runs background tasks before returning, so it's done already.
    upload = client.get(f"/uploads/{upload_id}").json()
    assert upload["status"] == "completed", upload
    assert upload["total_rows"] == 10
    assert upload["valid_rows"] == 6
    assert upload["invalid_rows"] == 4
    assert upload["inserted"] == 6

    errors = client.get(f"/uploads/{upload_id}/errors").json()
    assert errors["total"] == 5  # row 8 has two problems
    assert {e["row_number"] for e in errors["items"]} == {5, 8, 10, 12}

    customers = client.get("/customers", params={"country": "IN"}).json()
    assert customers["total"] == 3


def test_upload_with_missing_columns_is_failed(client):
    content = b"Name,Phone\nAsha,9876543210\n"
    r = client.post("/uploads", files={"file": ("bad.csv", content, "text/csv")})
    upload = client.get(f"/uploads/{r.json()['id']}").json()
    assert upload["status"] == "failed"
    assert "missing required columns" in upload["error_message"]
