# Quiz flow

Quick overview. The code is the source of truth: `apps/learning/services.py` (quiz section), `apps/learning/views.py`.

## Steps

```
learner opens topic
  -> GET  /topics/<id>                      progress: status, *_available_at, advanced_quiz_available
  -> POST /topics/<id>/quiz/generate        returns a quiz (new, or the unfinished one)
  -> learner answers locally
  -> POST /topics/<id>/quiz/submit          returns score, pass/fail, per-question results, level_check
  -> GET  /topics/<id>/quiz/attempts/...    re-read it later (any tab or device)
```

All routes are under `/api/learning/`, need the auth cookie, and go through `get_accessible_topic` (404 not enrolled, 403 level locked).

## Generate

1. An unsubmitted attempt for this user, topic and quiz type exists: return it with `resumed: true`. No AI call.
2. Otherwise check eligibility, then cooldown, then call the AI and save a new `QuizAttempt`.

| Rule | Value |
|---|---|
| Types | `NORMAL`, `ADVANCED` (default `NORMAL`) |
| Questions | normal 4-6, advanced 9-12 (the AI picks; not fixed) |
| `total_points` | normal 90-110, advanced 180-220 |
| Normal eligibility | topic not already passed |
| Advanced eligibility | normal passed and resource links viewed (`POST /resource-links-viewed`), advanced not yet passed |
| Cooldown | 1 hour from the last **submitted** attempt's `created_at`, per topic and quiz type. Unsubmitted attempts do not count |
| `prior_missed_questions` | optional; steers a new generation. Ignored when resuming |

Errors: 400 (not eligible / cooldown), 503 (AI unavailable).

## Submit

- Body: `attempt_id`, `answers` (one option index 0-3 per question).
- Pass: `correct / questions >= 0.6`. `score = int(total_points * ratio)` if passed, else 0.
- Pass of a normal quiz: topic `PASSED`, `completed_at` set, points earned, `level_check` returned (level-up happens here).
- Pass of an advanced quiz: topic `ADVANCED_PASSED`, `advanced_completed_at` set.
- Errors: 404 unknown or not-yours attempt, 400 already submitted / wrong answer count.

## What the client sees

Questions are always `{question, options}`. `correct_index` and `explanation` are never sent before submit.
After submit, `results[]` carries `selected_index`, `correct_index`, `is_correct`, `explanation`, plus `correct_count` and `question_count`.

Same shape from submit and from the two GET attempt endpoints: `id, quiz_type, attempt_number, questions, total_points, answers, score, passed, submitted_at, results, correct_count, question_count`.
`GET .../attempts/latest?quiz_type=` returns the newest **submitted** attempt (404 if none).

## Not stored

Picks made before submit are not saved server-side. On a resume the client restores them itself.
