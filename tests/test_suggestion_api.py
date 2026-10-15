"""The AI-assisted mapping flow over HTTP: needs_review, approve/reject, reprocess, profiles.

The model is always a fake (see tests/fakes.py); nothing here touches the network.
"""

import json

import pytest

from app.ingest.suggester import ClaudeSuggester, SuggestionError, get_suggester
from app.main import app
from app.models import Upload, UploadStatus
from tests.fakes import FakeClient, FakeSuggester, entry, reply_with

CSV = b"Customer,Mail ID,Country\nAsha Rao,asha@x.com,IN\nJohn Smith,john@y.com,us\n"


@pytest.fixture
def suggester(client):
    """Replace the real suggester with a fake for the duration of a test."""
    fake = FakeSuggester()
    app.dependency_overrides[get_suggester] = lambda: fake
    return fake


def upload(client, content=CSV, client_id=None):
    data = {"client_id": str(client_id)} if client_id is not None else None
    r = client.post("/uploads", files={"file": ("clients.csv", content, "text/csv")}, data=data)
    assert r.status_code == 202, r.text
    return r.json()["id"]


def get_upload(client, upload_id):
    return client.get(f"/uploads/{upload_id}").json()


def suggestions(client, upload_id):
    r = client.get(f"/uploads/{upload_id}/suggestions")
    assert r.status_code == 200
    return {s["original_header"]: s for s in r.json()}


