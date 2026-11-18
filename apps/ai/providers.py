"""
Provider configuration, client creation and response format builders.

Each provider (Groq, Gemini) has its own config and response format
strategy. The rest of the codebase never constructs clients or response
formats directly -- it goes through this module.
"""

import os
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Provider config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key_env: str


PROVIDERS: dict[str, ProviderConfig] = {
    "gemini": ProviderConfig(
        name="gemini",
        base_url="",
        api_key_env="GEMINI_API_KEY",
    ),
    "groq": ProviderConfig(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
    ),
}


# ---------------------------------------------------------------------------
# Client creation (OpenAI-compatible providers only)
# ---------------------------------------------------------------------------


def get_client(config: ProviderConfig):
    """Return an httpx client pointed at the given provider's base URL."""
    import httpx

    api_key = os.environ.get(config.api_key_env, "")
    return httpx.Client(
        base_url=config.base_url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=120.0,
    )


# ---------------------------------------------------------------------------
# Response format builders (per-provider structured output strategy)
# ---------------------------------------------------------------------------


def build_groq_response_format(schema: dict, name: str) -> dict:
    """Groq best-effort JSON schema mode -- validates after generation."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": False,
            "schema": schema,
        },
    }


# ---------------------------------------------------------------------------
# Gemini native call (uses google-generativeai SDK, not OpenAI-compatible)
# ---------------------------------------------------------------------------


# Keywords the Gemini native SDK accepts in a response schema. Anything else
# (minItems, maxItems, minimum, maximum, additionalProperties, ...) is rejected
# with "Unknown field for Schema". Size and range limits stay in the prompt text
# and in the caller's post-generation validators.
_GEMINI_SCHEMA_KEYWORDS = frozenset(
    {"type", "format", "description", "nullable", "enum", "items", "properties", "required"}
)


def sanitize_schema_for_gemini(schema: dict) -> dict:
    """Return a copy of a JSON schema containing only Gemini-supported keywords."""
    cleaned = {}
    for key, value in schema.items():
        if key not in _GEMINI_SCHEMA_KEYWORDS:
            continue
        if key == "properties":
            cleaned[key] = {
                name: sanitize_schema_for_gemini(sub) for name, sub in value.items()
            }
        elif key == "items" and isinstance(value, dict):
            cleaned[key] = sanitize_schema_for_gemini(value)
        else:
            cleaned[key] = value
    return cleaned


def call_gemini_native(
    messages: list[dict],
    schema: dict,
    model: str = "gemini-3.8-flash",
) -> str:
    """Call Gemini via native SDK and return the raw reply text (unparsed JSON)."""
    import google.generativeai as genai

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])

    system_msg = next((m["content"] for m in messages if m["role"] == "system"), "")
    user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")

    model_obj = genai.GenerativeModel(
        model_name=model,
        system_instruction=system_msg or None,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=sanitize_schema_for_gemini(schema),
        ),
    )
    response = model_obj.generate_content(user_msg)
    return response.text
