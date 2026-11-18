# Spec: AI content review and generation quality

Status: implemented on `feat/ai-content-review`, pending merge. Work happens on branch `feat/ai-content-review` in four passes. Each pass runs in its own session.

Read this file first, then `PROGRESS.md`, then the file for your pass. `AGENTS.md` still applies in full; where this spec is stricter, follow this spec.

| Pass | File | What it delivers |
|---|---|---|
| 1 | [pass-1-ai-layer-and-logging.md](pass-1-ai-layer-and-logging.md) | Clean AI layer: call metadata, honest errors, `AIGenerationLog` |
| 2 | [pass-2-generation-quality.md](pass-2-generation-quality.md) | Better topic summaries: goal sentence, level, neighbours, fixed shape, prompt versions |
| 3 | [pass-3-review-pipeline.md](pass-3-review-pipeline.md) | Narrow reviewer, quote check, fix-not-regenerate, new review statuses |
| 4 | [pass-4-verification.md](pass-4-verification.md) | Verification, architecture audit, docs, frontend handoff |

Frontend handoff note: [frontend-handoff.md](frontend-handoff.md).

---

## 1. Why this exists

Wicky turns a subject into a roadmap of topics. Each topic gets an AI-written summary and 2-5 links, and quizzes are built from that summary. So a wrong summary makes a wrong quiz.

Today, after a summary is written, a second model "reviews" it on four axes (accuracy, audience fit, relevance, completeness). Any failed axis throws the summary away and regenerates it. Two rejections mark the topic `FAILED`.

