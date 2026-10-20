# Live testing and seed data

Unit tests run on SQLite with eager tasks. To exercise real Celery, Redis, Postgres/pgvector and SSE, run the stack locally against a throwaway database. Never point this at a shared database.

## Quiz generation end-to-end check

`docs/scripts/quiz_live_check.py` has two modes, `worker` and `check`. It mocks only the AI call and verifies:
- 202, then the `quiz.status` SSE event, delivered to the owner only
- one attempt and one AI call under 8 simultaneous requests
- retries ending in `FAILED`, and retrying a `FAILED` quiz
- cleanup of a stuck attempt

It runs 21 checks and exits 1 on any failure.

```bash
docker run -d --name wicky-live-redis -p 6390:6379 redis:7-alpine
docker run -d --name wicky-live-pg -e POSTGRES_PASSWORD=live -e POSTGRES_DB=wicky_live -p 5440:5432 pgvector/pgvector:pg16

export DJANGO_SETTINGS_MODULE=config.settings.development
export DATABASE_URL=postgresql://postgres:live@127.0.0.1:5440/wicky_live
export CELERY_BROKER_URL=redis://127.0.0.1:6390/0
export DJANGO_SECRET_KEY=live-test-secret GROQ_API_KEY=unused GEMINI_API_KEY=unused BREVO_API_KEY=unused

python manage.py migrate
python -m uvicorn config.asgi:application --port 8011          # terminal 1
python docs/scripts/quiz_live_check.py worker                  # terminal 2
python docs/scripts/quiz_live_check.py check                   # terminal 3

docker rm -f wicky-live-redis wicky-live-pg
```

Notes:
- Exported variables win over `.env`, so the dummy API keys keep the run off the real providers.
- Use `127.0.0.1`, not `localhost`: on Windows each new DB connection first tries IPv6 and adds about 2s.
- On Windows use `python -m uvicorn` (the venv `uvicorn` shim exits silently) and the default `threads` worker pool. Set `WORKER_POOL=prefork` on Linux or macOS.
- Restart the web server and worker after code changes; there is no autoreload.
- Not covered: a worker process killed mid-task (only the stale-attempt cleanup is), and the prefork pool.

## Seed commands

Both are management commands (`python manage.py <name>`) and are safe to re-run.

| Command | What it does | Needs |
|---|---|---|
| `seed_subjects [--count N]` | Creates subjects (default 3, `0` = all 15) through the real subject-resolution flow and enrolls a `seed@wicky.dev` user | Real Groq and Gemini keys (spends AI tokens); a running worker for roadmaps |
| `seed_demo` | Creates 2 main users (`demo1@seed.wicky.test`, `demo2@seed.wicky.test`) and 25 NPC users (`npcNN@seed.wicky.test`) with enrollments, progress, quiz attempts and interests. Password for all: `SeedPass123!` | At least one subject with a `READY` roadmap, so run `seed_subjects` first |

Seeded data is meant for UI and leaderboard work. `seed_demo` writes some `TopicProgress` statuses (`READING`, `QUIZ_READY`) that the app itself never sets.
