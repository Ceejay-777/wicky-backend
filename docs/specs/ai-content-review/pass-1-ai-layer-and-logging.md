# Pass 1: AI layer and call logging

Goal: make `apps/ai` a clean, self-contained transport layer that reports honestly what happened on every call and records it in `AIGenerationLog`. After this pass, every AI call in the project (roadmap, topic, review, quiz, standardize, resolve) is logged, and nothing about content behaviour has changed yet, except the outage fix in 1.4.

Read `README.md` sections 3 and 5 first.

## 1.1 Branch

Create `feat/ai-content-review` from the current `main`.

## 1.2 Dependency clean-up

- `apps/ai` stops importing anything from `apps/learning`.
- `apps/ai` owns its own error for "the reply came back but is the wrong shape". Shape validation in `apps/ai` raises it.
- `apps/learning` keeps `GenerationError` for its own domain validation. Where learning code used to catch shape errors from `apps/ai` as `GenerationError`, it now catches the `apps/ai` error, or maps it, at the point where it calls `apps/ai`. Behaviour seen by tasks stays the same: a malformed reply is a domain failure, not a provider failure, unless every chain step was malformed (see 1.3).
- Remove the duplicate `TOPIC_CONTENT_SCHEMA` from `apps/ai/prompts.py`. It disagrees with the learning copy: it allows `book`, `course` and `podcast` but not `wikipedia`. `generate_content` must require the caller to pass a schema; there is no topic-shaped default inside `apps/ai`.
- `apps/ai/prompts.py` keeps only what is generic or belongs to non-learning actions that already live there (standardize, resolve). The review prompt and review schema move out in pass 3. Leave them in place for now, but nothing new about learning goes into `apps/ai`.

## 1.3 Call results

- Every public AI function returns a typed result carrying:
  - the parsed data;
  - which provider and model served it;
  - how many chain steps were tried;
  - total latency;
  - the id of the log row for the successful call, so callers can attach an outcome to it (1.5).
- Callers in `apps/learning` and `apps/core` are updated to read the data from the result. Their behaviour is otherwise unchanged in this pass.
- Shape validation moves into the chain loop. A malformed reply from one step counts as that step failing, and the chain moves on, the same way `finish_reason == "length"` already does. If every step fails for any reason, `ProviderError` is raised.
- Callers pass a **prompt version** (string) and a small **context** (structured, JSON-safe: for example topic id, subject id, quiz attempt id). Both are required arguments; there are no silent defaults. Existing callers that have no versioned prompt yet use a clear placeholder version (for example `"<action>-v0"`). Pass 2 replaces the topic and roadmap ones.

## 1.4 Review outage fix

- The review function no longer builds a fake failed result. If every review step fails, it raises `ProviderError`, like every other action.
- The current two-try loop for malformed review replies goes away. Moving to the next chain step (1.3) replaces it.
- The review task's `autoretry_for=(ProviderError,)` now actually fires. On the **last** retry the task must not mark the topic failed because of an outage. For this pass only, keep the old behaviour of regenerating on a genuine "not passed" verdict. Outage handling at retry exhaustion is: publish the topic as ready, with the review status left as it was. Pass 3 introduces `UNREVIEWED` and replaces this.
- Reorder the `review` chain: gpt-oss-120b (medium) first, then qwen3.8-27b, then gpt-oss-20b (medium).

## 1.5 `AIGenerationLog`

A model in `apps/ai`. One row per **chain step attempt**: a call that falls through two models writes two rows, one failed and one successful.

Each row records:
- action (the existing action names);
- prompt version;
- provider and model;
- the chain step's position and whether it was the step that served the call;
- outcome: success, or a failure kind (`http_error`, `truncated`, `malformed_json`, `shape_invalid`, `timeout`, `other`) plus a short error message;
- the request messages exactly as sent;
- the raw response content (text as returned) and, on success, the parsed data;
- latency in milliseconds; token usage if the provider returns it;
- the context passed by the caller;
- a nullable topic reference for fast lookup. It is derived from the context's topic id when present. Use a plain indexed integer, not a foreign key, so `apps/ai` does not depend on `apps/learning`;
- **outcome annotation**: a nullable JSON field the caller may fill after the call (for example the interpreted review verdict, or "repaired"). It is written through a small function in `apps/ai` that takes the log row id; callers never touch the model directly;
- human verdict: nullable choice of `agree`, `disagree`, `unsure`;
- human note: free text;
- created at.

