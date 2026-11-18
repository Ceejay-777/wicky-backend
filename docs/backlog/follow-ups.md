# Follow-ups

Non-bug improvements worth investigating. Real defects go in `known-bugs.md`.
One entry per item, numbered `F-n`, never reused. Status: `Open`, `In progress`, `Done`, `Dropped` (say why).

| # | Area | Status | Summary |
|---|---|---|---|
| F-1 | Content quality | Open (after MVP) | Generated resource links are mostly dead or invented |
| F-2 | Quiz quality | Open (after MVP) | Quiz answer keys are never verified |
| F-3 | Access control | Open | Topic leaderboard / others-learning have no enrollment check |
| F-4 | AI infra | Open | Move embeddings to `google-genai`; degrade gracefully if embedding fails |
| F-5 | AI infra | Open | Per-model circuit breaker for quota / 503; chain model IDs in settings |
| F-6 | Testing | Open | Opt-in live smoke test; E2E in CI with real pgvector; verify Sentry fires |
| F-7 | Performance | Open (when needed) | HNSW index on `Subject.embedding`; cache explore/suggestions; one Celery retry config |
| F-8 | Product | Open (post-MVP) | Sub-topic suggestions for very broad subjects |
| F-9 | Content quality | Open | Re-review `UNREVIEWED` topics later |
| F-10 | Content quality | Open (after MVP) | Lessons are not accurate enough for niche subjects; plan in `content-quality-plan.md` |

## F-1. Resource links are mostly dead or invented
56 checked links: 24 dead, 13 ok, 19 unverifiable (YouTube 13 dead / 1 ok; recipe/blog deep links dead; kubernetes.io 7/7 ok). Data: `docs/evidence/2026-10-14-groq-model-test.md`. Step 1 of `content-quality-plan.md` (links from real sources) would close this. Nothing checks them, including the reviewer.
Options: verify server-side after generation (YouTube oEmbed, GET with a browser UA; 404/410/DNS = dead, 403/429 = unknown) and regenerate when fewer than 2 survive; prefer Wikipedia (build the URL from the API) and official docs; stop asking for YouTube; give the reviewer the check result. Also a one-off script to prune dead links already stored.
Code: `validate_topic_content`, `generate_topic_content` (`apps/learning/pipeline.py`), `generate_content_for_topic`.

## F-2. Quiz answer keys are never verified
Blind second-model agreement with the key: normal 67-87%, advanced 75-90%, worst single quiz 25%. A wrong key fails a learner who was right.
Options: have a different model answer blind and drop disagreeing questions (rescale `total_points` into the 90-110 / 180-220 window; keep >= 4 / 9 questions or regenerate); judge only on the non-generator model; start in shadow mode (log disagreements) to measure the real rate. Costs 1-6s on a path the user waits on, so pair with generating quizzes ahead in the background.
Code: `generate_quiz`, `_validate_quiz_response`, `_ACTION_CHAINS` (new action e.g. `quiz_check`).

## F-3. No enrollment check on two topic endpoints
`TopicLeaderboardView` and `OthersLearningView` take a bare topic id; every other topic endpoint uses `get_accessible_topic`. They respect the other users' privacy flags, so this is an inconsistency, not a leak.
Decide: does the frontend call them for unenrolled subjects (explore preview)? Should a locked level hide its leaderboard? Should the subject leaderboard stay public?

## F-4. Embeddings
`google-generativeai` is deprecated. Migrate to `google-genai`; confirm 3072-dim output matches stored vectors (else re-embed); add a timeout; if embedding fails, skip the semantic-match step and go to standardize + get-or-create instead of failing `POST /subjects/create`.

## F-5. Provider resilience
Skip a model that returned a quota 429 / 503 for N minutes instead of re-probing it per request; log model, elapsed and tokens per attempt; move chain model IDs to settings so a retired model needs no deploy. `verify_provider_models` already acts as a startup check against the live model list.

## F-6. Testing gaps
Everything AI is mocked and tests run on SQLite. Add an env-gated live smoke test that sends the real roadmap/quiz/topic schemas to each configured model with a latency ceiling; an E2E run against real Postgres/pgvector in CI; confirm Sentry captures a deliberate error. No `--cov` in CI.

## F-7. Performance, when needed
HNSW index on `Subject.embedding`, a pgvector performance test, cache on explore/suggestions, standardize Celery retry config. Also considered: quiz cooldown per user instead of per topic and type (prevents cross-topic quiz spam).

## F-8. Broad-topic suggestions
When a user enters a very broad subject ("Biology"), return several narrower options instead of one. Extends the `narrow` action of `resolve_or_create_subject`.

## F-9. Re-review UNREVIEWED topics
A topic whose review or repair could not complete (outage, stuck past the sweeper cutoff) is published as `UNREVIEWED`. Nothing reviews it later. Option: a periodic task that runs the review stage on `UNREVIEWED` topics when the review chain is healthy, without hiding content that is already shown. Not built.

## F-10. Accurate content for niche subjects
The 2026-11-18 live test (`docs/evidence/2026-11-18-live-content-review/`) found wrong statements in about 1 lesson in 3 even after checking and fixing, because writer and checker rely on the same model memory. The six-step plan (ground roadmaps, lessons and the checker in real sources; fix the example-paragraph hotspot; report-a-mistake; a fixed test set; small checker fixes) is in [content-quality-plan.md](content-quality-plan.md). Overlaps F-1 and F-2.
