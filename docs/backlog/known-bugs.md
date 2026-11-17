# Known bugs (open only)

Fixed items are removed; git history has them. Add new findings at the bottom with the next number. Improvements that are not defects go in `follow-ups.md`.

Severity: **High** (security / data integrity) · **Medium** · **Low**

| # | Severity | Area | Summary |
|---|---|---|---|
| 1 | Medium | Validation | `answers` accepts booleans; `notification-frequency` unchecked |
| 2 | Medium | Throttle | Authenticated users are not throttled (`user` rate defined, `UserRateThrottle` not enabled) |
| 4 | Low | Misc | Small issues, list below |

## 1. Input validation
- `answers: [true, ...]` is accepted as option 1 (`bool` is an `int`).
- Reported 2026-10-12 and not rechecked: `notification-frequency` accepts `1.5` and `true`.
- Fix: use request serializers in the views; reject `bool`.

## 2. No authenticated throttle
`DEFAULT_THROTTLE_CLASSES` has only `AnonRateThrottle`, so the `'user': '100/hour'` rate is unused. Decide whether quiz generate (it spends AI tokens) needs its own scoped throttle.

## 3. Reviewer false rejections (closed 2026-11-17)
Replaced by the narrow review: only off-topic or quoted, verified factual errors count, and errors are repaired rather than regenerated (`docs/reference/ai-providers.md`, Topic review and repair).

## 4. Small issues
- Dead AI-generated links and wrong link `type` labels (see F-1).
- A non-integer topic id (`/topics/abc`) returns Django's HTML 404, not the JSON envelope.
- `resource-links-viewed` overwrites `resource_links_viewed_at` on every call.
- After `ADVANCED_PASSED`, asking for another advanced quiz says "pass the normal quiz and engage with resource links first".
- `/api/health/` reported `celery: true` with no worker running (2026-10-12, not rechecked).
- `google.generativeai` is deprecated (FutureWarning); see F-4.
- Not covered by the 2026-10-12 live run: Brevo emails, Beat dispatch, level-up / subject completion, auto-select, signup / password-reset flows.
