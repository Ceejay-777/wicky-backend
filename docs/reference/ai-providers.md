# AI providers

Code: `apps/ai/services.py` (`_ACTION_CHAINS`, `_MAX_COMPLETION_TOKENS`, `generate_content`), `apps/ai/providers.py`.

## Who does what

- **Groq** (OpenAI-compatible, via httpx): all text generation and review.
- **Gemini** (`gemini-embedding-001`, 3072 dims): embeddings only. Groq has no embedding model.
- **Brevo**: email (`django-anymail`).

## Chains (tried in order until one succeeds)

| Action | Chain (model, effort) |
|---|---|
| `roadmap`, `topic` | gpt-oss-120b (medium), qwen3.8-27b, gpt-oss-20b (medium) |
| `review` | gpt-oss-120b (medium), qwen3.8-27b (thinking on), gpt-oss-20b (medium) |
| `quiz_normal` | gpt-oss-120b (low), gpt-oss-20b (low), qwen3.8-27b |
| `quiz_advanced` | gpt-oss-120b (medium), gpt-oss-20b (medium), qwen3.8-27b |
| `standardize`, `resolve` | gpt-oss-20b (low), gpt-oss-120b (low) |

Check `_ACTION_CHAINS` for the live values. Every call sets `max_completion_tokens` (8192 for generation) and a per-step `reasoning_effort`. A step fails, and the chain moves to the next step, on an HTTP error or timeout, `finish_reason == "length"`, invalid JSON, or JSON in the wrong shape. When every step fails the call raises `ProviderError`, for every action including review; Celery tasks retry on it.

Every public function returns an `AIResult`: `data`, `provider`, `model`, `steps_tried`, `latency_ms` and `log_id`. Callers pass a `prompt_version` and a JSON-safe `context` (topic id, subject id, quiz attempt id); neither has a default.

## Call log

Every chain step attempt writes one `AIGenerationLog` row (`apps/ai/models.py`): action, prompt version, provider and model, step position and whether it served the call, outcome (`success` or `http_error`, `truncated`, `malformed_json`, `shape_invalid`, `timeout`, `other`), the request messages, raw response, parsed data, latency, token usage, context and topic id. Text fields are capped at `AI_LOG_MAX_FIELD_BYTES` (100 KB) with a truncation marker.

- Written by the dispatcher (`apps/ai/calllog.py`), never by callers. A failed log write goes to Sentry and never breaks the call.
- Rows go through a second database connection (`ai_log` alias, same database, `config/utils/db_routers.py`), so a failed call is still recorded when the caller's transaction rolls back.
- Callers may attach what they made of a call with `annotate_outcome(log_id, ...)` (the topic review stores `overall_passed`).
- Browse and judge rows in Django admin (`/admin/ai/aigenerationlog/`): only `human_verdict` and `human_note` are editable.
- Retention: `ai.delete_old_generation_logs` runs daily and deletes rows older than `AI_LOG_RETENTION_DAYS` (default 90) in batches.

## Why these settings (evidence, 2026-10-14)

- **Always set `max_completion_tokens`.** Without it, reasoning tokens ate the room and JSON was cut off (the old `json_validate_failed` 400s). With 8192: 59/60 valid generations across all three models.
- **Low/medium effort is enough.** 120b normal quiz: reasoning tokens ~800 -> ~50, latency 4s -> 3s, same validity.
- **Typical latency:** roadmap ~4s, topic ~3s, quiz 2-7s, review/standardize/resolve 1.5-2.5s. Gemini text generation was 30-270s (hidden thinking tokens, 503 retries up to 600s, 20 req/day free-tier cap), which is why it was dropped.
- **Qwen last for quizzes:** weakest answer-key agreement on normal quizzes (67%); returned `503 over capacity` in some runs.
- **Subject cleanup/matching:** 100% correct on all test cases, including a prompt-injection input.
- **Review:** no model passed wrong or off-topic text; the only errors were over-strict rejections of good text (see known bugs, reviewer false rejections).

## Known quality gaps (not solved by model choice)

- Generated resource links are mostly dead or invented: 24 dead, 13 ok, 19 unverifiable of 56 (YouTube 13 dead / 1 ok). See `backlog/follow-ups.md` F-1.
- About 1 in 5 quiz answer keys disagrees with a blind second model. See F-2.

## Limits of the evidence

Small samples (3 runs per cell). Validation checks JSON shape and counts, not teaching quality. Review was tested on blatant errors only. Rate limits and 503s vary over time.
