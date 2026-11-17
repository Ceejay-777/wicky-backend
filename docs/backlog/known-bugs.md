# Known bugs (open only)

Fixed items are removed; git history has them. Add new findings at the bottom with the next number. Improvements that are not defects go in `follow-ups.md`.

Severity: **High** (security / data integrity) · **Medium** · **Low**

| # | Severity | Area | Summary |
|---|---|---|---|
| 1 | Medium | Validation | `answers` accepts booleans; `notification-frequency` unchecked |
| 2 | Medium | Throttle | Authenticated users are not throttled (`user` rate defined, `UserRateThrottle` not enabled) |
| 3 | Low | AI quality | Reviewer falsely rejects good content ~2 in 8 runs |
| 4 | Low | Misc | Small issues, list below |

## 1. Input validation
- `answers: [true, ...]` is accepted as option 1 (`bool` is an `int`).
- Reported 2026-10-12 and not rechecked: `notification-frequency` accepts `1.5` and `true`.
- Fix: use request serializers in the views; reject `bool`.

## 2. No authenticated throttle
`DEFAULT_THROTTLE_CLASSES` has only `AnonRateThrottle`, so the `'user': '100/hour'` rate is unused. Decide whether quiz generate (it spends AI tokens) needs its own scoped throttle.

## 3. Reviewer false rejections
Reviewer `qwen/qwen3.8-27b` (primary until the review chain was reordered to gpt-oss-120b first) rejected known-good text 2 of 8 runs, both on "completeness". A rejection triggers regeneration (up to 2 attempts, then `FLAGGED`). Consider a softer completeness criterion or requiring two failing axes.

## 4. Small issues
- Dead AI-generated links and wrong link `type` labels (see F-1).
- A non-integer topic id (`/topics/abc`) returns Django's HTML 404, not the JSON envelope.
- `resource-links-viewed` overwrites `resource_links_viewed_at` on every call.
- After `ADVANCED_PASSED`, asking for another advanced quiz says "pass the normal quiz and engage with resource links first".
- `/api/health/` reported `celery: true` with no worker running (2026-10-12, not rechecked).
- `google.generativeai` is deprecated (FutureWarning); see F-4.
- Not covered by the 2026-10-12 live run: Brevo emails, Beat dispatch, level-up / subject completion, auto-select, signup / password-reset flows.
