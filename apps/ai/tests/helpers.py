from apps.ai.services import AIResult


def ai_result(data, **overrides) -> AIResult:
    """An AIResult as the AI layer returns it, for mocking public AI functions."""
    fields = {
        "data": data,
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
        "steps_tried": 1,
        "latency_ms": 1,
        "log_id": None,
    }
    return AIResult(**{**fields, **overrides})
