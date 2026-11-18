# AI providers

Code: `apps/ai/services.py` (`_ACTION_CHAINS`, `_MAX_COMPLETION_TOKENS`, `generate_content`), `apps/ai/providers.py`.

## Who does what

- **Groq** (OpenAI-compatible, via httpx): all text generation and review.
- **Gemini** (`gemini-embedding-001`, 3072 dims): embeddings only. Groq has no embedding model.
- **Brevo**: email (`django-anymail`).

## Chains (tried in order until one succeeds)

| Action | Chain (model, effort) |
|---|---|
| `roadmap`, `topic`, `topic_repair` | gpt-oss-120b (medium), qwen3.8-27b, gpt-oss-20b (medium) |
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
- Callers may attach what they made of a call with `annotate_outcome(log_id, ...)` (the topic review stores its interpreted verdict; the repair stores `{"repair": "repaired"}` or `{"repair": "invalid", "reason": ...}`).
- Browse and judge rows in Django admin (`/admin/ai/aigenerationlog/`): only `human_verdict` and `human_note` are editable.
- Retention: `ai.delete_old_generation_logs` runs daily and deletes rows older than `AI_LOG_RETENTION_DAYS` (default 90) in batches.

## Topic prompts

- Roadmap, topic, review and repair prompt text, system instructions, schemas and versions live in `apps/learning/prompts.py` (`roadmap-v1`, `topic-v1`, `review-v1`, `topic_repair-v1`). Builders there are pure (plain values in, `Prompt` out: text, system instruction, schema, version). All of them go through `apps.ai.generate_content` with their own action. Quiz prompts are still in `apps/learning/services.py`. Standardize and resolve prompts are in `apps/ai/prompts.py`.
- Every prompt has a version label (`topic-v1`, `roadmap-v1`, ...), recorded on each log row. Any wording change bumps the version, so log rows can be compared across prompt versions.
- The topic writer gets the subject, title, the topic's goal sentence (`Topic.description`, from the roadmap; may be blank), its level and what that level's reader knows, and the previous and next topic titles (do not re-teach the previous one, do not cover the next one).
- The summary is four plain paragraphs, in order: what it is; key terms and ideas; one concrete example or analogy; why it matters and what it leads to. Plain text, paragraphs separated by one blank line, no markdown, 300-500 words. The frontend splits on newlines and renders no markdown (`toParagraphs` in `wicky-frontend/apps/web/src/lib/learning/reading.ts`).
- The topic pipeline (`apps/learning/pipeline.py`) normalises the summary in code (strips heading, bullet, bold and italic markers; collapses blank lines; straightens smart quotes) and then rejects only an empty summary or one outside 120-900 words (`SUMMARY_MIN_WORDS`, `SUMMARY_MAX_WORDS`). Paragraph count is not checked.

## Topic review and repair

Code: `apps/learning/pipeline.py`, one Celery task per stage in `apps/learning/tasks.py` (generate, review, repair). Each stage persists its result before the next is queued, and a task that finds the topic outside its stage's state does nothing, so a redelivered task never repeats an AI call.

- **Review is narrow.** The reviewer gets the same brief as the writer (subject, title, goal, level and its reader, neighbouring titles) and the summary, never the resource links. It answers two questions only: is the summary on topic (yes or no, one-sentence reason), and which statements are clearly factually wrong, each with the exact sentence quoted, the problem and a corrected sentence. Style, tone, reading level, omissions and depth are not issues; those are writer instructions.
- **No quote, no rejection.** Code keeps an issue only when its quote appears in the summary (compared ignoring case, whitespace and curly vs straight quotes) and its quote, problem and correction are all non-empty; duplicate quotes count once. The result is stored on the topic (`Topic.review_verdict`: on-topic flag, reason, kept issues, dropped count) and on the review call's log row.
- **Decisions.** On topic with no kept issues: `PASSED`. On topic with kept issues: one `topic_repair` call (writer chain) that changes only what the issues require; the result is normalised and validated like a generated summary; valid gives `FIXED`, invalid keeps the original as `UNREVIEWED`. A repaired summary is not reviewed again. Off topic (even with issues): regenerate once (`Topic.regeneration_count`), and off topic again gives `FAILED` with `REJECTED`.
- **Outages are not verdicts.** A review or repair outage raises `ProviderError` and Celery retries; when retries run out the original is published as `UNREVIEWED`. A generation outage still retries and then sets `FAILED` with review status `PENDING`, which the hourly sweeper requeues. A topic stuck in review or repair for an hour is published as `UNREVIEWED`.

| `review_status` | Meaning | Content shown? |
|---|---|---|
| `PENDING` | Not reviewed yet (new, generating, or waiting for review) | No |
| `PASSED` | On topic, no verified issues | Yes |
| `FIXED` | Verified issues found and repaired | Yes |
| `UNREVIEWED` | Review or repair could not complete; original published | Yes |
| `REJECTED` | Off topic twice; topic `FAILED` | No |

`GET /topics/<id>` returns an empty `summary` and `resource_links` unless the status is `PASSED`, `FIXED` or `UNREVIEWED`. `POST /topics/<id>/retry` resets the regeneration count and verdict and starts a new cycle (also for `REJECTED`).

## Why these settings (evidence, 2026-10-14, full report in `docs/evidence/`)

- **Always set `max_completion_tokens`.** Without it, reasoning tokens ate the room and JSON was cut off (the old `json_validate_failed` 400s). With 8192: 59/60 valid generations across all three models.
- **Low/medium effort is enough.** 120b normal quiz: reasoning tokens ~800 -> ~50, latency 4s -> 3s, same validity.
- **Typical latency:** roadmap ~4s, topic ~3s, quiz 2-7s, review/standardize/resolve 1.5-2.5s. Gemini text generation was 30-270s (hidden thinking tokens, 503 retries up to 600s, 20 req/day free-tier cap), which is why it was dropped.
- **Qwen last for quizzes:** weakest answer-key agreement on normal quizzes (67%); returned `503 over capacity` in some runs.
- **Subject cleanup/matching:** 100% correct on all test cases, including a prompt-injection input.
- **Review:** no model passed wrong or off-topic text; the only errors were over-strict rejections of good text on "completeness" (the old four-axis review). That review was replaced by the narrow, quote-checked review above.

## Known quality gaps (not solved by model choice)

- Generated resource links are mostly dead or invented: 24 dead, 13 ok, 19 unverifiable of 56 (YouTube 13 dead / 1 ok). See `backlog/follow-ups.md` F-1.
- About 1 in 5 quiz answer keys disagrees with a blind second model. See F-2.
- Topic summaries (`topic-v1`) follow the four-paragraph plain-text shape, but one real sample (2026-11-17, level 2, gpt-oss-120b) came in under the 300-word target (284) and leaned conceptual rather than practical for its level. Worth looking at: length, how practical level 2 and 3 summaries are, and how far the last paragraph previews the next topic. A dedicated generation-quality pass will look at this in depth.

- Live test 2026-11-18 (19 real lessons through the narrow review): about 1 lesson in 3 still held a wrong statement after checking and fixing, and the checker wrongly flagged 3 true statements. Report and raw data in `docs/evidence/2026-11-18-live-content-review/`; plan in `backlog/content-quality-plan.md` (F-10).

## Limits of the evidence

Small samples (3 runs per cell). Validation checks JSON shape and counts, not teaching quality. Review was tested on blatant errors only. Rate limits and 503s vary over time.
