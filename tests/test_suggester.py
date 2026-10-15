"""The LLM suggester: masking, what gets sent, parsing, errors and configuration.

These never touch the network: ClaudeSuggester is given a fake client.
"""

import inspect
import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.config import Settings
from app.ingest.suggester import (
    ClaudeSuggester,
    ColumnSample,
    SuggestionError,
    get_suggester,
    mask_value,
    sample_columns,
)
from tests.fakes import FakeClient, entry, reply_with

FIELDS = ["name", "email", "phone"]


@pytest.mark.parametrize(
    "raw, masked",
    [
        ("asha@example.com", "<email>"),
        ("Contact: asha@example.com today", "Contact: <email> today"),
        ("+91 98765-43210", "+99 99999-99999"),
        ("(020) 7946-0958x", "(999) 9999-9999x"),
        ("9876543210", "9999999999"),
        ("2024-03-01", "2024-03-01"),  # dates carry no personal data and help the model
        ("01/03/2024", "01/03/2024"),
        ("01.03.2024", "01.03.2024"),  # a German-style date keeps its shape too
        ("2024/03/01", "2024/03/01"),
        ("2024-03-01 10:15:00", "2024-03-01 10:15:00"),
        ("020.7946.0958", "999.9999.9999"),  # dots alone don't make a phone number a date
        ("Asha Rao", "Asha Rao"),
        ("IN", "IN"),
        ("  padded  ", "padded"),
        ("x" * 200, "x" * 60),
    ],
)
def test_mask_value(raw, masked):
    assert mask_value(raw) == masked


def test_sample_columns_are_masked_distinct_limited_and_skip_blanks():
    rows = [
        {"Mail": "a@x.com", "Tel": "+91 98765-43210", "Ref": ""},
        {"Mail": "b@y.com", "Tel": "+91 91234-56789", "Ref": "K-1"},
        {"Mail": "", "Tel": "12", "Ref": "K-2"},
        {"Mail": "c@z.com", "Tel": "34", "Ref": "K-3"},
        {"Mail": "d@w.com", "Tel": "56", "Ref": "K-4"},
        {"Mail": "e@v.com", "Tel": "78", "Ref": "K-5"},
    ]
    columns = {c.header: c.samples for c in sample_columns(["Mail", "Tel", "Ref"], rows)}

    assert columns["Mail"] == ["<email>"]  # five emails, all masked to the same thing
    assert columns["Tel"] == ["+99 99999-99999", "12", "34"]  # distinct, at most 3
    assert columns["Ref"] == ["K-1", "K-2", "K-3"]  # blank skipped


def test_sample_columns_only_includes_requested_headers():
    columns = sample_columns(["Mail"], [{"Mail": "a@x.com", "Name": "Asha Rao"}])
    assert [c.header for c in columns] == ["Mail"]


def test_request_has_only_headers_and_masked_samples():
    client = FakeClient(reply_with([entry("Mail ID", "email"), entry("Tel", "phone")]))
    rows = [{"Mail ID": "asha.rao@example.com", "Tel": "+91 98765-43210", "Other": "secret"}]
    columns = sample_columns(["Mail ID", "Tel"], rows)

    ClaudeSuggester(client, model="test-model").suggest(columns, ["email", "phone"])

    (request,) = client.requests
    assert request["model"] == "test-model"
    sent = request["messages"][0]["content"]
    assert "asha.rao" not in sent and "example.com" not in sent
    assert "98765" not in sent and "43210" not in sent
    assert "secret" not in sent and "Other" not in sent  # columns we didn't ask about
    body = json.loads(sent)
    assert body["columns"] == [
        {"header": "Mail ID", "samples": ["<email>"]},
        {"header": "Tel", "samples": ["+99 99999-99999"]},
    ]
    assert set(body["target_fields"]) == {"email", "phone"}