def create_client(client, name="Acme"):
    r = client.post("/clients", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --- the main flow ------------------------------------------------------------------------


def test_needs_review_then_approve_then_reprocess_then_profile_reuse(client, suggester):
    acme = create_client(client)
    first = upload(client, client_id=acme)

    body = get_upload(client, first)
    assert body["status"] == "needs_review"
    assert body["client_id"] == acme
    assert "missing required columns: email, name" in body["error_message"]
    assert "content" not in body  # the stored bytes are never part of the API response
    found = suggestions(client, first)
    assert {h: (s["suggested_field"], s["status"]) for h, s in found.items()} == {
        "Customer": ("name", "pending"),
        "Mail ID": ("email", "pending"),
    }
    assert found["Customer"]["confidence"] == 0.95
    assert found["Customer"]["reason"] == "holds people's names"

    approved = client.post(f"/suggestions/{found['Customer']['id']}/approve")
    assert approved.status_code == 200
    assert (approved.json()["status"], approved.json()["approved_field"]) == ("approved", "name")
    client.post(f"/suggestions/{found['Mail ID']['id']}/approve", json={"field": "email"})
    assert get_upload(client, first)["status"] == "needs_review"  # approving alone changes nothing

    reprocess = client.post(f"/uploads/{first}/reprocess")
    assert reprocess.status_code == 202
    done = get_upload(client, first)
    assert done["status"] == "completed", done
    assert (done["total_rows"], done["valid_rows"], done["inserted"]) == (2, 2, 2)
    assert done["error_message"] is None
    assert client.get("/customers").json()["total"] == 2

    # The approvals were saved to Acme's profile: its next file maps with no model call.
    second = upload(
        client, b"Customer,Mail ID,Country\nPriya Sharma,priya@x.com,IN\n", client_id=acme
    )
    assert get_upload(client, second)["status"] == "completed"
    assert get_upload(client, second)["inserted"] == 1
    assert suggestions(client, second) == {}
    assert len(suggester.calls) == 1  # only the first upload asked the model

    # A different client doesn't benefit from Acme's profile.
    other = upload(client, client_id=create_client(client, "Other"))
    assert get_upload(client, other)["status"] == "needs_review"
    assert len(suggester.calls) == 2


def test_approval_without_a_client_applies_to_that_upload_only(client, suggester):
    first = upload(client)
    for s in suggestions(client, first).values():
        assert client.post(f"/suggestions/{s['id']}/approve").status_code == 200
    client.post(f"/uploads/{first}/reprocess")
    assert get_upload(client, first)["status"] == "completed"

    again = upload(client)  # no client, so nothing was remembered
    assert get_upload(client, again)["status"] == "needs_review"
    assert len(suggester.calls) == 2


def test_rejecting_suggestions_leaves_the_upload_failed(client, suggester):
    first = upload(client)
    for s in suggestions(client, first).values():
        r = client.post(f"/suggestions/{s['id']}/reject")
        assert (r.status_code, r.json()["status"]) == (200, "rejected")

    client.post(f"/uploads/{first}/reprocess")
    body = get_upload(client, first)
    assert body["status"] == "failed"
    assert "missing required columns" in body["error_message"]
    assert len(suggester.calls) == 1  # rejected headers aren't asked about again


def test_reviewer_can_pick_a_field_when_the_model_had_none(client, suggester):
    suggester.answers = {"Customer": (None, 0.1, "no idea"), "Mail ID": ("email", 0.9, "emails")}
    first = upload(client)
    found = suggestions(client, first)
    assert found["Customer"]["suggested_field"] is None

    no_choice = client.post(f"/suggestions/{found['Customer']['id']}/approve")
    assert no_choice.status_code == 400
    bad_choice = client.post(
        f"/suggestions/{found['Customer']['id']}/approve", json={"field": "nonsense"}
    )
    assert bad_choice.status_code == 422
    chosen = client.post(
        f"/suggestions/{found['Customer']['id']}/approve", json={"field": "name"}
    ).json()
    assert (chosen["suggested_field"], chosen["approved_field"]) == (None, "name")

    client.post(f"/suggestions/{found['Mail ID']['id']}/approve")
    client.post(f"/uploads/{first}/reprocess")
    assert get_upload(client, first)["status"] == "completed"


# --- errors -------------------------------------------------------------------------------


def test_deciding_a_suggestion_twice_or_a_missing_one(client, suggester):
    found = suggestions(client, upload(client))
    suggestion_id = found["Customer"]["id"]

    assert client.post(f"/suggestions/{suggestion_id}/approve").status_code == 200
    assert client.post(f"/suggestions/{suggestion_id}/approve").status_code == 409
    assert client.post(f"/suggestions/{suggestion_id}/reject").status_code == 409
    assert client.post("/suggestions/9999/approve").status_code == 404
    assert client.post("/suggestions/9999/reject").status_code == 404


def test_unknown_upload_or_client(client, suggester):
    assert client.get("/uploads/999/suggestions").status_code == 404
    assert client.post("/uploads/999/reprocess").status_code == 404
    r = client.post(
        "/uploads", files={"file": ("a.csv", CSV, "text/csv")}, data={"client_id": "99"}
    )
    assert r.status_code == 404


def test_cannot_reprocess_while_processing_or_without_the_stored_file(client, session_factory):
    with session_factory() as s:
        busy = Upload(filename="a.csv", status=UploadStatus.PROCESSING, content=CSV)
        legacy = Upload(filename="b.csv", status=UploadStatus.FAILED, content=None)
        s.add_all([busy, legacy])
        s.commit()
        busy_id, legacy_id = busy.id, legacy.id

    assert client.post(f"/uploads/{busy_id}/reprocess").status_code == 409
    r = client.post(f"/uploads/{legacy_id}/reprocess")
    assert r.status_code == 409
    assert "not stored" in r.json()["detail"]


def test_client_names_are_unique_and_required(client):
    assert client.post("/clients", json={"name": "  Acme  "}).json()["name"] == "Acme"
    assert client.post("/clients", json={"name": "Acme"}).status_code == 409
    assert client.post("/clients", json={"name": "   "}).status_code == 422


def test_uploaded_bytes_are_stored_for_reprocessing(client, session_factory, suggester):
    upload_id = upload(client)
    with session_factory() as s:
        assert s.get(Upload, upload_id).content == CSV


# --- no API key, failing model, masking ---------------------------------------------------


def test_without_an_api_key_suggestions_are_skipped_gracefully(client):
    # No dependency override: the real get_suggester runs, and the test settings have no key.
    upload_id = upload(client)

    body = get_upload(client, upload_id)
    assert body["status"] == "failed"
    assert "missing required columns: email, name" in body["error_message"]
    assert suggestions(client, upload_id) == {}


def test_a_failing_model_does_not_break_the_upload(client, suggester):
    suggester.error = SuggestionError("the model is down")
    upload_id = upload(client)

    assert get_upload(client, upload_id)["status"] == "failed"
    assert suggestions(client, upload_id) == {}


def test_only_headers_and_masked_samples_reach_the_model(client):
    fake_client = FakeClient(reply_with([entry("Customer", "name"), entry("Mail ID", "email")]))
    app.dependency_overrides[get_suggester] = lambda: ClaudeSuggester(fake_client, model="m")
    content = (
        b"Customer,Mail ID,Phone Number,Country,Notes\nAsha Rao,asha@x.com,+91 98765-43210,IN,vip\n"
    )

    upload_id = upload(client, content)

    assert get_upload(client, upload_id)["status"] == "needs_review"
    (request,) = fake_client.requests
    sent = request["messages"][0]["content"]
    assert "asha@x.com" not in sent and "x.com" not in sent
    assert "98765" not in sent and "43210" not in sent
    body = json.loads(sent)
    # "Phone Number" and "Country" are recognised by alias, so they aren't sent at all.
    assert body["columns"] == [
        {"header": "Customer", "samples": ["Asha Rao"]},  # only emails and phones are masked
        {"header": "Mail ID", "samples": ["<email>"]},
        {"header": "Notes", "samples": ["vip"]},
    ]
    assert set(body["target_fields"]) == {"name", "email", "signup_date"}
