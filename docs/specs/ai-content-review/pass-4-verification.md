# Pass 4: Verification and architecture audit

Goal: prove the whole spec is done and the code follows the rules. Fix what falls short. Write the frontend handoff. This pass builds nothing new, except small fixes the audit finds.

Read `README.md` fully and every entry in `PROGRESS.md` first.

## 4.1 Re-read with fresh eyes

Read the final diff of the branch against `main` from top to bottom before running anything. Note anything that looks twisted, duplicated or out of place.

## 4.2 Decision check

For each decision D1-D12 in `README.md` section 3, write one line in `PROGRESS.md`: where it lives in the code, and the test that proves it. Any decision without both is a failure to fix in this pass.

## 4.3 Architecture audit

Each item gets pass, or fail plus the fix made:
- `apps/ai` imports nothing from `apps/learning` (the import test exists and passes).
- `apps/ai` mentions no topics, subjects, roadmaps or quizzes beyond generic context keys.
- Every AI call goes through the single dispatcher. Search for direct HTTP or SDK calls outside `apps/ai/providers.py` and the dispatcher.
- Every AI call passes a prompt version and a context. No placeholder `-v0` versions remain for roadmap or topic flows; quiz, standardize and resolve may keep placeholders (note them).
- No prompt text for roadmap, topic, review or repair outside the learning prompts module.
- Pipeline logic lives only in the pipeline module. Tasks, views and other services hold no decision logic about review outcomes.
- No branching on exception message text.
- No bare dicts returned where a typed result was specified (AI call result, review verdict).
- No leftover references to `FLAGGED`, `review_attempts`, the four review axes, or the old `TOPIC_CONTENT_SCHEMA` in `apps/ai`. Search code, tests and docs.
- No path calls the AI twice for one request: reason through generate → review → repair, with redelivery and retries, and confirm each stage is guarded.
- Logging failures cannot break a stage (test exists).
- Retention runs in bounded batches and is scheduled.
- Admin list view does not load large columns.
- Dead code removed: unused imports, helpers, constants and tests for deleted behaviour.

## 4.4 Run everything

- Full `pytest`: all pass, no new warnings. Record the counts before the branch (`main`) and now.
- `python manage.py makemigrations --check --dry-run` against development settings: no missing migrations.
- If a local Postgres, Redis and Groq key are available (see `docs/reference/live-testing.md`), run one real topic through the pipeline and check its log rows in admin. If not available, say so in `PROGRESS.md`; do not fake it.

## 4.5 Docs pass

- `AGENTS.md`, `docs/README.md` and everything under `docs/` describe the new behaviour only. Add `specs/` to the `docs/README.md` index.
- Mark this spec's status in `README.md` as "implemented on `feat/ai-content-review`, pending merge".

## 4.6 Frontend handoff note

Write `docs/specs/ai-content-review/frontend-handoff.md` for the frontend repo (`wicky-frontend`). Do not edit the frontend. Cover:
- `ReviewStatus` type changes: old `PENDING | PASSED | FAILED | FLAGGED`, new `PENDING | PASSED | FIXED | UNREVIEWED | REJECTED` (`packages/types/src/common.ts`).
- The topic page's `underReview` check, which today tests `FLAGGED` or `FAILED` (`apps/web/src/app/(app)/topics/[id]/page.tsx`): what it should test now, and the suggested wording per status. `UNREVIEWED` and `FIXED` show content normally; `REJECTED` shows the failed or retry state.
- The new `description` field on topics, and where it could be shown (optional).
- The summary is now always four plain paragraphs; nothing to change in `toParagraphs`.

## 4.7 Follow-ups

Make sure `docs/backlog/follow-ups.md` has, briefly:
- Re-review `UNREVIEWED` topics later.
- Use `AIGenerationLog` human verdicts to measure reviewer precision before changing the prompt.
- Quiz review (to be specced separately).
- Link quality (F-1, existing).

## 4.8 Done when

- [ ] Decision table and audit written in `PROGRESS.md`, no open failures
- [ ] Full suite passes; migration check clean
- [ ] Docs and frontend handoff written
- [ ] Final `PROGRESS.md` entry includes a short summary for the owner: what changed, what to look at first in admin, known limits
- [ ] Committed on the branch, **not pushed, not merged**