def test_request_asks_for_schema_constrained_output():
    client = FakeClient(reply_with([]))
    ClaudeSuggester(client, model="m").suggest([ColumnSample("Mail ID", ["<email>"])], FIELDS)

    schema = client.requests[0]["output_config"]["format"]["schema"]
    assert client.requests[0]["output_config"]["format"]["type"] == "json_schema"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["suggestions"]["items"]["properties"]["field"]["enum"] == [
        *FIELDS,
        "none",
    ]


def test_parses_suggestions_and_cleans_up_model_output():
    client = FakeClient(
        reply_with(
            [
                entry("Mail ID", "email", 0.93, "holds email addresses"),
                entry("Ref", "none", 0.2, "unclear"),
                entry("Tel", "country", 1.7, "not an allowed field"),  # not in FIELDS
                entry("Tel", "phone", 0.5),  # repeated header: first one wins
                entry("Invented", "name", 0.9),  # not a column we sent
                entry("Score", "name", -3, "x" * 1000),
            ]
        )
    )
    columns = [ColumnSample(h) for h in ("Mail ID", "Ref", "Tel", "Score")]
    result = {s.header: s for s in ClaudeSuggester(client, model="m").suggest(columns, FIELDS)}

    assert set(result) == {"Mail ID", "Ref", "Tel", "Score"}
    assert (result["Mail ID"].field, result["Mail ID"].confidence) == ("email", 0.93)
    assert result["Ref"].field is None
    assert (result["Tel"].field, result["Tel"].confidence) == (None, 1.0)  # clamped
    assert result["Score"].confidence == 0.0  # clamped
    assert len(result["Score"].reason) == 300


@pytest.mark.parametrize(
    "client",
    [
        FakeClient(
            error=anthropic.APITimeoutError(request=httpx2.Request("POST", "https://x.test"))
        ),
        FakeClient(reply_with([], stop_reason="refusal")),
        FakeClient(reply_with([], stop_reason="max_tokens")),
        FakeClient(SimpleNamespace(stop_reason="end_turn", content=[])),
        FakeClient(
            SimpleNamespace(
                stop_reason="end_turn", content=[SimpleNamespace(type="text", text="not json")]
            )
        ),
        FakeClient(
            SimpleNamespace(
                stop_reason="end_turn", content=[SimpleNamespace(type="text", text='{"a": 1}')]
            )
        ),
    ],
    ids=["timeout", "refusal", "max_tokens", "no_text", "bad_json", "wrong_shape"],
)
def test_failures_become_suggestion_errors(client):
    with pytest.raises(SuggestionError):
        ClaudeSuggester(client, model="m").suggest([ColumnSample("Mail ID")], FIELDS)


def test_no_api_key_means_no_suggester():
    assert ClaudeSuggester.from_settings(Settings(anthropic_api_key=None, _env_file=None)) is None
    assert ClaudeSuggester.from_settings(Settings(anthropic_api_key="", _env_file=None)) is None
    assert get_suggester() is None  # the conftest fixture removes any key from the environment


def test_client_is_built_with_timeout_retries_and_model():
    config = Settings(
        anthropic_api_key="test-key",
        llm_model="some-model",
        llm_timeout_seconds=7,
        llm_max_retries=3,
        _env_file=None,
    )
    suggester = ClaudeSuggester.from_settings(config)

    assert suggester._model == "some-model"
    assert suggester._client.timeout == 7
    assert suggester._client.max_retries == 3


def test_model_defaults_to_haiku_and_reads_llm_model_env(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert Settings(_env_file=None).llm_model == "claude-haiku-5-5"

    monkeypatch.setenv("LLM_MODEL", "another-model")
    assert Settings(_env_file=None).llm_model == "another-model"


def test_sdk_still_accepts_the_parameters_we_send():
    # Guards against SDK drift without making a network call.
    create = anthropic.Anthropic(api_key="test-key").messages.create
    assert {"output_config", "system", "max_tokens"} <= set(inspect.signature(create).parameters)
