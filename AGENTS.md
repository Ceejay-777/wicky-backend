# Wicky Backend — Agent Reference

Django 5.1 · DRF · Celery · PostgreSQL/pgvector · Redis · Groq (text) · Gemini (embeddings). Code is the source of truth; `docs/README.md` indexes the docs.

## Where to look

| Need | Go to |
|---|---|
| API shapes | OpenAPI at `/api/docs/`, serializers in `apps/*/serializers.py` |
| Quiz behavior | `docs/flows/quiz.md` |
| AI chains, models, evidence | `docs/reference/ai-providers.md`, `apps/ai/services.py` |
| Deploy, env vars, services, CI | `docs/reference/deployment.md`, `.env.example` |
| Open bugs / ideas | `docs/backlog/known-bugs.md`, `docs/backlog/follow-ups.md` |
| SSE events | `apps/learning/events.py`, `apps/core/eventstream.py` |
| Live test with real Celery/Redis/SSE, seed users and subjects | `docs/reference/live-testing.md`, `docs/scripts/quiz_live_check.py` |

## Commands

```bash
pytest                                   # all tests; pyproject sets config.settings.test (SQLite, no Postgres/Redis)
pytest apps/learning/tests/test_quizzes.py -k name
python manage.py <cmd> --settings=config.settings.test   # manage.py defaults to development
python manage.py runserver 8000          # no SSE
uvicorn config.asgi:application --port 8000 --reload     # with SSE
celery -A config worker -l info          # and: celery -A config beat -l info
python manage.py seed_subjects | seed_demo   # seed data; see docs/reference/live-testing.md
```

## Architecture rules

- **No ViewSets.** Every endpoint is a `GenericAPIView` with one method. Views call functions from `apps/*/services.py` (imported by name, no service classes) and never touch models. Business logic lives in services, which return dataclasses rather than bare dicts where a shape is non-trivial.
- **Responses:** `{"data": ..., "status": "success"}` / `{"detail": "...", "status": "error"}`. Errors are normalized by `apps/core/exceptions.py`. URLs have no trailing slash.
- **Auth:** JWT in HTTP-only cookies via `CookieJWTAuthentication`; no `Authorization` header.
- **Shared infra** goes in `config/utils/`, not `apps/utils/`.
- **Celery:** the beat schedule is defined in `config/celery.py` (`on_after_finalize`). Task functions in `apps/*/tasks.py` use deferred imports inside the function body.
- **Uniqueness** is enforced with model-level `unique_together`, not soft checks.

## AI calls

- Always pass `action=` to `generate_content()`. Each action has its own chain and token ceiling (`_ACTION_CHAINS`, `_MAX_COMPLETION_TOKENS`). Every Groq request sets `max_completion_tokens` and `reasoning_effort`; `finish_reason == "length"` is an error that moves the chain on.
- Review failures return a flagged result instead of raising.
- AI calls cost money: never add a path that can call the LLM twice for the same request.

## Generation flows (do not bypass)

- **Topic content:** start it only through `request_topic_generation(topic_id)` (atomic `NOT_GENERATED -> GENERATING` claim; the task never claims). `GET /topics/<id>` triggers it for the opened topic and the next two; `FAILED` topics restart only via `POST /topics/<id>/retry`.
- **Roadmap:** async. `POST /subjects/create` enrolls immediately and queues `generate_roadmap_for_subject`; `POST /subjects/<id>/roadmap/retry` retries.
- **Status is pushed over SSE, but the DB is the source of truth.** Events are not replayable; clients must also GET.
- **Topic access:** every topic endpoint goes through `get_accessible_topic` (404 not enrolled, 403 level locked).
- **Quiz generation is async**, claimed through `request_quiz_generation` (one unsubmitted attempt per user, topic and type; the task never claims) and announced by `quiz.status`. Answers are never sent before submit. See `docs/flows/quiz.md`.
- `TopicProgress.Status` values in use: `NOT_STARTED`, `PASSED`, `ADVANCED_PASSED`. `READING` and `QUIZ_READY` are never set.

## Code rules

- **Branch on types, not messages.** Use `isinstance` against exception classes (e.g. `groq.RateLimitError`), typed return values, or `is not None`. Never match `str(e)` substrings or guess from message text.
- **`ProviderError` must propagate out of Celery tasks** so `autoretry_for` works. Catch only domain errors (`GenerationError`) in task bodies; never swallow provider errors.

## Test quirks

- `CELERY_TASK_ALWAYS_EAGER = True`: tasks run synchronously. Throttling is off. Password hasher is MD5.
- `MIGRATION_MODULES = {'ai': None}`: the `ai` app has no migrations in tests.
- pgvector queries check `connection.vendor != "postgresql"` first, so they no-op on SQLite.
- Auth fixtures: `APIClient()` plus `RefreshToken.for_user(user)`; set `client.cookies["access_token"]` by hand.

## Preferences

When testing against a running server, valid user credentials are in `.env`. Log in with them rather than creating a user.
