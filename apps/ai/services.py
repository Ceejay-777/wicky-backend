"""
AI Provider Service Layer

One function per role -- generate, embed, rank/resolve -- keeping
provider-specific code contained here so swapping providers later is a
config change, not a rewrite.

Every text call goes through ``_call_chain``: it tries the action's chain in
order, parses and shape-checks each reply, logs every step to
``AIGenerationLog`` and returns an ``AIResult``. A step that errors, is
truncated, or returns malformed or wrongly shaped JSON fails and the chain
moves on; ``ProviderError`` is raised only when every step failed.
"""

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
import sentry_sdk

from .calllog import record_call
from .exceptions import ProviderError, ResponseShapeError, TruncatedResponseError
from .models import AIGenerationLog
from .prompts import (
    RESOLVE_PROMPT_VERSION,
    RESOLVE_SCHEMA,
    STANDARDIZE_NAME_SCHEMA,
    STANDARDIZE_PROMPT_VERSION,
    build_generation_messages,
    build_resolve_messages,
    build_standardize_messages,
)
from .providers import (
    PROVIDERS,
    ProviderConfig,
    build_groq_response_format,
    call_gemini_native,
    get_client,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Retryable exception classes
# ---------------------------------------------------------------------------

try:
    from google.api_core.exceptions import (
        BadGateway,
        DeadlineExceeded,
        GatewayTimeout,
        ResourceExhausted,
        ServiceUnavailable,
    )
    from google.api_core.exceptions import (
        InternalServerError as GoogleInternalError,
    )

    _GOOGLE_RETRYABLE: tuple = (
        BadGateway,
        DeadlineExceeded,
        GatewayTimeout,
        GoogleInternalError,
        ResourceExhausted,
        ServiceUnavailable,
    )
except ImportError:
    _GOOGLE_RETRYABLE = ()

try:
    import httpx as _httpx

    _HTTPX_RETRYABLE = (
        _httpx.TimeoutException,
        _httpx.ConnectError,
        _httpx.RemoteProtocolError,
    )
except ImportError:
    _HTTPX_RETRYABLE = ()


def _is_retryable_error(exc: Exception) -> bool:
    if isinstance(exc, _GOOGLE_RETRYABLE):
        return True
    if isinstance(exc, _HTTPX_RETRYABLE):
        return True
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (
        429,
        502,
        503,
        504,
    ):
        return True
    return False


# ---------------------------------------------------------------------------
# JSON shape validation
# ---------------------------------------------------------------------------


def _validate_shape(data: Any, schema: dict, path: str = "") -> None:
    """Validate parsed JSON matches expected shape.

    Raises ResponseShapeError on mismatch. Covers: missing keys, wrong types,
    non-object top-level values.
    """
    if not isinstance(data, dict):
        raise ResponseShapeError(f"Expected object, got {type(data).__name__}")

    required_keys = set(schema.get("required", []))
    for key, prop_schema in schema.get("properties", {}).items():
        full_path = f"{path}.{key}" if path else key

        if key in required_keys and key not in data:
            raise ResponseShapeError(f"Missing required key: {full_path}")

        if key not in data:
            continue

        expected_type = prop_schema.get("type")
        actual = data[key]

        if expected_type == "string" and not isinstance(actual, str):
            raise ResponseShapeError(f"{full_path}: expected string, got {type(actual).__name__}")
        elif expected_type == "boolean" and not isinstance(actual, bool):
            raise ResponseShapeError(f"{full_path}: expected boolean, got {type(actual).__name__}")
        elif expected_type == "array" and not isinstance(actual, list):
            raise ResponseShapeError(f"{full_path}: expected array, got {type(actual).__name__}")
        elif expected_type == "array" and isinstance(actual, list):
            items_schema = prop_schema.get("items", {})
            if items_schema.get("type") == "object":
                for i, item in enumerate(actual):
                    _validate_shape(item, items_schema, f"{full_path}[{i}]")
        elif expected_type == "object" and isinstance(actual, dict):
            _validate_shape(actual, prop_schema, full_path)
        elif expected_type == "object" and not isinstance(actual, dict):
            raise ResponseShapeError(f"{full_path}: expected object, got {type(actual).__name__}")


# ---------------------------------------------------------------------------
# Per-action provider chains
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChainStep:
    provider: str
    model: str
    # Groq ``reasoning_effort``. None omits the parameter (provider default).
    effort: str | None = None


_GPT_OSS_120B = "openai/gpt-oss-120b"
_GPT_OSS_20B = "openai/gpt-oss-20b"
_QWEN = "qwen/qwen3.8-27b"

_LONG_FORM_CHAIN = [
    ChainStep("groq", _GPT_OSS_120B, "medium"),
    ChainStep("groq", _QWEN),
    ChainStep("groq", _GPT_OSS_20B, "medium"),
]

# Each action maps to an ordered chain. The dispatcher tries each step in
# order until one succeeds. Chosen from docs/reference/ai-providers.md.
_ACTION_CHAINS: dict[str, list[ChainStep]] = {
    "roadmap": _LONG_FORM_CHAIN,
    "topic": _LONG_FORM_CHAIN,
    "topic_repair": _LONG_FORM_CHAIN,
    "review": [
        ChainStep("groq", _GPT_OSS_120B, "medium"),
        ChainStep("groq", _QWEN, "default"),  # thinking on
        ChainStep("groq", _GPT_OSS_20B, "medium"),
    ],
    "quiz_normal": [
        ChainStep("groq", _GPT_OSS_120B, "low"),
        ChainStep("groq", _GPT_OSS_20B, "low"),
        ChainStep("groq", _QWEN),
    ],
    "quiz_advanced": [
        ChainStep("groq", _GPT_OSS_120B, "medium"),
        ChainStep("groq", _GPT_OSS_20B, "medium"),
        ChainStep("groq", _QWEN),
    ],
    "standardize": [
        ChainStep("groq", _GPT_OSS_20B, "low"),
        ChainStep("groq", _GPT_OSS_120B, "low"),
    ],
    "resolve": [
        ChainStep("groq", _GPT_OSS_20B, "low"),
        ChainStep("groq", _GPT_OSS_120B, "low"),
    ],
}

# Output-token ceiling per request. Reasoning tokens count toward it, so
# without an explicit cap the provider's small default truncates the JSON
# (seen at 2,048 tokens in testing) and the response fails validation.
_MAX_COMPLETION_TOKENS: dict[str, int] = {
    "roadmap": 8192,
    "topic": 8192,
    "topic_repair": 8192,
    "quiz_normal": 8192,
    "quiz_advanced": 8192,
    "review": 4096,
    "standardize": 4096,
    "resolve": 4096,
}


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AIResult:
    """Parsed reply plus what happened on the call."""

    data: dict[str, Any]
    provider: str
    model: str
    steps_tried: int
    latency_ms: int
    # Id of the AIGenerationLog row for the serving step; None if logging failed.
    log_id: int | None


# ---------------------------------------------------------------------------
# Core calling functions
# ---------------------------------------------------------------------------


def _call_openai_compatible(
    config: ProviderConfig,
    model: str,
    messages: list[dict],
    response_format: dict | None = None,
    reasoning_effort: str | None = None,
    max_completion_tokens: int | None = None,
) -> tuple[str, dict | None]:
    """Call an OpenAI-compatible provider. Returns (raw reply text, token usage)."""
    client = get_client(config)
    payload: dict[str, Any] = {"model": model, "messages": messages}
    if response_format:
        payload["response_format"] = response_format
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    if max_completion_tokens:
        payload["max_completion_tokens"] = max_completion_tokens
    resp = client.post("/chat/completions", json=payload)
    resp.raise_for_status()
    body = resp.json()
    choice = body["choices"][0]
    content = choice["message"].get("content") or ""
    if choice.get("finish_reason") == "length":
        # Cut off at the token ceiling: the JSON is incomplete.
        raise TruncatedResponseError(
            f"{model} response truncated at max_completion_tokens", raw=content
        )
    return content, body.get("usage")


def _call_provider(
    provider_name: str,
    model: str,
    messages: list[dict],
    schema: dict,
    *,
    effort: str | None = None,
    max_tokens: int | None = None,
) -> tuple[str, dict | None]:
    """Route to the correct calling mechanism per provider. Returns (raw text, usage)."""
    if provider_name == "gemini":
        return call_gemini_native(messages, schema, model=model), None
    elif provider_name == "groq":
        config = PROVIDERS["groq"]
        response_format = build_groq_response_format(schema, "Response")
        return _call_openai_compatible(
            config,
            model,
            messages,
            response_format,
            reasoning_effort=effort,
            max_completion_tokens=max_tokens,
        )
    else:
        raise ValueError(f"Unknown provider: {provider_name}")


def _failure_kind(exc: Exception) -> str:
    Outcome = AIGenerationLog.Outcome
    if isinstance(exc, TruncatedResponseError):
        return Outcome.TRUNCATED
    if isinstance(exc, json.JSONDecodeError):
        return Outcome.MALFORMED_JSON
    if isinstance(exc, ResponseShapeError):
        return Outcome.SHAPE_INVALID
    if isinstance(exc, httpx.TimeoutException):
        return Outcome.TIMEOUT
    if isinstance(exc, httpx.HTTPError):
        return Outcome.HTTP_ERROR
    return Outcome.OTHER


def _call_chain(
    action: str,
    messages: list[dict],
    schema: dict,
    *,
    prompt_version: str,
    context: dict,
) -> AIResult:
    """Try the action's chain in order, logging every step.

    Raises ProviderError when every step fails.
    """
    if action not in _ACTION_CHAINS:
        raise ValueError(f"Unknown AI action: {action}")
    if not isinstance(prompt_version, str) or not prompt_version:
        raise ValueError("prompt_version is required")
    if not isinstance(context, dict):
        raise ValueError("context must be a dict")
    chain = _ACTION_CHAINS[action]
    max_tokens = _MAX_COMPLETION_TOKENS.get(action)
    log = {
        "action": action,
        "prompt_version": prompt_version,
        "messages": messages,
        "context": context,
    }
    call_started = time.monotonic()

    for position, step in enumerate(chain):
        started = time.monotonic()
        raw, usage = "", None
        try:
            raw, usage = _call_provider(
                step.provider,
                step.model,
                messages,
                schema,
                effort=step.effort,
                max_tokens=max_tokens,
            )
            data = json.loads(raw)
            _validate_shape(data, schema)
        except Exception as e:
            latency_ms = int((time.monotonic() - started) * 1000)
            if isinstance(e, TruncatedResponseError):
                raw = e.raw
            record_call(
                **log,
                provider=step.provider,
                model=step.model,
                step_position=position,
                outcome=_failure_kind(e),
                latency_ms=latency_ms,
                raw_response=raw,
                usage=usage,
                error_message=str(e),
            )
            logger.warning(
                "%s %s/%s failed after %dms: %s",
                action, step.provider, step.model, latency_ms, e,
            )
            sentry_sdk.capture_message(
                f"{action} {step.provider}/{step.model} failed: {e}",
                level="warning",
            )
            continue

        latency_ms = int((time.monotonic() - started) * 1000)
        log_id = record_call(
            **log,
            provider=step.provider,
            model=step.model,
            step_position=position,
            outcome=AIGenerationLog.Outcome.SUCCESS,
            latency_ms=latency_ms,
            raw_response=raw,
            parsed_data=data,
            usage=usage,
        )
        logger.info(
            "%s served by %s/%s in %dms", action, step.provider, step.model, latency_ms
        )
        return AIResult(
            data=data,
            provider=step.provider,
            model=step.model,
            steps_tried=position + 1,
            latency_ms=int((time.monotonic() - call_started) * 1000),
            log_id=log_id,
        )

    raise ProviderError(
        f"All providers failed for {action}",
        provider=chain[0].provider,
        recoverable=False,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_content(
    prompt: str,
    system_instruction: str | None,
    output_schema: dict[str, Any],
    *,
    action: str,
    prompt_version: str,
    context: dict,
) -> AIResult:
    """Generate content for ``action`` (roadmap, topic, topic_repair, review, quiz_*).

    The caller owns the prompt, so it supplies the schema and the prompt version.
    """
    if not isinstance(output_schema, dict) or not output_schema:
        raise ValueError("output_schema is required")
    messages = build_generation_messages(prompt, system_instruction, output_schema)
    return _call_chain(
        action, messages, output_schema, prompt_version=prompt_version, context=context
    )


def rank_or_resolve(
    input_text: str,
    candidates: list[dict[str, Any]],
    instruction: str | None = None,
    *,
    context: dict,
) -> AIResult:
    """Rank candidates or resolve to a subject."""
    messages = build_resolve_messages(input_text, candidates, instruction)
    return _call_chain(
        "resolve", messages, RESOLVE_SCHEMA,
        prompt_version=RESOLVE_PROMPT_VERSION, context=context,
    )


def standardize_subject_name(raw_input: str, *, context: dict) -> AIResult:
    """Standardize a raw subject name."""
    messages = build_standardize_messages(raw_input)
    return _call_chain(
        "standardize", messages, STANDARDIZE_NAME_SCHEMA,
        prompt_version=STANDARDIZE_PROMPT_VERSION, context=context,
    )


def generate_embedding(text: str) -> list[float]:
    """Generate an embedding vector. Gemini-only, with retry."""
    max_attempts = 3
    last_error = None

    for attempt in range(max_attempts):
        try:
            import google.generativeai as genai

            genai.configure(api_key=os.environ["GEMINI_API_KEY"])
            result = genai.embed_content(
                model="models/gemini-embedding-001", content=text
            )
            return result["embedding"]
        except Exception as e:
            last_error = e
            if attempt < max_attempts - 1:
                wait = 2 ** attempt
                logger.warning(
                    "Embedding attempt %d failed, retrying in %ds: %s",
                    attempt + 1,
                    wait,
                    e,
                )
                time.sleep(wait)

    raise ProviderError(
        str(last_error), provider="gemini", recoverable=_is_retryable_error(last_error)
    )
