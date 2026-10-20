# Quiz flow

Quick overview. The code is the source of truth: `apps/learning/services.py` (quiz section), `apps/learning/views.py`.

## Steps

```
learner opens topic
  -> GET  /topics/<id>                      progress: status, *_available_at, advanced_quiz_available
  -> POST /topics/<id>/quiz/generate        starts (or resumes) a quiz; questions arrive via SSE event quiz.status
  -> GET  /topics/<id>/quiz/attempts/<id>   fetch the questions once status is READY
  -> learner answers locally
  -> POST /topics/<id>/quiz/submit          returns score, pass/fail, per-question results, level_check
  -> GET  /topics/<id>/quiz/attempts/...    re-read it later (any tab or device)
```

All routes are under `/api/learning/`, need the auth cookie, and go through `get_accessible_topic` (404 not enrolled, 403 level locked).

## Generate (async)

`POST .../quiz/generate` never waits for the AI. It claims an attempt and queues `generate_quiz_for_attempt`:

1. An unsubmitted attempt for this user, topic and type exists: return it.
   - `READY` or fresh `GENERATING`: returned as is.
   - `FAILED`, or `GENERATING` for over 15 min: re-queued on the same row (same `attempt_number`).
2. Otherwise check eligibility and cooldown, then create a `GENERATING` attempt. The check and insert run under a row lock on the learner's `TopicProgress`, so concurrent callers get the winner's row and one task is queued.

`resumed` is `true` when this call started no generation (existing quiz returned, ready or still generating) and `false` for a new quiz or a retry.

Response: `202` while `GENERATING`, `200` for `READY`/`FAILED`; `status` is in the payload and `questions` is empty until `READY`.
The task sets `READY` or `FAILED` and publishes SSE `quiz.status` (`subject_id`, `topic_id`, `attempt_id`, `quiz_type`, `status`) to the owner only. Events are not replayable: after generate, and on reconnect, also `GET .../attempts/<id>`. To retry a `FAILED` quiz, call generate again.

| Rule | Value |
|---|---|
| Types | `NORMAL`, `ADVANCED` (default `NORMAL`) |
| Questions | normal 4-6, advanced 9-12 (the AI picks; not fixed) |
| `total_points` | normal 90-110, advanced 180-220 |
| Normal eligibility | topic not already passed |
| Advanced eligibility | normal passed and resource links viewed (`POST /resource-links-viewed`), advanced not yet passed |
| Cooldown | 1 hour from the last **submitted** attempt's `created_at`, per topic and quiz type. Unsubmitted attempts do not count |
| `prior_missed_questions` | optional; steers a new generation. Ignored when resuming |

Errors: 400 (not eligible / cooldown / invalid body). AI failure is no longer an HTTP error; it shows as `status: FAILED`.

## Submit

- Body: `attempt_id`, `answers` (one option index 0-3 per question).
- Pass: `correct / questions >= 0.6`. `score = int(total_points * ratio)` if passed, else 0.
- Pass of a normal quiz: topic `PASSED`, `completed_at` set, points earned, `level_check` returned (level-up happens here).
- Pass of an advanced quiz: topic `ADVANCED_PASSED`, `advanced_completed_at` set.
- Errors: 404 unknown or not-yours attempt, 400 already submitted / not `READY` / wrong answer count.

## What the client sees

Questions are always `{question, options}`. `correct_index` and `explanation` are never sent before submit.
After submit, `results[]` carries `selected_index`, `correct_index`, `is_correct`, `explanation`, plus `correct_count` and `question_count`.

Same shape from generate, submit and the two GET attempt endpoints: `id, status, quiz_type, attempt_number, questions, total_points, answers, score, passed, submitted_at, results, correct_count, question_count`.
`GET .../attempts/latest?quiz_type=` returns the newest **submitted** attempt (404 if none).

## Not stored

Picks made before submit are not saved server-side. On a resume the client restores them itself.
