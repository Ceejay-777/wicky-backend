import json
from unittest.mock import patch

import httpx
import pytest
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import override_settings

from apps.ai.calllog import annotate_outcome
from apps.ai.exceptions import ProviderError, ResponseShapeError
from apps.ai.models import AIGenerationLog
from apps.ai.services import (
    _ACTION_CHAINS,
    _MAX_COMPLETION_TOKENS,
    _is_retryable_error,
    _validate_shape,
    generate_content,
    standardize_subject_name,
)

LOG_DB = pytest.mark.django_db(databases=["default", "ai_log"])

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}
GOOD = {"answer": "42"}
USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def reply(content, finish="stop", status=200):
    """A real httpx response shaped like a Groq chat completion."""
    if not isinstance(content, str):
        content = json.dumps(content)
    body = {
        "choices": [{"finish_reason": finish, "message": {"content": content}}],
        "usage": USAGE,
    }
    return httpx.Response(
        status, json=body, request=httpx.Request("POST", "https://groq.test/chat/completions")
    )


class FakeClient:
    """Stands in for the provider's httpx client: the HTTP boundary."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.payloads = []

    def post(self, path, json):
        self.payloads.append(json)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def fake_groq(*replies):
    client = FakeClient(replies)
    return patch("apps.ai.services.get_client", return_value=client), client


def generate(**overrides):
    kwargs = {
        "prompt": "What is the answer?",
        "system_instruction": "Be brief.",
        "output_schema": SCHEMA,
        "action": "topic",
        "prompt_version": "test-v1",
        "context": {"topic_id": 7, "subject_id": 3},
    }
    return generate_content(**{**kwargs, **overrides})


def rows():
    return list(AIGenerationLog.objects.order_by("id"))


# ---------------------------------------------------------------------------
# Dispatcher and results
# ---------------------------------------------------------------------------


@LOG_DB
class TestDispatcher:
    def test_first_step_success_returns_typed_result(self):
        patcher, _ = fake_groq(reply(GOOD))
        with patcher:
            result = generate()
        assert result.data == GOOD
        assert result.provider == "groq"
        assert result.model == "openai/gpt-oss-120b"
        assert result.steps_tried == 1
        assert result.latency_ms >= 0
        assert result.log_id == rows()[0].id

    def test_http_error_falls_through_to_second_step(self):
        patcher, _ = fake_groq(reply({}, status=503), reply(GOOD))
        with patcher:
            result = generate()
        assert result.steps_tried == 2
        assert result.model == "qwen/qwen3.8-27b"
        first, second = rows()
        assert (first.outcome, first.served, first.step_position) == ("http_error", False, 0)
        assert (second.outcome, second.served, second.step_position) == ("success", True, 1)
        assert result.log_id == second.id

    def test_timeout_is_logged_as_timeout(self):
        patcher, _ = fake_groq(httpx.ReadTimeout("slow"), reply(GOOD))
        with patcher:
            generate()
        assert rows()[0].outcome == "timeout"

    def test_truncated_reply_is_logged_and_chain_moves_on(self):
        patcher, _ = fake_groq(reply('{"answer": "4', finish="length"), reply(GOOD))
        with patcher:
            result = generate()
        assert result.steps_tried == 2
        first = rows()[0]
        assert first.outcome == "truncated"
        assert first.raw_response == '{"answer": "4'

    def test_invalid_json_is_logged_and_chain_moves_on(self):
        patcher, _ = fake_groq(reply("not json at all"), reply(GOOD))
        with patcher:
            result = generate()
        assert result.steps_tried == 2
        assert rows()[0].outcome == "malformed_json"
        assert rows()[0].raw_response == "not json at all"

    def test_wrong_shape_is_logged_and_chain_moves_on(self):
        patcher, _ = fake_groq(reply({"other": 1}), reply(GOOD))
        with patcher:
            result = generate()
        assert result.data == GOOD
        assert rows()[0].outcome == "shape_invalid"

    def test_every_step_failing_raises_provider_error(self):
        patcher, _ = fake_groq(*[reply({}, status=500)] * 3)
        with patcher, pytest.raises(ProviderError, match="All providers failed for topic"):
            generate()
        logged = rows()
        assert [r.outcome for r in logged] == ["http_error"] * 3
        assert [r.step_position for r in logged] == [0, 1, 2]
        assert not any(r.served for r in logged)

    def test_passes_effort_and_token_cap_for_each_step(self):
        patcher, client = fake_groq(reply(GOOD))
        with patcher:
            generate(action="quiz_normal")
        payload = client.payloads[0]
        assert payload["model"] == "openai/gpt-oss-120b"
        assert payload["reasoning_effort"] == "low"
        assert payload["max_completion_tokens"] == 8192

    def test_qwen_step_omits_reasoning_effort(self):
        patcher, client = fake_groq(reply({}, status=503), reply(GOOD))
        with patcher:
            generate(action="roadmap")
        assert "reasoning_effort" not in client.payloads[1]

    def test_unknown_action_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown AI action"):
            generate(action="nope")


class TestRequiredArguments:
    def test_missing_prompt_version_is_rejected(self):
        with pytest.raises(TypeError):
            generate_content("p", None, SCHEMA, action="topic", context={})

    def test_missing_context_is_rejected(self):
        with pytest.raises(TypeError):
            generate_content("p", None, SCHEMA, action="topic", prompt_version="v1")

    def test_empty_prompt_version_is_rejected(self):
        with pytest.raises(ValueError, match="prompt_version"):
            generate(prompt_version="")

    def test_non_dict_context_is_rejected(self):
        with pytest.raises(ValueError, match="context"):
            generate(context=None)

    def test_missing_schema_is_rejected(self):
        with pytest.raises(TypeError):
            generate_content("p", None, action="topic", prompt_version="v1", context={})

    def test_empty_schema_is_rejected(self):
        with pytest.raises(ValueError, match="output_schema"):
            generate(output_schema=None)

    def test_missing_action_is_rejected(self):
        with pytest.raises(TypeError):
            generate_content("p", None, SCHEMA, prompt_version="v1", context={})


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


@LOG_DB
class TestCallLog:
    def test_row_records_the_call(self):
        patcher, _ = fake_groq(reply(GOOD))
        with patcher:
            generate()
        row = rows()[0]
        assert row.action == "topic"
        assert row.prompt_version == "test-v1"
        assert row.provider == "groq"
        assert row.model == "openai/gpt-oss-120b"
        assert row.request_messages[0] == {"role": "system", "content": "Be brief."}
        assert "What is the answer?" in row.request_messages[1]["content"]
        assert row.raw_response == json.dumps(GOOD)
        assert row.parsed_data == GOOD
        assert row.latency_ms >= 0
        assert row.usage == USAGE
        assert row.context == {"topic_id": 7, "subject_id": 3}
        assert row.topic_id == 7

    def test_no_topic_id_without_one_in_context(self):
        patcher, _ = fake_groq(reply(GOOD))
        with patcher:
            generate(context={"subject_id": 3})
        assert rows()[0].topic_id is None

    def test_log_write_failure_does_not_break_the_call(self):
        patcher, _ = fake_groq(reply(GOOD))
        with patcher, patch.object(
            AIGenerationLog.objects, "create", side_effect=RuntimeError("db down")
        ), patch("apps.ai.calllog.sentry_sdk.capture_exception") as sentry:
            result = generate()
        assert result.data == GOOD
        assert result.log_id is None
        sentry.assert_called_once()

    def test_failed_call_row_survives_caller_rollback(self):
        User = get_user_model()
        patcher, _ = fake_groq(*[reply({}, status=500)] * 3)
        with patcher, pytest.raises(ProviderError):
            with transaction.atomic():
                User.objects.create_user(email="rollback@test.com", password="p")
                generate()
        assert not User.objects.filter(email="rollback@test.com").exists()
        assert [r.outcome for r in rows()] == ["http_error"] * 3

    @override_settings(AI_LOG_MAX_FIELD_BYTES=40)
    def test_large_fields_are_truncated_with_marker(self):
        long_answer = {"answer": "x" * 200}
        patcher, _ = fake_groq(reply(long_answer))
        with patcher:
            generate(prompt="p" * 200)
        row = rows()[0]
        assert row.raw_response.startswith(json.dumps(long_answer)[:40])
        assert "[truncated: " in row.raw_response
        assert "[truncated: " in row.request_messages[1]["content"]
        assert row.parsed_data["truncated"] is True

    def test_outcome_annotation_is_stored_by_log_id(self):
        patcher, _ = fake_groq(reply(GOOD))
        with patcher:
            result = generate()
        annotate_outcome(result.log_id, {"verdict": "repaired"})
        assert AIGenerationLog.objects.get(id=result.log_id).outcome_annotation == {
            "verdict": "repaired"
        }

    def test_annotating_an_unknown_id_does_not_raise(self):
        annotate_outcome(999_999, {"verdict": "x"})
        annotate_outcome(None, {"verdict": "x"})


# ---------------------------------------------------------------------------
# Other public functions
# ---------------------------------------------------------------------------


@LOG_DB
class TestOtherPublicFunctions:
    def test_standardize_uses_its_own_chain(self):
        patcher, client = fake_groq(reply({"standardized_name": "X", "is_specific": True}))
        with patcher:
            result = standardize_subject_name("x", context={"raw_input": "x"})
        assert result.data["standardized_name"] == "X"
        assert client.payloads[0]["model"] == "openai/gpt-oss-20b"
        assert rows()[0].action == "standardize"


class TestActionChains:
    """The chains and per-action settings come from docs/reference/ai-providers.md."""

    EXPECTED = {
        "roadmap": [("openai/gpt-oss-120b", "medium"), ("qwen/qwen3.8-27b", None), ("openai/gpt-oss-20b", "medium")],
        "topic": [("openai/gpt-oss-120b", "medium"), ("qwen/qwen3.8-27b", None), ("openai/gpt-oss-20b", "medium")],
        "topic_repair": [("openai/gpt-oss-120b", "medium"), ("qwen/qwen3.8-27b", None), ("openai/gpt-oss-20b", "medium")],
        "review": [("openai/gpt-oss-120b", "medium"), ("qwen/qwen3.8-27b", "default"), ("openai/gpt-oss-20b", "medium")],
        "quiz_normal": [("openai/gpt-oss-120b", "low"), ("openai/gpt-oss-20b", "low"), ("qwen/qwen3.8-27b", None)],
        "quiz_advanced": [("openai/gpt-oss-120b", "medium"), ("openai/gpt-oss-20b", "medium"), ("qwen/qwen3.8-27b", None)],
        "standardize": [("openai/gpt-oss-20b", "low"), ("openai/gpt-oss-120b", "low")],
        "resolve": [("openai/gpt-oss-20b", "low"), ("openai/gpt-oss-120b", "low")],
    }

    def test_chains_match_the_agreed_table(self):
        assert set(_ACTION_CHAINS) == set(self.EXPECTED)
        for action, expected in self.EXPECTED.items():
            assert [(s.model, s.effort) for s in _ACTION_CHAINS[action]] == expected
            assert all(s.provider == "groq" for s in _ACTION_CHAINS[action])

    def test_review_chain_order(self):
        assert [s.model for s in _ACTION_CHAINS["review"]] == [
            "openai/gpt-oss-120b",
            "qwen/qwen3.8-27b",
            "openai/gpt-oss-20b",
        ]

    def test_every_action_has_a_token_cap(self):
        assert set(_MAX_COMPLETION_TOKENS) == set(_ACTION_CHAINS)
        assert all(v >= 4096 for v in _MAX_COMPLETION_TOKENS.values())
        for action in ("roadmap", "topic", "quiz_normal", "quiz_advanced"):
            assert _MAX_COMPLETION_TOKENS[action] == 8192


# ---------------------------------------------------------------------------
# Shape validation and error classification
# ---------------------------------------------------------------------------


NESTED_SCHEMA = {
    "type": "object",
    "properties": {
        "check": {
            "type": "object",
            "properties": {"pass": {"type": "boolean"}, "note": {"type": "string"}},
            "required": ["pass", "note"],
        },
        "done": {"type": "boolean"},
    },
    "required": ["check", "done"],
}
NESTED_OK = {"check": {"pass": True, "note": "OK"}, "done": True}


class TestValidateShape:
    def test_passes_valid_data(self):
        _validate_shape(NESTED_OK, NESTED_SCHEMA)

    def test_fails_on_missing_key(self):
        with pytest.raises(ResponseShapeError, match="Missing required key"):
            _validate_shape({"check": {"pass": True, "note": "OK"}}, NESTED_SCHEMA)

    def test_fails_on_wrong_type(self):
        data = {**NESTED_OK, "check": {"pass": "yes", "note": "OK"}}
        with pytest.raises(ResponseShapeError, match="expected boolean"):
            _validate_shape(data, NESTED_SCHEMA)

    def test_fails_on_non_object_top_level(self):
        with pytest.raises(ResponseShapeError, match="Expected object"):
            _validate_shape("not a dict", NESTED_SCHEMA)

    def test_validates_nested_array_items(self):
        schema = {
            "type": "object",
            "properties": {
                "links": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
                }
            },
            "required": ["links"],
        }
        with pytest.raises(ResponseShapeError, match=r"links\[0\]"):
            _validate_shape({"links": [{"title": "T"}]}, schema)


class TestIsRetryableError:
    def test_provider_error_is_not_retryable(self):
        assert _is_retryable_error(ProviderError("test", provider="groq")) is False

    def test_non_retryable_returns_false(self):
        assert _is_retryable_error(ValueError("nope")) is False
