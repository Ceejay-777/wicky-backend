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
## Pass 2 — 2026-11-17
Commit: the `feat(learning): give the topic writer goal, level and neighbours` commit on `feat/ai-content-review`
Tests: 524 → 554 (all passing: yes; still only the pre-existing `google.generativeai` FutureWarning)
Done:
- `Topic.description` (`TextField`, blank allowed, default `""`; migration `learning/0013_topic_description`). Roadmap validation keeps it trimmed; missing, blank or non-string stores `""` and never fails the roadmap. `append_roadmap_topics`, `insert_roadmap_topics` and `update_roadmap_topic` accept an optional `description`. Exposed read-only, with help text, on topic detail, subject roadmap (`SubjectTopicProgressSerializer`) and preview (`SubjectPreviewTopicSerializer`).
- `apps/learning/prompts.py` owns roadmap and topic prompt text, system instructions, schemas and versions (`roadmap-v1`, `topic-v1`). Pure builders `build_roadmap_prompt(subject_name)` and `build_topic_prompt(subject_name=, title=, level=, goal=, previous_title=, next_title=)` return a `Prompt` (text, system instruction, schema, version). No roadmap or topic prompt text is left in `services.py`.
- Topic prompt: subject, title, level with its reader description, goal when present, previous/next titles when present with the do-not-re-teach / do-not-cover rules, the four-part shape, plain-text rule, 300-500 words, links instruction (same meaning). Audience fit and completeness are writer instructions now. The system instruction no longer says "no prior knowledge" (that contradicted levels 2 and 3).
- `apps/learning/pipeline.py`: gathers topic, subject and neighbours (one query), builds the prompt, calls the AI with version and context, normalises (`normalise_summary`), validates (`validate_topic_content`: empty or outside `SUMMARY_MIN_WORDS`-`SUMMARY_MAX_WORDS` = 120-900 words rejected; link checks unchanged; paragraph count not checked), saves. `generate_content_for_topic` calls it; its error handling is unchanged.
- Tests: `apps/learning/tests/test_generation_quality.py` (every test in section 2.7, plus the task on success). Existing tests now patch `apps.learning.pipeline.generate_content` and use summaries inside the word bounds.
- Docs: `docs/reference/ai-providers.md` (Topic prompts section), `AGENTS.md` (Prompts line under Generation flows), `docs/backlog/follow-ups.md` F-1 code pointers.
Deviations from spec (and why):
- There are no HTTP roadmap edit endpoints; editing exists only as the service functions above (no view calls them). The optional description and its test are on those functions.
- Builders return text plus system instruction plus schema, not finished messages: `apps.ai.generate_content` assembles the messages (and appends the schema) from those three, and changing that contract was not this pass's job. Each builder still returns its version with them.
- The roadmap prompt wording is unchanged but is now `roadmap-v1`: `v0` was pass 1's placeholder label, and the prompt has now moved to its versioned home.
- `description` is a `TextField`, not a short `CharField`: an over-long AI sentence would otherwise fail the whole roadmap insert, against "a bad description never fails the roadmap".
- `description` is not added to the small `next_topic` / `current_topic` references (title and id only); the spec's list (topic detail, roadmap, preview) is covered.
- The pipeline still makes a second writer call when the first reply fails domain validation, because the spec requires the task behaviour on `GenerationError` to be unchanged. Normalising first makes that path rarer.
- Neighbours are found by `order - 1` and `order + 1`. Orders are contiguous by construction (create, append and insert all renumber); a gap left by a manual delete would just mean no neighbour title.
Open questions for the owner:
- Should the second writer call on a validation failure go (one call per request, fail straight to `FAILED`)? Answered 2026-11-17: keep it. Celery does not retry a domain validation failure, so it is the only retry for a well-formed but unusable reply.
Follow-up, 2026-11-17:
- Migration `learning/0013_topic_description` applied to the Neon dev DB at the owner's request. Existing topics have a blank `description` (no backfill).
- One real `topic-v1` generation (level 2 topic, not saved): four plain paragraphs, no cleanup needed, 284 words. Recorded under Known quality gaps in `docs/reference/ai-providers.md`; a separate generation-quality pass will tune it.
Notes for later passes:
- Pass 3: the reviewer should get `Topic.description` and the level so "on topic" has the same meaning as the writer's brief. Reviewer and repair prompts go in `apps/learning/prompts.py`; `apps.ai.review_content` currently builds its own messages from `apps/ai/prompts.py`, so pass 3 needs a way to send learning-owned review text through the dispatcher (for example via `generate_content` with action `review`).
- `docs/backlog/known-bugs.md` #3 still describes the completeness rejections and `FLAGGED`; pass 3 owns that.
## Pass 3 — 2026-11-17
Commit: the `feat(learning): narrow quote-checked review with repair stage` commit on `feat/ai-content-review`
Tests: 554 → 570 (all passing: yes; still only the pre-existing `google.generativeai` FutureWarning)
Done:
- `Topic.ReviewStatus` is `PENDING`, `PASSED`, `FIXED`, `UNREVIEWED`, `REJECTED`; `FLAGGED`, the old `FAILED` review value and `review_attempts` are gone. New fields `regeneration_count` and `review_verdict` (JSON: `on_topic`, `reason`, kept `issues` of quote/problem/correction, `dropped`). `Topic.VISIBLE_REVIEW_STATUSES` = `PASSED`, `FIXED`, `UNREVIEWED`. Migration `learning/0014_review_pipeline` (no backfill).
- Reviewer and repair prompts, schemas and versions (`review-v1`, `topic_repair-v1`) in `apps/learning/prompts.py`. Writer, reviewer and repairer share one brief builder (`_brief_lines`), so "on topic" means the same thing to all three; the `topic-v1` text is unchanged. The reviewer never sees resource links. The old review prompt, system instruction, schema, `build_review_messages` and `review_content` are gone from `apps/ai`; review now goes through `generate_content(action="review")`. New action `topic_repair` with the writer's chain and an 8192 ceiling.
- `apps/learning/pipeline.py` owns the stages: generate, review (call, `interpret_review`, annotate the log row with the verdict, decide) and repair (one call, normalise and validate like a generated summary, annotate `repaired` or `invalid`). Each stage persists before queuing the next and guards on state: generate needs `GENERATING` and an empty summary; review needs `GENERATING`, a summary and no verdict; repair needs `GENERATING` and an on-topic verdict with kept issues. `give_up_review` publishes `READY`/`UNREVIEWED`; `mark_generation_failed` sets `FAILED`/`PENDING`. Every terminal transition goes through one `_finish` that only moves out of `GENERATING` and publishes `topic.status`.
- Tasks: `generate_content_for_topic`, `review_content_for_topic`, new `repair_content_for_topic`. Each one calls the pipeline, and on its last retry calls that stage's give-up path. They contain no decisions. `review_topic_content` returning a bool is gone from `services.py`.
- The claim (`ensure_topic_content_ready`) now also clears summary, links and verdict and sets `PENDING`, so the off-topic regenerate path goes through `request_topic_generation` as before. The retry service resets `regeneration_count` and the verdict. Sweeper: stuck in generation goes back to `NOT_GENERATED`; stuck in review or repair is published `UNREVIEWED`; `FAILED`/`PENDING` is requeued, `REJECTED` never is.
- `GET /topics/<id>` returns empty `summary` and `resource_links` unless the review status is visible. Serializer help texts list the review values.
- Tests: `apps/learning/tests/test_review_pipeline.py` (every test in 3.8; decisions, outages and logging go through the httpx boundary with `fake_groq`). `test_topic_events.py` rewritten for the new stages. Old four-axis tests removed; other tests updated for the stage guards and new values.  Tests duplicated by the new file (generation task outage and success, review outage retry, REJECTED sweeper and retry) were removed rather than kept twice. `TestValidateShape` now uses a local nested schema.
- Docs: `docs/reference/ai-providers.md` (Topic review and repair section with the status table, chain table, call-log annotations, prompt homes), `AGENTS.md` (Topic pipeline line under Generation flows), `docs/backlog/known-bugs.md` #3 closed, `docs/backlog/follow-ups.md` F-9 (re-review `UNREVIEWED`, not built), `README.md` (two stale lines).
Deviations from spec (and why):
- The counter is `regeneration_count`. It counts off-topic regenerations, not all generations, because the spec's test expects 1 after one regeneration.
- Stage state is derived from existing fields (empty summary, verdict present or not) rather than a new stage field. This works because the claim clears summary and verdict at the start of every generation. On the first off-topic verdict the verdict is persisted and then cleared by the claim straight away, so the record of it lives on the review's log row.
- The generate stage now queues review itself (it used to be the task), so the three stages match and the task holds no follow-on logic. Tests that call `generate_topic_content` directly patch the review queue.
- There was no backend visibility rule before (the topic GET always returned the summary; the frontend gated on `content_status`). I added one in `TopicDetailView`: hidden content comes back as `""` and `[]`, and the field shape is unchanged.
- A domain validation failure at generation (`GenerationError`) also ends `FAILED`/`PENDING`, so the sweeper requeues it after an hour. Before, it was `FLAGGED` with 0 attempts, which the sweeper also requeued, so the behaviour is unchanged.
- `send_notification` used to reset any `FAILED` topic for regeneration. It now skips `REJECTED` topics, so only the retry endpoint restarts them (the same rule the sweeper follows).
Open questions for the owner:
- Should hourly requeueing of domain-validation failures stay? Each requeue can cost two writer calls per topic per hour while the model keeps returning unusable output. Stopping it would need a way to tell those failures apart (for example a separate review value).
- Migration `learning/0014_review_pipeline` is not applied to the Neon dev DB. Say if you want it applied, as with 0002 and 0013.
Notes for later passes:
- The generate stage saves only into the round it started in (still `GENERATING`, summary still empty), so a task left over from an earlier round (for example after the hourly sweeper reset it) cannot overwrite a newer one or queue a second review.
- Pass 4 handoff: the frontend should read the new `review_status` values, should expect an empty `summary`/`resource_links` until `PASSED`/`FIXED`/`UNREVIEWED`, and should treat `FAILED` + `REJECTED` as "retry may not help".
- An off-topic regeneration is not announced over SSE (only terminal states are), the same as before.
- `apps/ai/providers.py` still has `review_models` on the provider config, used only by `verify_provider_models`, which nothing calls (pass 1 note).
## Pass 4 — 2026-11-18
Commit: the `chore(learning): pass 4 audit fixes, docs and frontend handoff` commit on `feat/ai-content-review`
Tests: `main` 517 (pass 1 baseline; `main` has not moved since) → start of pass 570 → 566 (all passing: yes; still only the pre-existing `google.generativeai` FutureWarning). The drop is 7 tests of deleted dead code; 4 tests were added.

