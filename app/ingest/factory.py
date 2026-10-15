"""Pick the MappingSuggester to use, from the SUGGESTER setting."""

from app.config import Settings, settings
from app.ingest.heuristic import HeuristicSuggester
from app.ingest.ollama import OllamaSuggester
from app.ingest.suggester import ClaudeSuggester, MappingSuggester


def build_suggester(config: Settings) -> MappingSuggester | None:
    """The suggester `config.suggester` names, or None when it can't run.

    Only "claude" can come back as None: without an ANTHROPIC_API_KEY it is skipped, so
    uploads with unknown headers simply aren't given suggestions.
    """
    if config.suggester == "ollama":
        return OllamaSuggester.from_settings(config)
    if config.suggester == "claude":
        return ClaudeSuggester.from_settings(config)
    return HeuristicSuggester()


def get_suggester() -> MappingSuggester | None:
    """FastAPI dependency."""
    return build_suggester(settings)
