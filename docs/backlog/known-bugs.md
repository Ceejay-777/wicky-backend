# Known bugs (open only)

Fixed items are removed; git history has them. Add new findings at the bottom with the next number. Improvements that are not defects go in `follow-ups.md`.

Severity: **High** (security / data integrity) · **Medium** · **Low**

| # | Severity | Area | Summary |
|---|---|---|---|
| 1 | High | Quiz / AI cost | Two simultaneous `quiz/generate` calls can both call the AI and create two attempts |
| 2 | Medium | Validation | `quiz_type` not validated on generate; `answers` accepts booleans |
| 3 | Medium | Infra | Stale DB connection after a long in-request LLM call (quiz generate) |
| 4 | Medium | Throttle | Authenticated users are not throttled (`user` rate defined, `UserRateThrottle` not enabled) |
| 5 | Low | AI quality | Reviewer falsely rejects good content ~2 in 8 runs |
| 6 | Low | Misc | Small issues, list below |

## 1. Concurrent quiz generation
`generate_quiz` has no lock. The open-attempt check, the LLM call and the insert are separate steps, so a double click or two tabs can run two LLM generations; the second insert may also fail on the `(user, topic, quiz_type, attempt_number)` unique constraint (500). Fix: claim a slot atomically before the LLM call (planned, not implemented).

## 2. Input validation
- `quiz/generate` ignores `GenerateQuizRequestSerializer`: `quiz_type: "EXPERT"` is not rejected (it runs the advanced path and stores the bad type).
- `answers: [true, ...]` is accepted as option 1 (`bool` is an `int`).
- Reported 2026-10-12 and not rechecked: `notification-frequency` accepts `1.5` and `true`.
- Fix: use the existing request serializers in the views; reject `bool`.

## 3. Stale DB connection
Quiz generation calls the LLM inside the request (3-9s, up to ~45s on fallbacks). A pooled Neon connection can be closed meanwhile, giving a 500 and then spurious 401s. Fix to try: `CONN_MAX_AGE` / `CONN_HEALTH_CHECKS`, and do not hold a transaction across the LLM call. Roadmap generation already runs in Celery.

## 4. No authenticated throttle
`DEFAULT_THROTTLE_CLASSES` has only `AnonRateThrottle`, so the `'user': '100/hour'` rate is unused. Decide whether quiz generate (expensive) needs its own scoped throttle.

## 5. Reviewer false rejections
Primary reviewer `qwen/qwen3.8-27b` rejected known-good text 2 of 8 runs, both on "completeness". A rejection triggers regeneration (up to 2 attempts, then `FLAGGED`). Consider a softer completeness criterion or requiring two failing axes.

## 6. Small issues
- Dead AI-generated links and wrong link `type` labels (see F-1).
- A non-integer topic id (`/topics/abc`) returns Django's HTML 404, not the JSON envelope.
- `resource-links-viewed` overwrites `resource_links_viewed_at` on every call.
- After `ADVANCED_PASSED`, asking for another advanced quiz says "pass the normal quiz and engage with resource links first".
- `/api/health/` reported `celery: true` with no worker running (2026-10-12, not rechecked).
- `google.generativeai` is deprecated (FutureWarning); see F-4.
- Not covered by the 2026-10-12 live run: Brevo emails, Beat dispatch, level-up / subject completion, auto-select, signup / password-reset flows.
