"""OllamaSuggester: request shape, parsing, retries and errors.

Unit tests inject a fake transport. A few more run the real HTTP code against a tiny
server on 127.0.0.1, so nothing here needs Ollama installed or any network access.
"""

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.config import Settings
from app.ingest.ollama import OllamaSuggester, _Retryable
from app.ingest.suggester import ColumnSample, SuggestionError

FIELDS = ["name", "email", "phone"]
COLUMNS = [ColumnSample("Mail ID", ["<email>"]), ColumnSample("Mob", ["+99 99999-99999"])]


def chat_reply(suggestions, **extra):
    content = json.dumps({"suggestions": suggestions})
    return {
        "model": "m",
        "message": {"role": "assistant", "content": content},
        "done": True,
        **extra,
    }


def item(header, field, confidence=0.9, reason="fits"):
    return {"header": header, "field": field, "confidence": confidence, "reason": reason}


class FakePost:
    """Replays a list of outcomes (a dict reply, or an exception to raise) and records calls."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, url, body, timeout):
        self.calls.append((url, body, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def suggester(post, **kwargs):
    kwargs.setdefault("sleep", lambda seconds: None)
    return OllamaSuggester("http://ollama.test:11434/", "qwen-test", post=post, **kwargs)


# --- request and response -----------------------------------------------------------------


def test_request_is_a_non_streaming_schema_constrained_chat_call():
    post = FakePost(chat_reply([item("Mail ID", "email"), item("Mob", "phone")]))
    suggester(post, timeout=12).suggest(COLUMNS, FIELDS)

    ((url, body, timeout),) = post.calls
    assert url == "http://ollama.test:11434/api/chat"  # trailing slash handled
    assert timeout == 12
    assert body["model"] == "qwen-test"
    assert body["stream"] is False
    assert body["options"]["temperature"] == 0
    schema = body["format"]
    assert schema["properties"]["suggestions"]["items"]["properties"]["field"]["enum"] == [
        *FIELDS,
        "none",
    ]
    system, user = body["messages"]
    assert system["role"] == "system" and user["role"] == "user"
    assert json.loads(user["content"])["columns"] == [
        {"header": "Mail ID", "samples": ["<email>"]},
        {"header": "Mob", "samples": ["+99 99999-99999"]},
    ]


def test_parses_the_models_answer():
    post = FakePost(chat_reply([item("Mail ID", "email", 0.9, "emails"), item("Mob", "none", 0.2)]))
    result = {s.header: s for s in suggester(post).suggest(COLUMNS, FIELDS)}

    assert (result["Mail ID"].field, result["Mail ID"].confidence) == ("email", 0.9)
    assert result["Mail ID"].reason == "emails"
    assert result["Mob"].field is None


@pytest.mark.parametrize(
    "reply",
    [
        {"done": True},  # no message at all
        {"message": {"content": "not json"}, "done": True},
        {"message": {"content": '{"nope": 1}'}, "done": True},
        chat_reply([], done_reason="length"),  # the answer was cut off
    ],
    ids=["no_message", "not_json", "wrong_shape", "truncated"],
)
def test_unusable_replies_are_suggestion_errors(reply):
    with pytest.raises(SuggestionError):
        suggester(FakePost(reply)).suggest(COLUMNS, FIELDS)


# --- retries ------------------------------------------------------------------------------


def test_retries_then_succeeds_with_growing_delays():
    slept = []
    post = FakePost(_Retryable("down"), _Retryable("down"), chat_reply([item("Mob", "phone")]))
    result = suggester(post, retry_delay=1.0, sleep=slept.append).suggest(COLUMNS, FIELDS)

    assert len(post.calls) == 3
    assert slept == [1.0, 2.0]
    assert [s.field for s in result] == ["phone"]


def test_gives_up_after_max_retries():
    post = FakePost(*[_Retryable("down")] * 3)
    with pytest.raises(SuggestionError, match="not answering"):
        suggester(post, max_retries=2).suggest(COLUMNS, FIELDS)
    assert len(post.calls) == 3  # one try plus two retries


def test_no_retries_when_max_retries_is_zero():
    post = FakePost(_Retryable("down"))
    with pytest.raises(SuggestionError):
        suggester(post, max_retries=0).suggest(COLUMNS, FIELDS)
    assert len(post.calls) == 1


def test_a_rejected_request_is_not_retried():
    post = FakePost(SuggestionError("Ollama rejected the request: HTTP 404"))
    with pytest.raises(SuggestionError, match="404"):
        suggester(post).suggest(COLUMNS, FIELDS)
    assert len(post.calls) == 1


# --- the real HTTP code, against a throwaway local server ---------------------------------


class StubOllama:
    """A throwaway HTTP server on 127.0.0.1 that answers /api/chat from a script."""

    def __init__(self, *script):
        self.script = list(script)  # each: (status, json body, seconds to wait first)
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                owner.requests.append((self.path, json.loads(self.rfile.read(length))))
                status, body, delay = owner.script.pop(0)
                time.sleep(delay)
                data = json.dumps(body).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass  # the client gave up (timeout) before we answered

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stub():
    made = []

    def make(*script):
        made.append(StubOllama(*script))
        return made[-1]

    yield make
    for s in made:
        s.close()


def real(url, **kwargs):
    return OllamaSuggester(url, "qwen-test", retry_delay=0, **kwargs)


def test_real_http_round_trip(stub):
    server = stub((200, chat_reply([item("Mail ID", "email")]), 0))
    result = real(server.url).suggest(COLUMNS, FIELDS)

    assert [(s.header, s.field) for s in result] == [("Mail ID", "email")]
    ((path, body),) = server.requests
    assert path == "/api/chat" and body["model"] == "qwen-test"


def test_real_http_retries_a_500(stub):
    server = stub((500, {"error": "busy"}, 0), (200, chat_reply([item("Mob", "phone")]), 0))
    result = real(server.url).suggest(COLUMNS, FIELDS)

    assert [s.field for s in result] == ["phone"]
    assert len(server.requests) == 2


def test_real_http_model_not_pulled_is_a_clear_error_and_not_retried(stub):
    server = stub((404, {"error": "model 'qwen-test' not found"}, 0))
    with pytest.raises(SuggestionError, match="not found.*ollama pull"):
        real(server.url).suggest(COLUMNS, FIELDS)
    assert len(server.requests) == 1


def test_real_http_timeout_is_retried_then_reported(stub):
    server = stub(*[(200, chat_reply([]), 0.6)] * 2)
    with pytest.raises(SuggestionError, match="not answering"):
        real(server.url, timeout=0.15, max_retries=1).suggest(COLUMNS, FIELDS)
    assert len(server.requests) == 2


def test_real_http_nothing_listening():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]  # a free port that nothing listens on once we close it
    with pytest.raises(SuggestionError, match="not answering"):
        real(f"http://127.0.0.1:{port}", max_retries=1).suggest(COLUMNS, FIELDS)


def test_real_http_non_json_reply(stub):
    server = stub((200, "not an object", 0))  # valid JSON, but not what Ollama sends
    with pytest.raises(SuggestionError):
        real(server.url).suggest(COLUMNS, FIELDS)


# --- configuration ------------------------------------------------------------------------


def test_defaults_point_at_a_local_ollama_with_qwen(monkeypatch):
    for name in ("OLLAMA_URL", "OLLAMA_MODEL", "OLLAMA_TIMEOUT_SECONDS", "OLLAMA_MAX_RETRIES"):
        monkeypatch.delenv(name, raising=False)
    config = Settings(_env_file=None)

    assert config.ollama_url == "http://localhost:11434"
    assert config.ollama_model == "qwen2.5:3b"


def test_settings_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://gpu-box:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2:1b")
    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("OLLAMA_MAX_RETRIES", "4")
    built = OllamaSuggester.from_settings(Settings(_env_file=None))

    assert built._url == "http://gpu-box:11434/api/chat"
    assert (built._model, built._timeout, built._max_retries) == ("llama3.2:1b", 5.0, 4)
