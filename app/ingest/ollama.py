"""Suggest column mappings with a local Ollama server: free, offline, no API key.

Ollama (https://ollama.com) runs open models on your own machine. This talks to its
`/api/chat` endpoint with a JSON-schema constrained response, a timeout and retries.
Only the standard library is used for HTTP, so there is no extra dependency.
"""

import http.client
import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from app.config import Settings, settings
from app.ingest.suggester import (
    SYSTEM_PROMPT,
    ColumnSample,
    ColumnSuggestion,
    SuggestionError,
    build_payload,
    parse_suggestions,
    response_schema,
)

log = logging.getLogger(__name__)

# Enough for ~20 columns of suggestions; stops a rambling model instead of waiting forever.
MAX_OUTPUT_TOKENS = 2048

Post = Callable[[str, dict[str, Any], float], dict[str, Any]]


class _Retryable(Exception):
    """The server was unreachable, too slow or returned 5xx: worth trying again."""


def _post_json(url: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = _error_detail(exc)
        if exc.code >= 500:
            raise _Retryable(f"HTTP {exc.code}: {detail}") from exc
        hint = " (pull it first: ollama pull <model>)" if exc.code == 404 else ""
        raise SuggestionError(
            f"Ollama rejected the request: HTTP {exc.code}: {detail}{hint}"
        ) from exc
    except (OSError, http.client.HTTPException) as exc:  # refused, unreachable, timed out, cut off
        raise _Retryable(str(exc) or exc.__class__.__name__) from exc
    except ValueError as exc:
        raise SuggestionError("Ollama returned something that isn't JSON") from exc


def _error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        return str(json.loads(exc.read()).get("error", exc.reason))
    except (ValueError, AttributeError, OSError):
        return str(exc.reason)


class OllamaSuggester:
    """Suggests mappings with a model served by a local Ollama."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 60.0,
        max_retries: int = 2,
        retry_delay: float = 0.5,
        post: Post = _post_json,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._url = f"{base_url.rstrip('/')}/api/chat"
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._post = post
        self._sleep = sleep

    @classmethod
    def from_settings(cls, config: Settings = settings) -> "OllamaSuggester":
        return cls(
            base_url=config.ollama_url,
            model=config.ollama_model,
            timeout=config.ollama_timeout_seconds,
            max_retries=config.ollama_max_retries,
        )

    def suggest(self, columns: list[ColumnSample], fields: list[str]) -> list[ColumnSuggestion]:
        body = {
            "model": self._model,
            "stream": False,
            "format": response_schema(fields),
            "options": {"temperature": 0, "num_predict": MAX_OUTPUT_TOKENS},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_payload(columns, fields)},
            ],
        }
        reply = self._send(body)

        if not isinstance(reply, dict):
            raise SuggestionError("Ollama's reply wasn't a JSON object")
        if reply.get("done_reason") == "length":
            raise SuggestionError("Ollama's answer was cut off")
        try:
            text = reply["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise SuggestionError("Ollama's reply had no message") from exc
        return parse_suggestions(text, [c.header for c in columns], fields)

    def _send(self, body: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            try:
                return self._post(self._url, body, self._timeout)
            except _Retryable as exc:
                if attempt == self._max_retries:
                    raise SuggestionError(
                        f"Ollama is not answering at {self._url} ({exc}); "
                        "is `ollama serve` running and the model pulled?"
                    ) from exc
                log.info("Ollama attempt %s failed (%s); retrying", attempt + 1, exc)
                self._sleep(self._retry_delay * 2**attempt)
        raise AssertionError("unreachable")  # the loop always returns or raises
