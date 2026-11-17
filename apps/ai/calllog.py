"""Writes AIGenerationLog rows. Logging never breaks the AI call it records."""

import json
import logging
from typing import Any

import sentry_sdk
from django.conf import settings

logger = logging.getLogger(__name__)

TRUNCATION_MARKER = "\n...[truncated: {total} bytes total]"


def _cap(text: str) -> str:
    limit = settings.AI_LOG_MAX_FIELD_BYTES
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    head = encoded[:limit].decode("utf-8", errors="ignore")
    return head + TRUNCATION_MARKER.format(total=len(encoded))


def _cap_json(value: Any) -> Any:
    if value is None:
        return None
    dumped = json.dumps(value, default=str)
    if len(dumped.encode("utf-8")) <= settings.AI_LOG_MAX_FIELD_BYTES:
        return value
    return {"truncated": True, "text": _cap(dumped)}


def record_call(
    *,
    action: str,
    prompt_version: str,
    provider: str,
    model: str,
    step_position: int,
    outcome: str,
    messages: list[dict],
    context: dict,
    latency_ms: int,
    raw_response: str = "",
    parsed_data: Any = None,
    usage: dict | None = None,
    error_message: str = "",
) -> int | None:
    """Write one chain-step row and return its id, or None if the write failed."""
    from .models import AIGenerationLog

    try:
        topic_id = context.get("topic_id")
        row = AIGenerationLog.objects.create(
            action=action,
            prompt_version=prompt_version,
            provider=provider,
            model=model,
            step_position=step_position,
            served=outcome == AIGenerationLog.Outcome.SUCCESS,
            outcome=outcome,
            error_message=_cap(error_message),
            request_messages=[
                {**m, "content": _cap(str(m.get("content", "")))} for m in messages
            ],
            raw_response=_cap(raw_response or ""),
            parsed_data=_cap_json(parsed_data),
            latency_ms=latency_ms,
            usage=usage,
            context=_cap_json(context),
            topic_id=topic_id if isinstance(topic_id, int) else None,
        )
        return row.id
    except Exception as e:
        logger.exception("Could not write AI call log row")
        sentry_sdk.capture_exception(e)
        return None


def annotate_outcome(log_id: int | None, annotation: Any) -> None:
    """Attach what the caller made of a call (e.g. the review verdict) to its log row."""
    from .models import AIGenerationLog

    if log_id is None:
        return
    try:
        AIGenerationLog.objects.filter(id=log_id).update(outcome_annotation=annotation)
    except Exception as e:
        logger.exception("Could not annotate AI call log row %s", log_id)
        sentry_sdk.capture_exception(e)