Evidence in the repo (`docs/backlog/known-bugs.md` #3, `docs/reference/ai-providers.md`):
- The reviewer rejected known-good text 2 of 8 times, both on "completeness".
- It never caught a real error. It was only tested on planted, blatant errors.
- A review outage returns a fake "failed" result. Good content is then regenerated, and Celery's `autoretry_for` on the review task never fires.
- Review notes are discarded. Regeneration is a blind retry with the same prompt.
- The reviewer (Qwen 27B) is smaller than the writer (gpt-oss-120b).
- The writer gets no topic level, no neighbouring topics and no goal. The roadmap step already asks the AI for a one-sentence goal per topic and then drops it. So "completeness" has no fixed meaning.

## 2. Goals

1. Make the first generation good, so the reviewer rarely has anything to say.
2. Shrink the reviewer to two hard questions: is it on topic, and does it contain a clearly wrong statement it can quote?
3. When a quotable error is found, repair the summary. Do not regenerate it.
4. Never treat an AI outage as bad content.
5. Record every AI call so prompts and models can be tuned later from real data.
6. Leave the code cleaner than we found it: one home per concern, a clean dependency direction, nothing wired in twice.

## 3. Decisions (locked)

| # | Decision |
|---|---|
| D1 | Review stays **blocking**: a topic is not shown until review finishes (or is given up on, see D6). |
| D2 | Reviewer chain: **gpt-oss-120b first**, then the others. |
| D3 | Reviewer checks only **relevance** (on topic for the title, goal and level) and **clear factual errors**. Completeness and audience fit are removed from review; they become writer instructions. |
| D4 | **No quote, no rejection.** Every factual issue must quote the exact sentence. Code checks that the quote appears in the summary and ignores issues whose quote does not. |
| D5 | Factual issues → **one repair** of the summary by the writer model. **No re-review** of the repaired summary. |
| D6 | Reviewer outage → Celery retries the review. When retries are used up, **publish the summary as `UNREVIEWED`**. Never `FAILED` because of an outage. |
| D7 | Off-topic → **regenerate once**. Off-topic again → topic `FAILED` with review status `REJECTED`. |
| D8 | Summary format is **plain paragraphs**. The frontend splits on newlines and renders no markdown (`wicky-frontend/apps/web/src/lib/learning/reading.ts`, `toParagraphs`). |
| D9 | The writer gets the topic's goal sentence, level and meaning of that level, previous and next topic titles, and a fixed four-part shape. |
| D10 | Every AI call is logged to a database table, `AIGenerationLog`, browsable in Django admin, with fields for a human verdict. Rows older than a configurable age (default 90 days) are deleted on a schedule. |
| D11 | Every prompt carries a version label, recorded on each log row. |
| D12 | Project is in development (see `AGENTS.md`, Project stage): change models, statuses and API values freely. No backfills, no compatibility shims. |

## 4. Out of scope

- **Resource link quality** (dead or invented URLs, `follow-ups.md` F-1). Do not add link checking. Do not give links to the reviewer.
- **Quizzes** (answer-key checks, quiz prompts, quiz review). Quizzes will be logged automatically by pass 1, because every AI call is. Nothing else about quizzes changes. Quiz prompt text stays where it is.
- Re-reviewing `UNREVIEWED` topics later. Note it as a follow-up; do not build it.
- Moving model IDs to settings (`follow-ups.md` F-5).
- Frontend changes. Pass 4 writes a handoff note only.

## 5. Architecture rules (apply in every pass)

**Dependency direction**
- `apps/learning` may import from `apps/ai`. `apps/ai` must never import from `apps/learning` (today it imports `GenerationError`; pass 1 removes that).
- `apps/ai` knows nothing about topics, subjects, roadmaps or quizzes. It knows actions, chains, providers, schemas, messages and logs.

**One home per concern**
- Provider transport, chains, token ceilings, response parsing and call logging: `apps/ai`.
- What to ask the AI about learning content (prompt text, prompt versions, learning schemas): one prompts module in `apps/learning`. Nothing else in `apps/learning` builds prompt text for topics or roadmaps.
- The topic content pipeline (generate, validate, review, interpret, repair, decide, save): one module in `apps/learning`. Celery tasks only load ids, call into it, and handle retry or failure states. Views and other services never hold pipeline logic.
- Every AI call goes through the single chain dispatcher in `apps/ai`. No code calls a provider any other way. Logging happens in that dispatcher, so no caller logs AI calls by hand.

**Types over dicts**
- AI calls return a small typed result (data plus call metadata), not a bare dict.
- The interpreted review is a typed value: on-topic flag, kept issues, dropped issues.
- Branch on types and enums, never on message text (existing `AGENTS.md` rule).

**Cost and idempotency**
- No path may call the AI twice for the same logical request (existing rule). A redelivered or retried task must not repeat a stage whose result is already persisted.
- Each pipeline stage persists its result before the next stage starts. A retry resumes from the failed stage; it does not redo earlier ones.

**Errors**
- `ProviderError` means the AI could not be reached or returned unusable output on every chain step. It propagates out of Celery tasks so `autoretry_for` works.
- A domain validation failure (content is the wrong shape after normalisation) is a separate error type, and it is not retried by Celery.
- Logging must never break the work. If writing a log row fails, report it to Sentry and continue.

**Prefer normalising to rejecting**
- Cosmetic problems (stray markdown markers, extra blank lines, leading/trailing whitespace, smart vs straight quotes) are fixed in code and never fail a generation.
- Reject only what cannot be fixed: missing or empty fields, wrong types, absurd length (see pass 2).

**Settings, not constants, for operational knobs**
- Log retention age is a Django setting with a default. Prompt versions and chains are code constants.

## 6. Workflow rules for every pass

1. Start from the branch `feat/ai-content-review`. Pass 1 creates it from `main`. Later passes check it out.
2. Read `README.md`, then `PROGRESS.md`, then your pass file. Check that the previous pass's checklist is complete in `PROGRESS.md`. If it is not, finish it first and say so.
3. Run `pytest` before changing anything and note the baseline.
4. Do only your pass. If you find something that belongs to a later pass, write it under "Notes for later passes" in `PROGRESS.md`.
5. Every test listed in your pass file must exist and pass. The full suite must pass at the end of the pass.
6. Update docs your pass makes stale, in the same pass (`AGENTS.md`, `docs/reference/ai-providers.md`, `docs/backlog/*`).
7. Append your entry to `PROGRESS.md`: what was done, deviations from the spec and why, test count before and after, open questions.
8. Commit on the branch with a clear conventional message (for example `feat(ai): ...`). **Do not push. Do not merge.** The owner pushes when ready.
9. If the spec is wrong or impossible on a point, do the closest thing that keeps the invariants, and record the deviation in `PROGRESS.md`. Do not silently change a locked decision (section 3).

## 7. Definition of done (whole spec)

- All four pass entries in `PROGRESS.md` are complete, and pass 4's audit has no open failures.
- Full test suite passes, with no warnings added.
- Every decision in section 3 is visibly true in the code and covered by at least one test.
- `AGENTS.md` and `docs/` describe the new behaviour; no doc describes the old four-axis review or `FLAGGED`.
- A frontend handoff note exists (pass 4).
