"""Choosing a suggester with the SUGGESTER setting."""

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.ingest.factory import build_suggester
from app.ingest.heuristic import HeuristicSuggester
from app.ingest.ollama import OllamaSuggester
from app.ingest.suggester import ClaudeSuggester


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("SUGGESTER", "ANTHROPIC_API_KEY", "OLLAMA_URL", "OLLAMA_MODEL"):
        monkeypatch.delenv(name, raising=False)


def make(**values):
    return Settings(_env_file=None, **values)


def test_default_is_the_free_heuristic_suggester():
    assert make().suggester == "heuristic"
    assert isinstance(build_suggester(make()), HeuristicSuggester)


def test_an_api_key_alone_does_not_switch_on_the_paid_suggester():
    assert isinstance(build_suggester(make(anthropic_api_key="sk-test")), HeuristicSuggester)


def test_ollama_uses_the_ollama_settings():
    built = build_suggester(
        make(suggester="ollama", ollama_url="http://box:11434", ollama_model="tiny:1b")
    )
    assert isinstance(built, OllamaSuggester)
    assert built._url == "http://box:11434/api/chat" and built._model == "tiny:1b"


def test_claude_needs_a_key_and_is_skipped_without_one():
    assert build_suggester(make(suggester="claude")) is None
    assert isinstance(
        build_suggester(make(suggester="claude", anthropic_api_key="sk-test")), ClaudeSuggester
    )


def test_suggester_is_read_from_the_environment_case_insensitively(monkeypatch):
    monkeypatch.setenv("SUGGESTER", " Ollama ")
    assert make().suggester == "ollama"


def test_an_unknown_suggester_is_rejected_at_startup():
    with pytest.raises(ValidationError):
        make(suggester="gpt")
