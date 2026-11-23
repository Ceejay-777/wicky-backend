# Pass 3: Narrow review, quote check, repair

Goal: replace the four-axis review with a narrow, evidence-based review. Repair quotable errors instead of regenerating. Make every outcome explicit in the topic's state.

Read `README.md` sections 3 and 5 and the pass 1 and 2 entries in `PROGRESS.md` first.

## 3.1 Review statuses

`Topic.ReviewStatus` becomes:

| Value | Meaning | Content visible? |
|---|---|---|
| `PENDING` | Not reviewed yet (new, generating, or waiting for review) | No |
| `PASSED` | Reviewed; on topic; no verified issues | Yes |
| `FIXED` | Reviewed; verified issues found; summary repaired | Yes |
| `UNREVIEWED` | Review or repair could not complete (outage, unusable repair); original published | Yes |
| `REJECTED` | Off-topic twice; content not usable | No (topic `FAILED`) |

`FLAGGED` and the old `FAILED` review value are removed. `review_attempts` is removed.

Add one counter for content generations of the current cycle (for D7's "regenerate once"). The retry endpoint resets it. Name it for what it counts.

Persist the interpreted review on the topic: a JSON field holding the kept issues (quote, problem, correction), the count of dropped issues, and the on-topic flag. The repair stage reads its input from here, so a retried repair never re-runs review. Clear it when a new generation cycle starts.

Content status values are unchanged.

## 3.2 Reviewer prompt and schema

Both live in the learning prompts module (pass 2) with their own version. Remove the old review prompt, system instruction and schema from `apps/ai/prompts.py`.

The reviewer receives: subject, topic title, goal sentence (when present), level and its meaning, previous and next titles, and the summary. It does **not** receive the resource links.

It is told to:
- decide only whether the summary is about this topic, within its level and boundaries (on-topic: yes or no, with a one-sentence reason);
- list only statements that are **clearly factually wrong**, each with the exact sentence copied verbatim from the summary, what is wrong, and a corrected sentence;
- not report style, tone, reading level, omissions, missing depth, ordering or preferences. An empty issue list is the normal, expected answer.

Reply shape: an on-topic flag, a reason, and a list of issues (quote, problem, correction). Keep it small; no per-axis objects.

## 3.3 Interpreting the review (code, not AI)

- For each issue, check that the quote appears in the summary after normalising both sides the same way: case, whitespace and quote characters. If it does not appear, drop the issue.
- An issue with an empty quote, problem or correction is dropped.
- Duplicate quotes count once.
- The result is a typed verdict: on-topic flag, kept issues, dropped-issue count.
- Attach the verdict to the review call's log row as its outcome annotation (pass 1).

## 3.4 Decision rules

After review:

| Verdict | Action |
|---|---|
| On topic, no kept issues | `READY`, `PASSED` |
| On topic, kept issues | Persist the verdict, then run the repair stage |
| Off topic, first time this cycle | Persist the verdict; regenerate (back to generation; counter +1) |
| Off topic, second time | `FAILED`, `REJECTED` |

Off-topic wins over issues: an off-topic summary is never repaired.

Repair stage:
- One AI call to a new `topic_repair` action, with its own chain (the writer's chain) and token ceiling, per the `AGENTS.md` rule that every action has its own.
- Input: the summary, the kept issues and the same topic context as the writer, plus the plain-paragraph format rules. Instruction: change only what the issues require; keep everything else word for word where possible.
- The repaired summary goes through the same normalisation and validation as a generated one (pass 2).
- Valid → save it; `READY`, `FIXED`. Attach "repaired" to the repair call's log row.
- Invalid after normalisation → keep the original; `READY`, `UNREVIEWED`. Log why.
- No re-review of a repaired summary (D5).

Outage rules (D6):
- Review stage: `ProviderError` propagates; Celery retries. On the last retry → original published, `READY`, `UNREVIEWED`.
- Repair stage: same; on the last retry → original published, `READY`, `UNREVIEWED`. Review is not re-run.
- Generation stage outage: unchanged from today (retries, then `FAILED`, review status `PENDING`, so the stale sweeper can requeue it).

## 3.5 Stages and tasks

- The pipeline module (pass 2) now owns generation, review, interpretation, decision and repair, as separate stages. Each stage reads its input from the database and writes its result before the next is queued.
- One Celery task per stage: generate, review, repair. Each is thin and uses `autoretry_for=(ProviderError,)` with the existing backoff settings. Tasks contain no decision logic; they call the pipeline and, on the last retry, call the pipeline's "give up" path for that stage.
- A task that finds the topic no longer in the state its stage expects (for example already `READY`, or regenerated meanwhile) does nothing. That makes redelivery safe and guarantees no double AI calls.
- `request_topic_generation` stays the only way to start generation (`AGENTS.md`). The off-topic regenerate path goes through the same claim.
- Every terminal transition publishes `topic.status` over SSE as today.

## 3.6 Other code that reads review state

Update everything that used `FLAGGED`, `review_attempts` or the old review shape:
- `cleanup_stuck_generations`: requeue `FAILED` topics whose review status is `PENDING` (they failed from a generation outage). Never requeue `REJECTED`. Topics stuck in review or repair past the cutoff are given up as `UNREVIEWED` (published), not failed.
- The retry endpoint service: resets the generation counter, clears the stored verdict, sets `PENDING`, then claims.
- Serializers, views and events that expose `review_status`: new value set; update help texts to list the values.
- The old `review_topic_content` returning a bool goes away; nothing returns a bare bool for review.

## 3.7 Docs

- `docs/reference/ai-providers.md`: replace the review description with the narrow review, the quote rule, the repair stage, the `topic_repair` chain, and the status table from 3.1.
- `docs/backlog/known-bugs.md`: close #3 (reviewer false rejections) with a one-line note on what replaced it.
- `docs/backlog/follow-ups.md`: add "Re-review `UNREVIEWED` topics later" as a follow-up, unbuilt.
- `AGENTS.md`, Generation flows: describe the stages, the statuses, and that content is shown only for `PASSED`, `FIXED` and `UNREVIEWED`.

## 3.8 Tests (all required)

Interpretation (pure)
- A verbatim quote is kept.
- A quote differing only in case, whitespace or curly versus straight quotes is kept.
- A quote not in the summary is dropped and counted.
- Empty quote, problem or correction: dropped.
- Duplicate quotes are kept once.

Decisions (pipeline, AI mocked at the HTTP boundary)
- On topic, no issues → `READY`/`PASSED`; exactly one review call; no repair call.
- On topic, only made-up quotes → `PASSED`; no repair call.
- On topic, one real issue → one repair call → `READY`/`FIXED`; the saved summary is the repaired text; no second review call.
- Repair returns an invalid summary → original kept; `READY`/`UNREVIEWED`.
- Off topic once → regenerated once (counter 1), then a passing review → `PASSED`.
- Off topic twice → `FAILED`/`REJECTED`; no third generation.
- Off topic with issues → regenerate, not repair.

Outages and retries
- Review outage → task retries; on last retry → `READY`/`UNREVIEWED`; the summary is unchanged.
- Repair outage → retries; on last retry → `READY`/`UNREVIEWED`; the review call count stays 1 across retries.
- Redelivered review task on a topic already `READY` → no AI call.
- Generation outage on last retry → `FAILED` with `PENDING`, and the sweeper requeues it; a `REJECTED` topic is not requeued.
- A topic stuck in review past the cutoff → `UNREVIEWED`, published.

State and API
- Retry endpoint on a `REJECTED` topic resets the counter and verdict and starts a new cycle.
- Topic GET, subject GET and the SSE `topic.status` payload carry the new status values.
- Content (summary) is hidden while `PENDING` and for `REJECTED`, shown for `PASSED`, `FIXED` and `UNREVIEWED` (match the existing visibility rule's shape).

Logging
- A full review-and-repair cycle writes log rows for generate, review and repair, each with its own prompt version; the review row's outcome annotation holds the verdict, and the repair row's holds "repaired".

Reviewer prompt
- The prompt contains the goal, level and neighbours, and does not contain resource link URLs.
- The prompt states that omissions and style are not issues.

## 3.9 Done when

- [x] Statuses, counter and stored verdict in place; old fields and values gone
- [x] Reviewer and repair prompts in the learning prompts module; old review prompt and schema removed from `apps/ai`
- [x] Stages and thin tasks; sweeper and retry endpoint updated
- [x] All listed tests exist and pass; full suite passes
- [x] Docs updated; `PROGRESS.md` entry written
- [x] Committed on the branch, not pushed
