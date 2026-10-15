"""Test doubles for the model, so no test ever needs the real Anthropic API."""

import json
from types import SimpleNamespace

from app.ingest.suggester import ColumnSample, ColumnSuggestion

# What the fake model "says" about the headers in most tests: {header: (field, confidence, reason)}
DEFAULT_ANSWERS = {
    "Customer": ("name", 0.95, "holds people's names"),
    "Mail ID": ("email", 0.9, "emails"),
}


class FakeSuggester:
    """A MappingSuggester that answers from a canned dict and records every call."""

    def __init__(self, answers=None, error=None):
        self.answers = answers if answers is not None else DEFAULT_ANSWERS
        self.error = error
        self.calls: list[tuple[list[ColumnSample], list[str]]] = []

    def suggest(self, columns, fields):
        self.calls.append((columns, fields))
        if self.error:
            raise self.error
        return [
            ColumnSuggestion(c.header, *self.answers[c.header])
            for c in columns
            if c.header in self.answers
        ]


class FakeClient:
    """Stands in for anthropic.Anthropic: records requests, replays a canned reply."""

    def __init__(self, reply=None, error=None):
        self.requests = []
        self.reply = reply
        self.error = error
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        return self.reply


def reply_with(suggestions, stop_reason="end_turn"):
    """A fake Messages API response whose text is the JSON the schema asks for."""
    text = json.dumps({"suggestions": suggestions})
    return SimpleNamespace(
        stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)]
    )


def entry(header, field, confidence=0.9, reason="looks right"):
    return {"header": header, "field": field, "confidence": confidence, "reason": reason}
