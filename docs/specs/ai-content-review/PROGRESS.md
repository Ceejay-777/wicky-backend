# Progress log: AI content review spec

Each pass appends an entry. Do not edit earlier entries, except to tick a box the next pass completes for it, and say so in your own entry.

Entry template:

```
## Pass N — <date>
Commit: <short hash>
Tests: <before> → <after> (all passing: yes/no)
Done:
- ...
Deviations from spec (and why):
- ...
Open questions for the owner:
- ...
Notes for later passes:
- ...
```

---

## Pass 1 — 2026-11-17
Commit: the `feat(ai): log every AI call and raise on review outage` commit on `feat/ai-content-review`
Tests: 517 → 524 (all passing: yes; still only the pre-existing `google.generativeai` FutureWarning)
Done:
- Branch `feat/ai-content-review` created from `main`.
- `apps/ai` no longer imports `apps/learning`. It owns `ResponseShapeError` (wrong shape) and `TruncatedResponseError` (`apps/ai/exceptions.py`); both only fail a chain step. The duplicate `TOPIC_CONTENT_SCHEMA` is gone from `apps/ai/prompts.py`; `generate_content` requires `output_schema`, `action`, `prompt_version` and `context` (no defaults).
- One dispatcher, `_call_chain` (`apps/ai/services.py`): parses and shape-checks inside the loop, classifies each failure (`http_error`, `timeout`, `truncated`, `malformed_json`, `shape_invalid`, `other`), logs every step, returns `AIResult` (data, provider, model, steps_tried, latency_ms, log_id). `ProviderError` only when every step fails. Providers now return raw text plus usage so the raw response can be logged.
- Review: the fake "flagged" result and the two-try loop are gone; an outage raises `ProviderError`. Review chain is now gpt-oss-120b (medium), qwen3.8-27b, gpt-oss-20b (medium). `review_content_for_topic` lets Celery retry; on the last retry it sets the topic `READY`, leaves `review_status` as it was, publishes, and re-raises. A genuine "not passed" still regenerates (unchanged for this pass).
- `AIGenerationLog` model, migration `ai/0002_aigenerationlog`, admin (first `admin.py`: list without large columns, filters, `=topic_id` search, read-only detail except verdict and note, pretty-printed messages and JSON), `apps/ai/calllog.py` (`record_call`, `annotate_outcome`, 100 KB cap with marker, failures to Sentry), `ai.delete_old_generation_logs` (batched, daily in `config/celery.py`), settings `AI_LOG_RETENTION_DAYS` (default 90) and `AI_LOG_MAX_FIELD_BYTES`.
- Callers updated: topic, roadmap, quiz, review, standardize, resolve read `.data` and pass context. Placeholder versions `topic-v0`, `roadmap-v0`, `quiz_normal-v0`, `quiz_advanced-v0`. The topic review annotates its log row with `{"overall_passed": ...}`.
- Tests: `apps/ai/tests/test_services.py` (rewritten, mocks the httpx client), `apps/ai/tests/test_call_log_admin.py` (retention, import-scan architecture test, admin), `TestReviewOutage` in `apps/learning/tests/test_topic_events.py`. Learning test mocks now return `ai_result(...)` (`apps/ai/tests/helpers.py`).
- Docs: `AGENTS.md` (AI calls, test quirks), `docs/reference/ai-providers.md` (chain order, failure rules, Call log section), `docs/reference/deployment.md` (beat task, env var), `.env.example`, `docs/backlog/known-bugs.md` #3 (reviewer is no longer primary).
- Postgres check: `makemigrations --check` reports no changes; `migrate ai --plan` against development settings (the Neon dev DB) plans only `Create model AIGenerationLog`; `sqlmigrate ai 0002` output reviewed (table plus the six indexes). Applied to the Neon dev DB on 2026-11-17 at the owner's request (`migrate ai`: OK; table readable through `ai_log`).
Deviations from spec (and why):
- Rollback-proof logging uses a second database alias, `ai_log` (same database, own connection, `config/utils/db_routers.py`). Django has no other way to write outside the caller's transaction. In tests `ai_log` is a separate in-memory SQLite DB, because SQLite cannot give a second connection to the same in-memory DB its own write transaction; tests touching the log mark `databases=["default", "ai_log"]`.
- Review, standardize and resolve take `context` but not `prompt_version`: their prompt text lives in `apps/ai/prompts.py`, so their versions are constants there (`review-v0`, `standardize-v0`, `resolve-v0`). A caller passing a version for text it does not own could drift from the text. `generate_content` callers own their prompts and must pass one. The dispatcher itself requires both.
- At review retry exhaustion the task still re-raises `ProviderError` after publishing `READY`, matching the other tasks, so Celery records the failure.
- The learning-side tests of `apps.ai` internals (`TestAIReviewContent`, `TestAIGenerateContent`) were removed; their coverage now lives in `apps/ai/tests`. The "learning schemas are Gemini-safe" test moved from `apps/ai/tests` to `apps/learning/tests/test_content_generation.py`, because the import scan covers test files too.
- Test settings use plain `StaticFilesStorage` so admin pages render without `collectstatic`.
Open questions for the owner:
- The `ai_log` alias doubles Postgres connections per process (two instead of one). Fine at current scale; say if the Neon pooler limits matter.
Notes for later passes:
- `build_quiz` retries the whole AI call inside the task on `ProviderError` as well as `GenerationError`, so one quiz can call the chain twice before Celery retries. Quizzes are out of scope here; worth a follow-up.
- `_mark_topic_failed` and `cleanup_stuck_generations` still use `FLAGGED`; pass 3 replaces them.
- `generate_topic_content` still calls the writer twice on a domain validation failure; pass 2's normalise-before-reject should shrink this.
- `_validate_shape` does not check `integer` or `enum`; out-of-range values are left to the learning validators.
- `verify_provider_models` is not called anywhere (the old unused import was dropped).
## Pass 2: not started
## Pass 3: not started
## Pass 4: not started