Rules:
- Writing the row must not depend on the caller's transaction. A row for a failed call must persist even when the surrounding work fails or rolls back.
- Logging failures are caught, sent to Sentry, and never raised.
- Very large text fields are stored up to a cap (setting, default 100 KB per field) with a clear truncation marker.
- Indexes support: newest first; filter by action; filter by model; filter by outcome; lookup by topic id; filter by prompt version.

### Admin

- Register `AIGenerationLog` in Django admin. This is the first `admin.py` in the project; keep it minimal and tidy.
- List view: created, action, prompt version, model, outcome, latency, topic id, human verdict. Filters: action, model, outcome, prompt version, human verdict, date. Search by topic id.
- The list view must not load the large text or JSON columns.
- Detail view: all fields are read-only except human verdict and human note. Show request messages and response in a readable form.

### Retention

- A Django setting gives the retention age in days (default 90).
- A Celery beat task, registered in `config/celery.py` alongside the others, deletes older rows in bounded batches, so a large backlog never becomes one long delete. Run it daily.

### Migrations and tests

- `config/settings/test.py` sets `MIGRATION_MODULES = {'ai': None}` because the existing `ai` migration enables pgvector, which SQLite cannot run. Keep that working. The new table must exist in tests (Django creates unmigrated app tables), and the real migration must apply cleanly on Postgres. Record in `PROGRESS.md` how you checked the Postgres side (for example `makemigrations --check` and a migration plan against development settings).

## 1.6 Docs

- `AGENTS.md`, AI calls section: replace "Review failures return a flagged result instead of raising" with the new rule (all actions raise `ProviderError` when the chain is exhausted). Add: every AI call needs a prompt version and context; every call is logged automatically; `apps/ai` never imports `apps/learning`.
- `docs/reference/ai-providers.md`: new review chain order; a short "Call log" section saying where logs live and how retention works.

## 1.7 Tests (all required)

Mock at the HTTP boundary (the provider client), not inside the dispatcher.

Dispatcher and results
- A successful first step returns a result with data, provider, model, steps tried = 1, and a log row id.
- First step HTTP error, second succeeds: steps tried = 2; two log rows (one failure, one success) in order.
- First step `finish_reason == "length"`: logged as `truncated`, chain moves on.
- First step returns invalid JSON: logged as `malformed_json`, chain moves on.
- First step returns valid JSON in the wrong shape: logged as `shape_invalid`, chain moves on.
- Every step fails: `ProviderError` raised; one failed row per step.
- Calling without a prompt version or without context is rejected (type error or explicit error), not defaulted.
- `generate_content` without a schema is rejected.

Logging
- The row stores request messages, raw response, parsed data, latency, prompt version, context and topic id.
- If the log write raises, the AI call still returns its result and Sentry is called.
- A failed call's row persists when the caller's surrounding transaction rolls back.
- Fields over the size cap are truncated with the marker.
- Attaching an outcome annotation by log id stores it; an unknown id does not raise.

Review outage
- Every review step fails: the review function raises `ProviderError` and returns no fake result.
- Review task with an outage: Celery retry is attempted (assert the retry, not the sleep). At retry exhaustion the topic is not `FAILED`.
- Review chain order is gpt-oss-120b, qwen3.8-27b, gpt-oss-20b.

Retention
- Rows older than the setting are deleted, newer rows kept; works across more rows than one batch.

Architecture
- A test fails if any module under `apps/ai` imports from `apps.learning` (scan the source files' imports).

Admin
- Admin changelist and change pages load (status 200) for a superuser, and the changelist query does not select the large columns.

## 1.8 Done when

- [x] Branch created, all items above implemented
- [x] All listed tests exist and pass; full suite passes
- [x] Docs updated
- [x] `PROGRESS.md` entry written
- [x] Committed on the branch, not pushed