Summary for the owner:
- What changed on this branch: every AI call goes through one dispatcher that logs each chain step to `AIGenerationLog`; the topic writer gets goal, level and neighbours and writes four plain paragraphs; the reviewer checks only "on topic" and quoted factual errors, quotes are verified in code, errors are repaired once, off topic regenerates once, and an outage publishes `UNREVIEWED` instead of failing good content.
- What to look at first in admin (`/admin/ai/aigenerationlog/`): filter action `review`, prompt version `review-v1`, and read `outcome_annotation` (the kept issues and the `dropped` count) next to the summary in the request. Mark `human_verdict` on a sample; that is F-11. Then filter action `topic_repair` to see whether repairs fixed what they were asked to.
- Known limits: about 1 lesson in 3 still had a wrong statement after review and repair in the 2026-11-18 live test (writer and checker share the same model knowledge; F-10). The quote check is strict, so a reviewer that paraphrases ("16 century" for "16th century") loses a correct issue. `UNREVIEWED` topics are never reviewed later (F-9). Links are unchecked (F-1). Quizzes are logged but not reviewed (F-12).

Done:
- 4.1 Read the full `main...HEAD` diff. Found and fixed the items marked "fail" below.
- 4.2 Decision table and 4.3 audit (below).
- 4.4 Full suite passes; `makemigrations --check --dry-run` (development settings): "No changes detected". `showmigrations` on the Neon dev DB: `ai/0002`, `learning/0013` and `learning/0014` are applied. Live run: not repeated in this pass. The owner's 2026-11-18 live test (`docs/evidence/2026-11-18-live-content-review/`) ran 19 real topics plus the off-topic, rejected and retry branches through this pipeline with real Groq and the Neon dev DB, and its call-log rows are saved there. It ran Celery inline in one script, so the queued path (Redis, a worker, the `delay` hand-offs between stages) has not been exercised live, and I did not open the admin against those rows. I did not start a worker or beat (AI credits).
- 4.5 Docs: `docs/reference/deployment.md` (the sweeper row described the old behaviour), `docs/reference/ai-providers.md` (late-reply guard, `sweep_stuck_topics`), `docs/backlog/follow-ups.md` (F-5 no longer claims `verify_provider_models` runs; new F-11 and F-12), `docs/backlog/known-bugs.md` (#5, quiz double AI call), spec `README.md` status and handoff link. `docs/README.md` already indexes `specs/`. `AGENTS.md` needed no change.
- 4.6 `frontend-handoff.md`: `ReviewStatus` change, the `underReview` check and wording per status (including that the `FAILED` screen's "check your connection" text is wrong for `REJECTED`), `description`, `toParagraphs` unchanged.
- 4.7 Follow-ups: F-9 (re-review `UNREVIEWED`), F-11 (reviewer precision from human verdicts), F-12 (quiz review, to be specced), F-1 (links) are all present.

Decision check (4.2):

| # | Where in the code | Test |
|---|---|---|
| D1 | `Topic.content_visible` / `VISIBLE_REVIEW_STATUSES` (`apps/learning/models.py`), used by `TopicDetailView`; the pipeline leaves `GENERATING` only through `_finish` | `test_review_pipeline.py::TestStateAndApi::test_summary_shown_only_for_visible_statuses` |
| D2 | `_ACTION_CHAINS["review"]` (`apps/ai/services.py`) | `apps/ai/tests/test_services.py::test_review_chain_order` |
| D3 | `build_review_prompt`, `REVIEW_SCHEMA` (`apps/learning/prompts.py`) | `TestReviewPrompt::test_states_that_omissions_and_style_are_not_issues`, `test_contains_goal_level_and_neighbours_but_no_links` |
| D4 | `interpret_review` (`apps/learning/pipeline.py`) | `TestInterpretReview` (all), `TestDecisions::test_only_made_up_quotes_passes_without_repair` |
| D5 | `repair_topic_content` (one call, `_finish` to `FIXED`, no review queued) | `TestDecisions::test_real_issue_is_repaired_without_re_review`, `test_invalid_repair_keeps_the_original` |
| D6 | `review_content_for_topic` / `repair_content_for_topic` (`apps/learning/tasks.py`) → `give_up_review` | `TestOutages::test_review_outage_retries_then_publishes_unreviewed`, `test_repair_outage_retries_then_publishes_without_re_review` |
| D7 | `review_topic_content` off-topic branch (`regeneration_count`) | `TestDecisions::test_off_topic_once_regenerates_then_passes`, `test_off_topic_twice_is_rejected`, `test_off_topic_with_issues_regenerates_not_repairs` |
| D8 | `FORMAT_RULES` (prompts), `normalise_summary` (pipeline) | `test_generation_quality.py::test_strips_markdown_and_keeps_text`, `test_saved_summary_is_normalised` |
| D9 | `build_topic_prompt`, `_brief_lines` (prompts); `_brief`, `_neighbour_titles` (pipeline) | `test_generation_quality.py::test_includes_every_input_and_the_shape`, `test_messages_carry_level_goal_and_neighbours` |
| D10 | `AIGenerationLog` (`apps/ai/models.py`), `record_call` (`calllog.py`), `admin.py`, `delete_old_generation_logs` + beat entry (`config/celery.py`), `AI_LOG_RETENTION_DAYS` | `test_services.py::test_row_records_the_call`, `test_call_log_admin.py` (retention, admin, verdict save) |
| D11 | `prompt_version` required by `_call_chain`; versions in `apps/learning/prompts.py` and `apps/ai/prompts.py` | `test_full_cycle_logs_each_stage_with_its_version`, `test_missing_prompt_version_is_rejected` |
| D12 | Migrations `learning/0013`, `0014`, `ai/0002` hold schema operations only (no `RunPython`); no aliases for old values | `test_review_pipeline.py::test_review_statuses_are_only_the_new_values` (added this pass) |

Architecture audit (4.3):
- `apps/ai` imports nothing from `apps/learning`: pass (`test_apps_ai_never_imports_apps_learning`).
- `apps/ai` mentions no topics or subjects beyond generic context: pass. Action names (`topic`, `roadmap`, `quiz_*`) are chain keys, `topic_id` is a plain indexed column taken from `context` for admin search, and the standardize/resolve prompts talk about "subjects" because they are those prompts (moving them is out of scope, see pass 1).
- Every AI call goes through the dispatcher: pass after fix. The only provider HTTP call is `_call_openai_compatible`, reached only from `_call_chain`; Gemini text (`call_gemini_native`) is also reached only from there; embeddings are a separate, non-chat path (`generate_embedding`). Fail fixed: `verify_provider_models` (unused) made its own HTTP call to the provider. It was removed, with the `generate_models`/`review_models` lists it read (they duplicated `_ACTION_CHAINS`), the unused `get_response_format`/`build_gemini_response_format`, and their tests.
- Prompt version and context on every call: pass. `roadmap-v1`, `topic-v1`, `review-v1`, `topic_repair-v1`. Placeholders kept: `quiz_normal-v0`, `quiz_advanced-v0` (built in `build_quiz`), `standardize-v0`, `resolve-v0`.
- No roadmap, topic, review or repair prompt text outside `apps/learning/prompts.py`: pass.
- Pipeline logic only in the pipeline: pass after two fixes. (1) The hourly sweeper's topic part (which stage a stuck topic is in, publish `UNREVIEWED` or reset, which `FAILED` topics to requeue) moved from `cleanup_stuck_generations` to `pipeline.sweep_stuck_topics`; the task only calls it and announces. (2) `TopicDetailView` computed visibility from review statuses; it now reads `Topic.content_visible`. Accepted as is: `send_notification` skips `REJECTED` when choosing a topic to restart (a restart filter, not a review decision).
- No branching on exception text: pass (`str(e)` is only passed on as a message or logged).
- Typed results: pass (`AIResult`; `ReviewVerdict`/`ReviewIssue`, stored as JSON through `to_json`/`from_json`).
- No leftovers of `FLAGGED`, `review_attempts`, the four axes, or `TOPIC_CONTENT_SCHEMA` in `apps/ai`: pass. Remaining mentions are migration history (`0001`, `0014`), this spec, earlier `PROGRESS.md` entries, and one evidence line in `ai-providers.md` that says the four-axis review was replaced.
- No path calls the AI twice for one request: pass after fix, with two known exceptions. Generate is guarded by `GENERATING` + empty summary and saves only into its own round; review by `GENERATING` + no verdict; repair by `GENERATING` + an on-topic verdict with issues; a Celery retry only re-enters a stage whose result is not saved. Fail fixed: the review stage's off-topic and "has issues" writes were unconditional, so a review reply arriving after the sweeper had published the topic `UNREVIEWED` would flip a `READY` topic back to `NOT_GENERATED` and pay for a regeneration (or queue a pointless repair). Both writes now apply only while the topic is still `GENERATING` with no verdict, and only then queue the next stage (`TestOutages::test_late_review_does_not_reopen_a_published_topic`, which fails on the old code). Known exceptions: the writer's second call on a domain validation failure (owner decision, pass 2), and `build_quiz` retrying on `ProviderError` (quizzes out of scope; known bug 5).
- Logging failures cannot break a stage: pass (`test_log_write_failure_does_not_break_the_call`; `annotate_outcome` also catches and reports, `test_annotating_an_unknown_id_does_not_raise`).
- Retention batched and scheduled: pass (`delete_old_generation_logs` deletes id batches of 1000; daily in `config/celery.py`; `test_deletes_old_rows_in_batches_and_keeps_new_ones`).
- Admin list without large columns: pass (`test_changelist_loads_without_large_columns`).
- Dead code: pass after the provider removal above. `_is_retryable_error` stays (used by `generate_embedding`).

Deviations from spec (and why):
- No live run in this pass (see 4.4); the 2026-11-18 evidence stands in for it, with the gaps stated.
- The audit removed dead code the branch did not add (`verify_provider_models`, `get_response_format`), because it duplicated the chains and was the one provider HTTP call outside the dispatcher. F-5 now says nothing checks model IDs at startup.
Open questions for the owner:
- Pass 3's question still stands: keep hourly requeueing of `FAILED`/`PENDING` topics that failed domain validation (up to two writer calls per topic per hour)?
- Should `UNREVIEWED` show a banner to learners? Answered 2026-11-18: no, it is not the learner's business. The handoff now says to remove the review banner.
- Run the queued path once live (worker + Redis, one topic, then its rows in admin) before merging? Answered 2026-11-18: yes, done (see the follow-up below).
Notes for later passes:
- None; this is the last pass. Follow-ups are F-9, F-11, F-12 and known bug 5.
Follow-up, 2026-11-18 (queued path, live):
- At the owner's request: one topic through a real Celery worker (threads pool, local Redis on 6390, no beat), real Groq, the Neon dev DB. Redis queue checked empty first. Topic 4 (WWII Pacific, level 1, no goal sentence: no topic on the dev DB has one, since every roadmap predates pass 2) was reset to `NOT_GENERATED` and started with `request_topic_generation`.
- The worker received exactly three tasks, generate → review → repair, each queued by the stage before it, and nothing else. Result: `READY`/`FIXED`, 390 words, four paragraphs, 3 links, 86 s end to end. The reviewer kept one quoted issue (the summary said Britain declared war on Japan a day after the US; both declared on 8 December 1941), and the repair applied it. Three log rows (`topic-v1`, `review-v1`, `topic_repair-v1`, all gpt-oss-120b step 0) with the verdict and `{"repair": "repaired"}` annotations.
- The three AI calls took about 22 s; the stages took 22 s, 42 s and 26 s in the worker, so most of the time is outside the AI calls (likely database round trips to Neon from a local machine). Not investigated.
- Admin page not checked: the dev DB has no superuser, and I did not create one without asking. The rows were read directly instead.
