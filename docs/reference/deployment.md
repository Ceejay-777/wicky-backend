# Deployment

Facts only. Files: `Dockerfile`, `railway.json`, `docker-compose.yml`, `.github/workflows/ci.yml`, `config/settings/`, `config/celery.py`.

## Services (Railway: 3 services from one image)

| Service | Command | Notes |
|---|---|---|
| web | `migrate && collectstatic && uvicorn config.asgi:application --host 0.0.0.0 --port $PORT --workers 4 --timeout-keep-alive 75 --timeout-graceful-shutdown 15` | ASGI (not gunicorn) because SSE streams are long-lived. Static via Whitenoise |
| worker | `celery -A config worker -l info --concurrency=2` | prefetch 1, late acks, hard/soft limit 30/25 min, shutdown timeout 60s |
| beat | `celery -A config beat -l info` | Schedule defined in code (`config/celery.py`, `on_after_finalize`); `django_celery_beat` DatabaseScheduler is the configured scheduler |

Settings: `config.settings.production` in the image; `docker-compose.yml` (web, worker, beat, redis) uses `development`. Postgres is external (Railway-provisioned, pgvector enabled by `apps/ai` migration).

## Periodic tasks

| Task | Every | Does |
|---|---|---|
| `learning.dispatch_notifications` | 15 min | fans out `send_notification` for due subjects |
| `learning.cleanup_stuck_generations` | 60 min | topics stuck in `GENERATING` > 1h are reset to `NOT_GENERATED`; stuck roadmaps become `FAILED` |
| `learning.auto_select_subjects` | 15 min | enrolls opted-in idle users into a suggestion |

## Redis

Celery broker, SSE cross-process events (`EVENTSTREAM_REDIS`, derived from `CELERY_BROKER_URL`), production Django cache (shared throttle counters), and the health check ping. Celery results go to Postgres (`django-db`, 24h expiry). No persistence configured.

## Environment variables (`.env.example` is the full list)

| Variable | Purpose |
|---|---|
| `DJANGO_SECRET_KEY`, `DATABASE_URL`, `CELERY_BROKER_URL` | core |
| `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `FRONTEND_URL`, `SERVICE_BASE_URL` | hosts / CORS / email links |
| `GROQ_API_KEY` | all text generation and review |
| `GEMINI_API_KEY` | embeddings only |
| `BREVO_API_KEY`, `DEFAULT_FROM_EMAIL` | email (sender must be verified in Brevo) |
| `SENTRY_DSN`, `ENVIRONMENT` | optional error tracking |
| `SECURE_SSL_REDIRECT` | default `True` |
| `PORT` | web only, set by the host |

All three services get the same variable set.

## Production security settings

HSTS 1y (subdomains, preload), SSL redirect, secure session/CSRF cookies, `X-Frame-Options DENY`, nosniff. JWT cookies: HTTP-only, access 15 min, refresh 7 days. Throttle: anonymous 10/hour (see known bugs for the authenticated rate).

## Health

`GET /api/health/`: checks database, redis, celery; 200 healthy, 503 degraded; cached 5s.

## CI

GitHub Actions on push/PR to `main`: Python 3.12, `config.settings.test` (SQLite in memory, tasks eager, no Redis/Postgres), `pytest --tb=short -v`. No CD step.

## Docker build

`collectstatic` runs at build (dummy `DATABASE_URL`/`DJANGO_SECRET_KEY`, overridden at runtime) and again at start.
